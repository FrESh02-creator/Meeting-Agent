from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from typing import Any, Dict, List, Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


# LLM prompt for extraction agent.
EXTRACTION_SYSTEM_PROMPT = """
You are a professional meeting assistant for engine R&D delivery.
Task: extract structured information from noisy meeting transcript.
Target quality: action-item recall >= 0.90, extraction accuracy >= 0.85.

Required extraction:
1) decisions: confirmed agreements, approved plans, and change notices.
2) actions: MUST include task, owner, deadline, risk.
3) risks: hardware delay, technical bottleneck, staffing pressure, schedule conflict.

Domain context:
- Engine intelligent platform
- Experiment assistant system
- TianGong large-model API integration

Output JSON only. No markdown. No extra text.
""".strip()


# LLM prompt for reflection agent.
REFLECTION_SYSTEM_PROMPT = """
You are a senior quality reviewer for meeting extraction.
Review extracted structure against source transcript.

Checklist:
1) Missing action items (especially casual commitments near the end)
2) Owner assignment correctness and ambiguity
3) Deadline reasonableness

Return strict JSON:
{
  "passed": boolean,
  "estimated_accuracy": 0.0-1.0,
  "estimated_action_recall": 0.0-1.0,
  "missing_action_hints": ["..."],
  "ambiguous_owners": ["..."],
  "notes": ["..."]
}
Quality gate:
- estimated_accuracy >= 0.85
- estimated_action_recall >= 0.90
""".strip()


class ValidationState(TypedDict):
    passed: bool
    estimated_accuracy: float
    estimated_action_recall: float
    missing_action_hints: List[str]
    ambiguous_owners: List[str]
    notes: List[str]
    iteration: int


class MeetingState(TypedDict, total=False):
    raw_text: str
    report_type: Literal["management", "project"]
    extraction_prompt: str
    reflection_prompt: str
    title: str
    date: str
    weekly_period: str
    decisions: List[str]
    actions: List[Dict[str, str]]
    risks: List[str]
    validation: ValidationState
    report_markdown: str
    iteration: int
    max_iterations: int


ACTION_HINT_TOKENS = ["action", "todo", "owner", "deadline", "responsible", "follow-up", "need to", "will", "task"]
DECISION_HINT_TOKENS = ["decision", "conclusion", "approve", "confirm", "decide"]
RISK_HINT_TOKENS = ["risk", "block", "delay", "issue"]
AMBIGUOUS_OWNERS = {"tbd", "unknown", "team", "someone", "to be assigned"}


def _get_zhipu_client() -> OpenAI:
    api_key = os.getenv("ZHIPU_API_KEY", "")
    if not api_key:
        raise RuntimeError("未配置 ZHIPU_API_KEY")
    timeout_sec = float(os.getenv("ZHIPU_TIMEOUT_SECONDS", "40"))
    base_url = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    return OpenAI(api_key=api_key, timeout=timeout_sec, base_url=base_url)


def _safe_json_load(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    return json.loads(cleaned)


@retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3), reraise=True)
def _chat_json(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    # Zhipu API call with retry logic for timeout/transient failures.
    model = os.getenv("ZHIPU_MODEL", "glm-5")
    client = _get_zhipu_client()
    logger.info("Calling Zhipu model=%s input_chars=%s", model, len(user_prompt))
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    logger.info("Zhipu response received chars=%s", len(content))
    return _safe_json_load(content)


def _match_lines(raw_text: str, keywords: List[str]) -> List[str]:
    lines = [line.strip(" -:\t") for line in raw_text.splitlines() if line.strip()]
    result: List[str] = []
    for line in lines:
        low = line.lower()
        if any(keyword in low for keyword in keywords):
            result.append(line)
    return result


def _extract_owner(line: str) -> str:
    pattern = r"(owner[: ]+|responsible[: ]+)([A-Za-z0-9_\- ]{1,24})"
    match = re.search(pattern, line, re.IGNORECASE)
    if match:
        return match.group(2).strip()
    return "待定"


def _extract_deadline(line: str) -> str:
    match = re.search(r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", line)
    return match.group(1) if match else "待定"


def _fallback_extraction(state: MeetingState, reason: str) -> Dict[str, Any]:
    logger.warning("触发规则兜底提取: %s", reason)
    raw_text = state.get("raw_text", "").strip()
    today = dt.date.today()
    weekly_start = today - dt.timedelta(days=today.weekday())
    weekly_end = weekly_start + dt.timedelta(days=4)
    iteration = state.get("iteration", 0)

    decisions = _match_lines(raw_text, DECISION_HINT_TOKENS)
    risks = _match_lines(raw_text, RISK_HINT_TOKENS)
    action_candidates = _match_lines(raw_text, ACTION_HINT_TOKENS)

    actions: List[Dict[str, str]] = []
    for line in action_candidates:
        actions.append(
            {
                "task": line[:120],
                "owner": _extract_owner(line),
                "deadline": _extract_deadline(line),
                "risk": "无",
            }
        )

    if iteration > 0 and len(actions) < 2:
        for line in raw_text.splitlines():
            clean = line.strip()
            if len(clean) > 20 and clean not in action_candidates:
                actions.append(
                    {
                        "task": clean[:120],
                        "owner": _extract_owner(clean),
                        "deadline": _extract_deadline(clean),
                        "risk": "none",
                    }
                )
                if len(actions) >= 3:
                    break

    if not decisions:
        decisions = ["待人工确认：未识别到明确的决策项。"]
    if not actions:
        actions = [{"task": "待人工补充行动项", "owner": "待定", "deadline": "待定", "risk": "无"}]
    if not risks:
        risks = ["原始文本中未识别到明确风险项。"]

    return {
        "title": "会议结构化提取结果",
        "date": str(today),
        "weekly_period": f"周期：{weekly_start} 至 {weekly_end}",
        "decisions": decisions[:10],
        "actions": actions[:20],
        "risks": risks[:10],
    }


def _fallback_reflection(state: MeetingState, reason: str) -> Dict[str, Any]:
    logger.warning("触发规则兜底校验: %s", reason)
    raw_text = state.get("raw_text", "")
    actions = state.get("actions", [])
    iteration = state.get("iteration", 0) + 1

    candidate_lines = _match_lines(raw_text, ACTION_HINT_TOKENS)
    candidate_count = max(1, len(candidate_lines))
    recall = min(1.0, len(actions) / candidate_count)

    ambiguous_owners: List[str] = []
    for action in actions:
        owner = str(action.get("owner", "")).strip().lower()
        if not owner or owner in AMBIGUOUS_OWNERS:
            ambiguous_owners.append(action.get("task", "未知任务"))

    owner_penalty = min(0.20, len(ambiguous_owners) * 0.03)
    detail_penalty = 0.08 if any(action.get("deadline", "待定") == "待定" for action in actions) else 0.0
    estimated_accuracy = max(0.0, min(1.0, 0.95 - owner_penalty - detail_penalty))
    passed = recall >= 0.90 and estimated_accuracy >= 0.85

    missing_hints: List[str] = []
    if recall < 0.90:
        missing_hints.extend(candidate_lines[len(actions) : len(actions) + 6])

    notes = [
        f"兜底校验原因：{reason}",
        f"准确率目标 >= 0.85，当前估计值 {estimated_accuracy:.2f}",
        f"召回率目标 >= 0.90，当前估计值 {recall:.2f}",
    ]
    if ambiguous_owners:
        notes.append("存在负责人不明确的行动项，建议在下一轮抽取中补充。")

    return {
        "iteration": iteration,
        "validation": {
            "passed": passed,
            "estimated_accuracy": round(estimated_accuracy, 4),
            "estimated_action_recall": round(recall, 4),
            "missing_action_hints": missing_hints,
            "ambiguous_owners": ambiguous_owners,
            "notes": notes,
            "iteration": iteration,
        },
    }


def _normalize_actions(actions: Any) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    if not isinstance(actions, list):
        return normalized
    for item in actions:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "task": str(item.get("task", "")).strip() or "待人工补充行动项",
                "owner": str(item.get("owner", "待定")).strip() or "待定",
                "deadline": str(item.get("deadline", "待定")).strip() or "待定",
                "risk": str(item.get("risk", "无")).strip() or "无",
            }
        )
    return normalized


def extraction_node(state: MeetingState) -> Dict[str, Any]:
    # LLM extraction node with retry; fallback to heuristic extraction on failure.
    raw_text = state.get("raw_text", "").strip()
    if not raw_text:
        return _fallback_extraction(state, reason="empty_input")

    today = dt.date.today()
    weekly_start = today - dt.timedelta(days=today.weekday())
    weekly_end = weekly_start + dt.timedelta(days=4)
    iteration = state.get("iteration", 0)

    # Keep inputs bounded for robustness on very long transcript.
    raw_text_for_llm = raw_text[:24000]
    hints: List[str] = []
    if iteration > 0:
        prior_validation = state.get("validation", {})
        hints.extend(prior_validation.get("missing_action_hints", []))
        hints.extend(prior_validation.get("ambiguous_owners", []))
        hints = hints[:10]

    extraction_prompt = state.get("extraction_prompt", EXTRACTION_SYSTEM_PROMPT)
    user_prompt = (
        "Extract meeting structure and return JSON with keys: "
        "title,date,weekly_period,decisions,actions,risks.\n"
        "actions must be list of {task, owner, deadline, risk}.\n"
        f"Iteration: {iteration}\n"
        f"Reflection hints: {json.dumps(hints, ensure_ascii=True)}\n"
        f"Source transcript:\n{raw_text_for_llm}"
    )

    try:
        payload = _chat_json(extraction_prompt, user_prompt)
        decisions = payload.get("decisions", [])
        risks = payload.get("risks", [])
        actions = _normalize_actions(payload.get("actions", []))

        if not isinstance(decisions, list):
            decisions = []
        if not isinstance(risks, list):
            risks = []

        decisions = [str(x).strip() for x in decisions if str(x).strip()][:10]
        risks = [str(x).strip() for x in risks if str(x).strip()][:10]

        if not decisions:
            decisions = ["待人工确认：未识别到明确的决策项。"]
        if not actions:
            actions = [{"task": "待人工补充行动项", "owner": "待定", "deadline": "待定", "risk": "无"}]
        if not risks:
            risks = ["原始文本中未识别到明确风险项。"]

        return {
            "title": str(payload.get("title", "会议结构化提取结果")),
            "date": str(payload.get("date", today)),
            "weekly_period": str(payload.get("weekly_period", f"周期：{weekly_start} 至 {weekly_end}")),
            "decisions": decisions,
            "actions": actions[:20],
            "risks": risks,
        }
    except Exception as exc:
        logger.exception("LLM extraction failed at iteration=%s", iteration)
        return _fallback_extraction(state, reason=f"llm_error:{type(exc).__name__}")


def reflection_node(state: MeetingState) -> Dict[str, Any]:
    # LLM reflection node with retry; fallback to heuristic validation on failure.
    raw_text = state.get("raw_text", "").strip()
    extracted_payload = {
        "title": state.get("title", ""),
        "date": state.get("date", ""),
        "weekly_period": state.get("weekly_period", ""),
        "decisions": state.get("decisions", []),
        "actions": state.get("actions", []),
        "risks": state.get("risks", []),
    }
    iteration = state.get("iteration", 0) + 1

    if not raw_text:
        return _fallback_reflection(state, reason="empty_input")

    reflection_prompt = state.get("reflection_prompt", REFLECTION_SYSTEM_PROMPT)
    user_prompt = (
        "Validate extraction quality against source transcript.\n"
        "Return JSON only.\n"
        f"Source transcript:\n{raw_text[:24000]}\n\n"
        f"Extracted JSON:\n{json.dumps(extracted_payload, ensure_ascii=True)}"
    )

    try:
        payload = _chat_json(reflection_prompt, user_prompt)
        estimated_accuracy = float(payload.get("estimated_accuracy", 0.0))
        estimated_recall = float(payload.get("estimated_action_recall", 0.0))
        missing_hints_raw = payload.get("missing_action_hints", [])
        ambiguous_raw = payload.get("ambiguous_owners", [])
        notes_raw = payload.get("notes", [])

        missing_hints = [str(x).strip() for x in missing_hints_raw if str(x).strip()] if isinstance(missing_hints_raw, list) else []
        ambiguous = [str(x).strip() for x in ambiguous_raw if str(x).strip()] if isinstance(ambiguous_raw, list) else []
        notes = [str(x).strip() for x in notes_raw if str(x).strip()] if isinstance(notes_raw, list) else []

        # Keep acceptance threshold check deterministic in code.
        gate_pass = estimated_accuracy >= 0.85 and estimated_recall >= 0.90
        llm_pass = bool(payload.get("passed", False))
        passed = gate_pass and llm_pass

        notes.append(f"阈值检查：规则门禁={gate_pass}，模型判定={llm_pass}")

        return {
            "iteration": iteration,
            "validation": {
                "passed": passed,
                "estimated_accuracy": max(0.0, min(1.0, round(estimated_accuracy, 4))),
                "estimated_action_recall": max(0.0, min(1.0, round(estimated_recall, 4))),
                "missing_action_hints": missing_hints[:10],
                "ambiguous_owners": ambiguous[:10],
                "notes": notes[:12],
                "iteration": iteration,
            },
        }
    except Exception as exc:
        logger.exception("LLM reflection failed at iteration=%s", iteration)
        return _fallback_reflection(state, reason=f"llm_error:{type(exc).__name__}")


def _route_after_reflection(state: MeetingState) -> str:
    validation = state.get("validation", {})
    if validation.get("passed", False):
        return "format_report"
    if state.get("iteration", 0) >= state.get("max_iterations", 2):
        return "format_report"
    return "extraction"


def render_report_markdown(state: MeetingState) -> str:
    report_type = state.get("report_type", "management")
    title = state.get("title", "会议报告")
    date = state.get("date", "")
    weekly_period = state.get("weekly_period", "")
    decisions = state.get("decisions", [])
    actions = state.get("actions", [])
    risks = state.get("risks", [])
    validation = state.get("validation", {})

    if report_type == "project":
        lines = [
            f"# {title}",
            "",
            f"- 日期：{date}",
            f"- {weekly_period}",
            "",
            "## 决策项",
        ]
        lines.extend([f"- {item}" for item in decisions])
        lines.append("")
        lines.append("## 行动时间线")
        lines.extend([f"- {a.get('task')} | 负责人：{a.get('owner')} | 截止日期：{a.get('deadline')}" for a in actions])
        lines.append("")
        lines.append("## 风险项")
        lines.extend([f"- {item}" for item in risks])
    else:
        lines = [
            "# 管理版周报",
            "",
            f"**来源会议：** {title}",
            f"**日期：** {date}",
            f"**{weekly_period}**",
            "",
            "## 关键决策",
        ]
        lines.extend([f"- {item}" for item in decisions])
        lines.append("")
        lines.append("## 行动项与进展")
        lines.extend([f"- {a.get('task')}（负责人：{a.get('owner')}，截止日期：{a.get('deadline')}）" for a in actions])
        lines.append("")
        lines.append("## 风险与预警")
        lines.extend([f"- {item}" for item in risks])

    if validation:
        lines.extend(
            [
                "",
                "## 质量检查",
                f"- 预估准确率：{validation.get('estimated_accuracy', 0)}",
                f"- 预估行动项召回率：{validation.get('estimated_action_recall', 0)}",
                f"- 是否通过：{validation.get('passed', False)}",
            ]
        )

    return "\n".join(lines)


def format_node(state: MeetingState) -> Dict[str, Any]:
    return {"report_markdown": render_report_markdown(state)}


workflow = StateGraph(MeetingState)
workflow.add_node("extraction", extraction_node)
workflow.add_node("reflection", reflection_node)
workflow.add_node("format_report", format_node)
workflow.add_edge(START, "extraction")
workflow.add_edge("extraction", "reflection")
workflow.add_conditional_edges(
    "reflection",
    _route_after_reflection,
    {
        "extraction": "extraction",
        "format_report": "format_report",
    },
)
workflow.add_edge("format_report", END)
meeting_workflow = workflow.compile()
