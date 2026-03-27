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


EXTRACTION_SYSTEM_PROMPT = """
\u4f60\u662f\u4e00\u540d\u4e13\u4e1a\u7684\u4f1a\u8bae\u7eaa\u8981\u4e0e\u9879\u76ee\u534f\u540c\u52a9\u624b\uff0c\u670d\u52a1\u4e8e\u201c\u53d1\u52a8\u673a\u667a\u80fd\u5e73\u53f0\u201d\u201c\u8bd5\u9a8c\u52a9\u7406\u7cfb\u7edf\u201d\u201c\u5929\u5de5\u5927\u6a21\u578b API \u96c6\u6210\u201d\u7b49\u7814\u53d1\u573a\u666f\u3002

\u4f60\u7684\u4efb\u52a1\uff1a
\u4ece\u539f\u59cb\u4f1a\u8bae\u6587\u672c\u6216\u5e26\u6709 [Speaker N]: ... \u7684\u8f6c\u5199\u6587\u672c\u4e2d\uff0c\u62bd\u53d6\u7ed3\u6784\u5316\u4f1a\u8bae\u7eaa\u8981\u3002

\u8f93\u51fa\u8981\u6c42\uff1a
1. \u5fc5\u987b\u8fd4\u56de\u4e25\u683c JSON\uff0c\u4e0d\u8981\u8f93\u51fa Markdown\uff0c\u4e0d\u8981\u8f93\u51fa\u89e3\u91ca\u3002
2. JSON \u952e\u56fa\u5b9a\u4e3a\uff1atitle\u3001date\u3001weekly_period\u3001decisions\u3001actions\u3001risks\u3002
3. actions \u4e2d\u6bcf\u4e00\u9879\u90fd\u5fc5\u987b\u5305\u542b\uff1atask\u3001owner\u3001deadline\u3001risk\u3002
4. \u65e0\u8bba\u539f\u59cb\u6587\u672c\u662f\u4e2d\u6587\u8fd8\u662f\u82f1\u6587\uff0c\u6700\u7ec8\u8f93\u51fa\u5185\u5bb9\u90fd\u5fc5\u987b\u4f7f\u7528\u7b80\u4f53\u4e2d\u6587\u8868\u8fbe\u3002
5. \u4e13\u6709\u540d\u8bcd\u53ef\u4fdd\u7559\u82f1\u6587\u6216\u539f\u8bcd\uff0c\u4f46\u6574\u4f53\u53e5\u5b50\u5fc5\u987b\u662f\u4e2d\u6587\u3002
6. \u5982\u679c\u8d1f\u8d23\u4eba\u65e0\u6cd5\u5224\u65ad\uff0c\u586b\u201c\u5f85\u5b9a\u201d\uff1b\u5982\u679c\u622a\u6b62\u65f6\u95f4\u65e0\u6cd5\u5224\u65ad\uff0c\u586b\u201c\u5f85\u5b9a\u201d\uff1b\u5982\u679c\u65e0\u98ce\u9669\uff0c\u586b\u201c\u65e0\u201d\u3002
7. \u4e0d\u8981\u628a [Speaker N] \u8fd9\u79cd\u6807\u7b7e\u6294\u8fdb task \u6587\u672c\u4e2d\uff0c\u53ea\u628a\u5b83\u5f53\u4f5c\u53d1\u8a00\u7ebf\u7d22\u3002
8. decisions \u63d0\u53d6\u4f1a\u8bae\u5df2\u786e\u8ba4\u7684\u7ed3\u8bba\u3001\u65b9\u6848\u3001\u51b3\u8bae\u3002
9. actions \u63d0\u53d6\u5fc5\u987b\u6267\u884c\u7684\u4e8b\u9879\u3001\u8d23\u4efb\u4eba\u3001\u65f6\u95f4\u70b9\u3002
10. risks \u63d0\u53d6\u98ce\u9669\u3001\u963b\u585e\u3001\u5ef6\u671f\u3001\u8d44\u6e90\u4e0d\u8db3\u3001\u6280\u672f\u74f6\u9888\u7b49\u3002

\u8d28\u91cf\u8981\u6c42\uff1a
- \u884c\u52a8\u9879\u5c3d\u91cf\u5168\uff0c\u4e0d\u8981\u6f0f\u6389\u4f1a\u8bae\u4e2d\u7684\u968f\u53e3\u5b89\u6392\u3001\u627f\u8bfa\u548c\u5f85\u529e\u3002
- \u8868\u8fbe\u8981\u7b80\u6d01\uff0c\u9002\u5408\u76f4\u63a5\u7528\u4e8e\u4f1a\u8bae\u7eaa\u8981\u5c55\u793a\u548c\u6587\u4ef6\u5bfc\u51fa\u3002
""".strip()


REFLECTION_SYSTEM_PROMPT = """
\u4f60\u662f\u4e00\u540d\u8d44\u6df1\u4f1a\u8bae\u7eaa\u8981\u8d28\u68c0\u4e13\u5bb6\u3002
\u8bf7\u68c0\u67e5\u201c\u7ed3\u6784\u5316\u63d0\u53d6\u7ed3\u679c\u201d\u662f\u5426\u51c6\u786e\u3001\u5b8c\u6574\uff0c\u5c24\u5176\u5173\u6ce8\u884c\u52a8\u9879\u53ec\u56de\u7387\u548c\u8d1f\u8d23\u4eba\u3001\u65f6\u95f4\u4fe1\u606f\u662f\u5426\u660e\u786e\u3002

\u68c0\u67e5\u91cd\u70b9\uff1a
1. \u662f\u5426\u9057\u6f0f\u884c\u52a8\u9879\uff0c\u7279\u522b\u662f\u4f1a\u8bae\u672b\u5c3e\u7684\u4e34\u65f6\u5b89\u6392\u3001\u627f\u8bfa\u548c follow-up\u3002
2. \u8d1f\u8d23\u4eba\u662f\u5426\u660e\u786e\uff0c\u662f\u5426\u5b58\u5728\u201c\u67d0\u4eba/\u56e2\u961f/\u5f85\u5b9a/unknown\u201d\u8fd9\u7c7b\u6a21\u7cca\u5f52\u5c5e\u3002
3. \u622a\u6b62\u65e5\u671f\u662f\u5426\u7f3a\u5931\u6216\u660e\u663e\u4e0d\u5408\u7406\u3002
4. \u5982\u679c\u6e90\u6587\u672c\u4e2d\u51fa\u73b0 [Speaker N]: ...\uff0c\u8bf7\u6309\u8bed\u4e49\u7406\u89e3\u5185\u5bb9\uff0c\u4e0d\u8981\u628a Speaker \u6807\u7b7e\u5f53\u6210\u6b63\u6587\u3002

\u8fd4\u56de\u4e25\u683c JSON\uff0c\u952e\u56fa\u5b9a\u4e3a\uff1a
{
  "passed": boolean,
  "estimated_accuracy": 0.0-1.0,
  "estimated_action_recall": 0.0-1.0,
  "missing_action_hints": ["..."],
  "ambiguous_owners": ["..."],
  "notes": ["..."]
}

\u9644\u52a0\u8981\u6c42\uff1a
- notes \u5fc5\u987b\u4f7f\u7528\u7b80\u4f53\u4e2d\u6587\u3002
- missing_action_hints \u548c ambiguous_owners \u5fc5\u987b\u4f7f\u7528\u7b80\u4f53\u4e2d\u6587\u3002
- \u53ea\u6709\u5728 estimated_accuracy >= 0.85 \u4e14 estimated_action_recall >= 0.90 \u65f6\uff0cpassed \u624d\u80fd\u4e3a true\u3002
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


ACTION_HINT_TOKENS = [
    "\u884c\u52a8",
    "\u5f85\u529e",
    "\u8d1f\u8d23",
    "\u622a\u6b62",
    "\u8ddf\u8fdb",
    "\u63a8\u8fdb",
    "\u5b8c\u6210",
    "\u5b89\u6392",
    "\u843d\u5b9e",
    "\u9700\u8981",
    "action",
    "todo",
    "owner",
    "deadline",
    "responsible",
    "follow-up",
    "need to",
    "will",
    "task",
]
DECISION_HINT_TOKENS = [
    "\u51b3\u7b56",
    "\u7ed3\u8bba",
    "\u786e\u5b9a",
    "\u901a\u8fc7",
    "\u6279\u51c6",
    "\u51b3\u5b9a",
    "\u786e\u8ba4",
    "decision",
    "approve",
    "confirm",
]
RISK_HINT_TOKENS = [
    "\u98ce\u9669",
    "\u963b\u585e",
    "\u5ef6\u671f",
    "\u95ee\u9898",
    "\u74f6\u9888",
    "\u8d44\u6e90\u4e0d\u8db3",
    "\u51b2\u7a81",
    "risk",
    "delay",
    "issue",
    "block",
]
AMBIGUOUS_OWNERS = {
    "tbd",
    "unknown",
    "team",
    "someone",
    "to be assigned",
    "\u5f85\u5b9a",
    "\u672a\u77e5",
    "\u56e2\u961f",
    "\u76f8\u5173\u4eba\u5458",
}

PLACEHOLDER_TRANSLATIONS = {
    "tbd": "\u5f85\u5b9a",
    "unknown": "\u5f85\u5b9a",
    "to be determined": "\u5f85\u5b9a",
    "to be assigned": "\u5f85\u5b9a",
    "not specified": "\u5f85\u5b9a",
    "unspecified": "\u5f85\u5b9a",
    "n/a": "\u5f85\u5b9a",
    "na": "\u5f85\u5b9a",
    "none": "\u65e0",
    "no risk": "\u65e0",
    "no": "\u65e0",
    "participants": "\u53c2\u4f1a\u4eba\u5458",
    "all participants": "\u5168\u4f53\u53c2\u4f1a\u4eba\u5458",
    "participant": "\u53c2\u4f1a\u4eba\u5458",
    "team": "\u56e2\u961f",
    "owner unknown": "\u5f85\u5b9a",
    "immediately after meeting": "\u4f1a\u540e\u7acb\u5373",
    "after meeting": "\u4f1a\u540e",
    "today": "\u4eca\u5929",
    "tomorrow": "\u660e\u5929",
    "this week": "\u672c\u5468",
    "next week": "\u4e0b\u5468",
}


def _get_zhipu_client() -> OpenAI:
    api_key = os.getenv("ZHIPU_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("\u7f3a\u5c11 ZHIPU_API_KEY \u914d\u7f6e")
    timeout_sec = float(os.getenv("ZHIPU_TIMEOUT_SECONDS", "40"))
    base_url = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    return OpenAI(api_key=api_key, timeout=timeout_sec, base_url=base_url)


def _safe_json_load(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    return json.loads(cleaned or "{}")


@retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3), reraise=True)
def _chat_json(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
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


def _build_weekly_period(target_date: dt.date | None = None) -> str:
    current = target_date or dt.date.today()
    weekly_start = current - dt.timedelta(days=current.weekday())
    weekly_end = weekly_start + dt.timedelta(days=4)
    return f"\u5468\u62a5\u5468\u671f\uff1a{weekly_start} \u81f3 {weekly_end}"


def _normalize_common_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    lowered = text.lower()
    if lowered in PLACEHOLDER_TRANSLATIONS:
        return PLACEHOLDER_TRANSLATIONS[lowered]
    return text


def _normalize_owner(value: Any) -> str:
    normalized = _normalize_common_text(value, "\u5f85\u5b9a")
    return normalized or "\u5f85\u5b9a"


def _normalize_deadline(value: Any) -> str:
    normalized = _normalize_common_text(value, "\u5f85\u5b9a")
    return normalized or "\u5f85\u5b9a"


def _normalize_risk(value: Any) -> str:
    normalized = _normalize_common_text(value, "\u65e0")
    return normalized or "\u65e0"


def _normalize_decision_item(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("decision", "summary", "content", "text"):
            if item.get(key):
                return _normalize_common_text(item.get(key), "\u5f85\u8865\u5145\u51b3\u7b56\u9879")
        return "\u5f85\u8865\u5145\u51b3\u7b56\u9879"
    return _normalize_common_text(item, "\u5f85\u8865\u5145\u51b3\u7b56\u9879")


def _normalize_risk_item(item: Any) -> str:
    if isinstance(item, dict):
        risk_type = _normalize_common_text(item.get("risk_type"), "\u98ce\u9669")
        description = _normalize_common_text(item.get("description"), "\u5f85\u8865\u5145\u98ce\u9669\u8bf4\u660e")
        return f"{risk_type}\uff1a{description}"
    return _normalize_risk(item)


def _match_lines(raw_text: str, keywords: List[str]) -> List[str]:
    lines = [line.strip(" -:\t") for line in raw_text.splitlines() if line.strip()]
    result: List[str] = []
    for line in lines:
        lowered = line.lower()
        if any(keyword.lower() in lowered for keyword in keywords):
            result.append(line)
    return result


def _extract_owner(line: str) -> str:
    speaker_match = re.match(r"\[(.+?)\]:", line.strip())
    if speaker_match:
        return _normalize_owner(speaker_match.group(1))

    owner_patterns = [
        r"\u8d1f\u8d23\u4eba[\uff1a: ]*([\u4e00-\u9fa5A-Za-z0-9_\-]{1,24})",
        r"\u7531([\u4e00-\u9fa5A-Za-z0-9_\-]{1,24})\u8d1f\u8d23",
        r"([\u4e00-\u9fa5A-Za-z0-9_\-]{1,24})\u8d1f\u8d23",
        r"owner[: ]+([A-Za-z0-9_\- ]{1,24})",
        r"responsible[: ]+([A-Za-z0-9_\- ]{1,24})",
    ]
    for pattern in owner_patterns:
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            return _normalize_owner(match.group(1))
    return "\u5f85\u5b9a"


def _extract_deadline(line: str) -> str:
    date_match = re.search(r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", line)
    if date_match:
        return date_match.group(1).replace("/", "-")

    keyword_map = {
        "\u4eca\u5929": "\u4eca\u5929",
        "\u660e\u5929": "\u660e\u5929",
        "\u672c\u5468": "\u672c\u5468",
        "\u4e0b\u5468": "\u4e0b\u5468",
        "today": "\u4eca\u5929",
        "tomorrow": "\u660e\u5929",
        "this week": "\u672c\u5468",
        "next week": "\u4e0b\u5468",
        "immediately": "\u4f1a\u540e\u7acb\u5373",
    }
    lowered = line.lower()
    for key, value in keyword_map.items():
        if key.lower() in lowered:
            return value
    return "\u5f85\u5b9a"


def _normalize_actions(actions: Any) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    if not isinstance(actions, list):
        return normalized

    for item in actions:
        if not isinstance(item, dict):
            continue
        task = _normalize_common_text(item.get("task"), "\u5f85\u8865\u5145\u884c\u52a8\u9879")
        normalized.append(
            {
                "task": task or "\u5f85\u8865\u5145\u884c\u52a8\u9879",
                "owner": _normalize_owner(item.get("owner")),
                "deadline": _normalize_deadline(item.get("deadline")),
                "risk": _normalize_risk(item.get("risk")),
            }
        )
    return normalized


def _fallback_extraction(state: MeetingState, reason: str) -> Dict[str, Any]:
    logger.warning("\u7ed3\u6784\u5316\u63d0\u53d6\u56de\u9000\u5230\u89c4\u5219\u903b\u8f91: %s", reason)
    raw_text = state.get("raw_text", "").strip()
    today = dt.date.today()
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
                "risk": "\u65e0",
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
                        "risk": "\u65e0",
                    }
                )
                if len(actions) >= 3:
                    break

    if not decisions:
        decisions = ["\u672c\u6b21\u4f1a\u8bae\u6682\u65e0\u53ef\u660e\u786e\u63d0\u53d6\u7684\u51b3\u7b56\u9879\uff0c\u8bf7\u4eba\u5de5\u8865\u5145\u786e\u8ba4\u3002"]
    if not actions:
        actions = [{"task": "\u8bf7\u4eba\u5de5\u8865\u5145\u672c\u6b21\u4f1a\u8bae\u884c\u52a8\u9879", "owner": "\u5f85\u5b9a", "deadline": "\u5f85\u5b9a", "risk": "\u65e0"}]
    if not risks:
        risks = ["\u672c\u6b21\u4f1a\u8bae\u672a\u8bc6\u522b\u5230\u660e\u786e\u98ce\u9669\u9879\uff0c\u5982\u6709\u9690\u60a3\u8bf7\u4eba\u5de5\u8865\u5145\u3002"]

    return {
        "title": "\u4f1a\u8bae\u7eaa\u8981\u7ed3\u6784\u5316\u7ed3\u679c",
        "date": str(today),
        "weekly_period": _build_weekly_period(today),
        "decisions": decisions[:10],
        "actions": actions[:20],
        "risks": risks[:10],
    }


def _fallback_reflection(state: MeetingState, reason: str) -> Dict[str, Any]:
    logger.warning("\u8d28\u68c0\u6821\u9a8c\u56de\u9000\u5230\u89c4\u5219\u903b\u8f91: %s", reason)
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
            ambiguous_owners.append(_normalize_common_text(action.get("task"), "\u672a\u547d\u540d\u884c\u52a8\u9879"))

    owner_penalty = min(0.20, len(ambiguous_owners) * 0.03)
    deadline_penalty = 0.08 if any(str(action.get("deadline", "\u5f85\u5b9a")).strip() == "\u5f85\u5b9a" for action in actions) else 0.0
    estimated_accuracy = max(0.0, min(1.0, 0.95 - owner_penalty - deadline_penalty))
    passed = recall >= 0.90 and estimated_accuracy >= 0.85

    missing_hints: List[str] = []
    if recall < 0.90:
        missing_hints.extend(candidate_lines[len(actions) : len(actions) + 6])

    notes = [
        f"\u6821\u9a8c\u91c7\u7528\u89c4\u5219\u515c\u5e95\uff0c\u539f\u56e0\uff1a{reason}",
        f"\u4f30\u8ba1\u51c6\u786e\u7387\uff1a{estimated_accuracy:.2f}\uff0c\u76ee\u6807\u9608\u503c\uff1a0.85",
        f"\u4f30\u8ba1\u884c\u52a8\u9879\u53ec\u56de\u7387\uff1a{recall:.2f}\uff0c\u76ee\u6807\u9608\u503c\uff1a0.90",
    ]
    if ambiguous_owners:
        notes.append("\u5b58\u5728\u8d1f\u8d23\u4eba\u4e0d\u660e\u786e\u7684\u884c\u52a8\u9879\uff0c\u5efa\u8bae\u4eba\u5de5\u8865\u5145\u8d23\u4efb\u4eba\u3002")

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


def extraction_node(state: MeetingState) -> Dict[str, Any]:
    raw_text = state.get("raw_text", "").strip()
    if not raw_text:
        return _fallback_extraction(state, reason="empty_input")

    today = dt.date.today()
    iteration = state.get("iteration", 0)
    raw_text_for_llm = raw_text[:24000]

    hints: List[str] = []
    if iteration > 0:
        prior_validation = state.get("validation", {})
        hints.extend(prior_validation.get("missing_action_hints", []))
        hints.extend(prior_validation.get("ambiguous_owners", []))
        hints = hints[:10]

    extraction_prompt = state.get("extraction_prompt", EXTRACTION_SYSTEM_PROMPT)
    user_prompt = (
        "\u8bf7\u62bd\u53d6\u4f1a\u8bae\u7ed3\u6784\u5316\u4fe1\u606f\uff0c\u5e76\u4e25\u683c\u8fd4\u56de JSON\u3002\n"
        "JSON \u952e\u56fa\u5b9a\u4e3a\uff1atitle\u3001date\u3001weekly_period\u3001decisions\u3001actions\u3001risks\u3002\n"
        "\u5176\u4e2d actions \u5fc5\u987b\u662f {task, owner, deadline, risk} \u7684\u6570\u7ec4\u3002\n"
        f"\u5f53\u524d\u8fed\u4ee3\u8f6e\u6b21\uff1a{iteration}\n"
        f"\u4e0a\u4e00\u8f6e\u8d28\u68c0\u63d0\u793a\uff1a{json.dumps(hints, ensure_ascii=False)}\n"
        "\u8bf7\u6ce8\u610f\uff1a\u65e0\u8bba\u539f\u6587\u662f\u4ec0\u4e48\u8bed\u8a00\uff0c\u6700\u7ec8\u8f93\u51fa\u5fc5\u987b\u4e3a\u7b80\u4f53\u4e2d\u6587\u3002\n"
        f"\u539f\u59cb\u4f1a\u8bae\u5185\u5bb9\u5982\u4e0b\uff1a\n{raw_text_for_llm}"
    )

    try:
        payload = _chat_json(extraction_prompt, user_prompt)
        decisions_raw = payload.get("decisions", [])
        risks_raw = payload.get("risks", [])
        actions = _normalize_actions(payload.get("actions", []))

        decisions: List[str] = []
        if isinstance(decisions_raw, list):
            decisions = [_normalize_decision_item(item) for item in decisions_raw][:10]

        risks: List[str] = []
        if isinstance(risks_raw, list):
            risks = [_normalize_risk_item(item) for item in risks_raw][:10]

        if not decisions:
            decisions = ["\u672c\u6b21\u4f1a\u8bae\u6682\u65e0\u53ef\u660e\u786e\u63d0\u53d6\u7684\u51b3\u7b56\u9879\uff0c\u8bf7\u4eba\u5de5\u8865\u5145\u786e\u8ba4\u3002"]
        if not actions:
            actions = [{"task": "\u8bf7\u4eba\u5de5\u8865\u5145\u672c\u6b21\u4f1a\u8bae\u884c\u52a8\u9879", "owner": "\u5f85\u5b9a", "deadline": "\u5f85\u5b9a", "risk": "\u65e0"}]
        if not risks:
            risks = ["\u672c\u6b21\u4f1a\u8bae\u672a\u8bc6\u522b\u5230\u660e\u786e\u98ce\u9669\u9879\uff0c\u5982\u6709\u9690\u60a3\u8bf7\u4eba\u5de5\u8865\u5145\u3002"]

        title = _normalize_common_text(payload.get("title"), "\u4f1a\u8bae\u7eaa\u8981\u7ed3\u6784\u5316\u7ed3\u679c")
        date_text = _normalize_common_text(payload.get("date"), str(today))
        weekly_period = _normalize_common_text(payload.get("weekly_period"), _build_weekly_period(today))

        return {
            "title": title or "\u4f1a\u8bae\u7eaa\u8981\u7ed3\u6784\u5316\u7ed3\u679c",
            "date": date_text or str(today),
            "weekly_period": weekly_period or _build_weekly_period(today),
            "decisions": decisions,
            "actions": actions[:20],
            "risks": risks,
        }
    except Exception as exc:
        logger.exception("LLM extraction failed at iteration=%s", iteration)
        return _fallback_extraction(state, reason=f"llm_error:{type(exc).__name__}")


def reflection_node(state: MeetingState) -> Dict[str, Any]:
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
        "\u8bf7\u6821\u9a8c\u7ed3\u6784\u5316\u63d0\u53d6\u8d28\u91cf\uff0c\u5e76\u4e25\u683c\u8fd4\u56de JSON\u3002\n"
        "\u8bf7\u91cd\u70b9\u5173\u6ce8\u9057\u6f0f\u884c\u52a8\u9879\u3001\u8d1f\u8d23\u4eba\u662f\u5426\u660e\u786e\u3001\u622a\u6b62\u65e5\u671f\u662f\u5426\u5b8c\u6574\u3002\n"
        "notes\u3001missing_action_hints\u3001ambiguous_owners \u90fd\u5fc5\u987b\u8f93\u51fa\u4e2d\u6587\u3002\n"
        f"\u539f\u59cb\u4f1a\u8bae\u5185\u5bb9\uff1a\n{raw_text[:24000]}\n\n"
        f"\u7ed3\u6784\u5316\u7ed3\u679c\uff1a\n{json.dumps(extracted_payload, ensure_ascii=False)}"
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

        gate_pass = estimated_accuracy >= 0.85 and estimated_recall >= 0.90
        llm_pass = bool(payload.get("passed", False))
        passed = gate_pass and llm_pass

        notes.append(f"\u4ee3\u7801\u9608\u503c\u5224\u5b9a\uff1a{gate_pass}\uff1b\u6a21\u578b\u5224\u5b9a\uff1a{llm_pass}")

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
    title = _normalize_common_text(state.get("title"), "\u4f1a\u8bae\u7eaa\u8981")
    date_text = _normalize_common_text(state.get("date"), "\u5f85\u5b9a")
    weekly_period = _normalize_common_text(state.get("weekly_period"), _build_weekly_period())
    decisions = [_normalize_decision_item(item) for item in state.get("decisions", [])]
    actions = _normalize_actions(state.get("actions", []))
    risks = [_normalize_risk_item(item) for item in state.get("risks", [])]
    validation = state.get("validation", {})

    if report_type == "project":
        lines = [
            f"# {title}",
            "",
            f"- \u65e5\u671f\uff1a{date_text}",
            f"- {weekly_period}",
            "",
            "## \u51b3\u7b56\u9879",
        ]
        lines.extend([f"- {item}" for item in decisions] or ["- \u6682\u65e0"])
        lines.append("")
        lines.append("## \u884c\u52a8\u9879")
        if actions:
            lines.extend(
                [
                    f"- {a.get('task')} | \u8d1f\u8d23\u4eba\uff1a{a.get('owner')} | \u622a\u6b62\u65e5\u671f\uff1a{a.get('deadline')} | \u98ce\u9669\uff1a{a.get('risk')}"
                    for a in actions
                ]
            )
        else:
            lines.append("- \u6682\u65e0")
        lines.append("")
        lines.append("## \u98ce\u9669\u9879")
        lines.extend([f"- {item}" for item in risks] or ["- \u6682\u65e0"])
    else:
        lines = [
            "# \u9879\u76ee\u7ba1\u7406\u5468\u62a5",
            "",
            f"**\u4f1a\u8bae\u4e3b\u9898\uff1a** {title}",
            f"**\u65e5\u671f\uff1a** {date_text}",
            f"**{weekly_period}**",
            "",
            "## \u6838\u5fc3\u7ed3\u8bba\u4e0e\u51b3\u7b56",
        ]
        lines.extend([f"- {item}" for item in decisions] or ["- \u6682\u65e0"])
        lines.append("")
        lines.append("## \u5f85\u529e\u4efb\u52a1\u4e0e\u8fdb\u5ea6")
        if actions:
            lines.extend(
                [
                    f"- {a.get('task')}\uff1b\u8d1f\u8d23\u4eba\uff1a{a.get('owner')}\uff1b\u622a\u6b62\u65e5\u671f\uff1a{a.get('deadline')}\uff1b\u98ce\u9669\uff1a{a.get('risk')}"
                    for a in actions
                ]
            )
        else:
            lines.append("- \u6682\u65e0")
        lines.append("")
        lines.append("## \u6838\u5fc3\u98ce\u9669\u4e0e\u9884\u8b66")
        lines.extend([f"- {item}" for item in risks] or ["- \u6682\u65e0"])

    if validation:
        lines.extend(
            [
                "",
                "## \u8d28\u91cf\u6821\u9a8c",
                f"- \u4f30\u8ba1\u51c6\u786e\u7387\uff1a{validation.get('estimated_accuracy', 0)}",
                f"- \u4f30\u8ba1\u884c\u52a8\u9879\u53ec\u56de\u7387\uff1a{validation.get('estimated_action_recall', 0)}",
                f"- \u662f\u5426\u901a\u8fc7\uff1a{'\u662f' if validation.get('passed', False) else '\u5426'}",
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
