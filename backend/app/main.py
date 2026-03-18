from __future__ import annotations

import io
import json
import logging
from datetime import date as current_date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .asr import ASRServiceError, ASRTimeoutError, transcribe_audio_with_speakers
from .graph import meeting_workflow, render_report_markdown
from .history_store import (
    clear_meeting_history,
    delete_meeting_history,
    get_meeting_history_record,
    init_history_db,
    list_meeting_history,
    save_meeting_history,
    update_audio_transcript_history,
)
from .schemas import (
    ActionItem,
    AnalyzeResult,
    AnalyzeTextRequest,
    AudioTranscriptDownloadRequest,
    AudioTranscriptResponse,
    MeetingHistoryItem,
    WeeklyReportRequest,
    WeeklyReportResponse,
)

load_dotenv()

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(title="Meeting Agent API", version="0.9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "mp4", "aac", "ogg", "webm"}
ENTRY_TYPE_MEETING_ANALYSIS = "meeting_analysis"
ENTRY_TYPE_AUDIO_TRANSCRIPT = "audio_transcript"
MEETING_DOWNLOAD_OPTIONS = {
    "management-markdown",
    "management-docx",
    "project-markdown",
    "project-docx",
}
AUDIO_DOWNLOAD_OPTIONS = {"transcript-markdown", "transcript-docx"}


@app.on_event("startup")
def startup() -> None:
    init_history_db()


def _safe_title(value: str | None, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _build_source_title(filename: str | None, fallback: str) -> str:
    raw_name = Path(filename or "").stem.strip()
    return raw_name or fallback


def _build_download_headers(filename: str) -> dict[str, str]:
    ascii_filename = "".join(ch if ch.isascii() and ch not in '\\/:*?"<>|' else "_" for ch in filename)
    ascii_filename = ascii_filename.strip(" ._") or "report"
    return {
        "Content-Disposition": f"attachment; filename={ascii_filename}; filename*=UTF-8''{quote(filename)}",
    }


def _build_report_state(request: WeeklyReportRequest) -> dict[str, Any]:
    return {
        "raw_text": "",
        "title": request.title,
        "date": request.date or "",
        "weekly_period": request.weekly_period,
        "decisions": [item for item in request.decisions],
        "actions": [item.model_dump() for item in request.actions],
        "risks": [item for item in request.risks],
        "report_type": request.report_type,
        "validation": {
            "passed": True,
            "estimated_accuracy": 1.0,
            "estimated_action_recall": 1.0,
            "missing_action_hints": [],
            "ambiguous_owners": [],
            "notes": [],
            "iteration": 0,
        },
    }


def _render_transcript_markdown(title: str, transcript_text: str) -> str:
    return f"# {title}\n\n## 转写全文\n\n{transcript_text.strip()}\n"


def _build_transcript_docx_bytes(request: AudioTranscriptDownloadRequest) -> io.BytesIO:
    document = Document()
    document.add_heading(request.title, level=1)
    document.add_heading("转写全文", level=2)

    paragraphs = [item.strip() for item in request.transcript_text.splitlines() if item.strip()]
    if paragraphs:
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
    else:
        document.add_paragraph(request.transcript_text.strip())

    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


def _build_weekly_docx_bytes(request: WeeklyReportRequest) -> io.BytesIO:
    document = Document()
    document_title = "项目管理周报" if request.report_type == "management" else _safe_title(request.title, "项目会议纪要")
    document.add_heading(document_title, level=1)

    if request.date:
        document.add_paragraph(f"日期：{request.date}")
    if request.weekly_period:
        document.add_paragraph(request.weekly_period)

    document.add_heading("决策项", level=2)
    if request.decisions:
        for decision in request.decisions:
            document.add_paragraph(decision, style="List Bullet")
    else:
        document.add_paragraph("暂无")

    document.add_heading("行动项", level=2)
    if request.actions:
        for action in request.actions:
            document.add_paragraph(
                f"{action.task} | 负责人：{action.owner} | 截止日期：{action.deadline}",
                style="List Bullet",
            )
    else:
        document.add_paragraph("暂无")

    document.add_heading("风险项", level=2)
    if request.risks:
        for risk in request.risks:
            document.add_paragraph(risk, style="List Bullet")
    else:
        document.add_paragraph("暂无")

    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


def _build_weekly_request_from_payload(
    payload: dict[str, Any], report_type: str, title_override: str | None = None
) -> WeeklyReportRequest:
    actions = [ActionItem(**action) for action in payload.get("actions", [])]
    return WeeklyReportRequest(
        title=_safe_title(title_override or payload.get("title"), "会议纪要"),
        date=str(payload.get("date", "") or ""),
        weekly_period=str(payload.get("weekly_period", "") or ""),
        decisions=[str(item) for item in payload.get("decisions", [])],
        actions=actions,
        risks=[str(item) for item in payload.get("risks", [])],
        report_type=report_type,
    )


def _load_structured_data(record: dict[str, Any]) -> dict[str, Any]:
    raw_value = record.get("structured_data") or "{}"
    try:
        payload = json.loads(raw_value)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        logger.warning("Failed to decode structured_data for history id=%s", record.get("id"))
        return {}


def _save_meeting_analysis_history(payload: dict[str, Any], report_type: str, title_override: str | None = None) -> int:
    display_title = _safe_title(title_override or payload.get("title"), "会议纪要")
    weekly_request = _build_weekly_request_from_payload(payload, report_type, title_override=display_title)
    markdown_content = render_report_markdown(_build_report_state(weekly_request))
    docx_content = _build_weekly_docx_bytes(weekly_request).getvalue()
    structured_data = json.dumps(
        {
            "display_title": display_title,
            "generated_title": payload.get("title", ""),
            "date": weekly_request.date,
            "weekly_period": weekly_request.weekly_period,
            "decisions": payload.get("decisions", []),
            "actions": payload.get("actions", []),
            "risks": payload.get("risks", []),
            "validation": payload.get("validation", {}),
        },
        ensure_ascii=False,
    )
    history_id = save_meeting_history(
        title=display_title,
        date=weekly_request.date,
        entry_type=ENTRY_TYPE_MEETING_ANALYSIS,
        report_type=report_type,
        structured_data=structured_data,
        markdown_content=markdown_content,
        docx_content=docx_content,
    )
    logger.info(
        "Saved meeting analysis history: id=%s title=%s report_type=%s",
        history_id,
        display_title,
        report_type,
    )
    return history_id


def _save_audio_transcript_history(title: str, transcript_text: str, analysis_payload: dict[str, Any] | None = None) -> int:
    display_title = _safe_title(title, "会议语音转写")
    transcript_request = AudioTranscriptDownloadRequest(title=display_title, transcript_text=transcript_text)
    markdown_content = _render_transcript_markdown(display_title, transcript_text)
    docx_content = _build_transcript_docx_bytes(transcript_request).getvalue()
    analysis_payload = analysis_payload or {}
    structured_data = json.dumps(
        {
            "display_title": display_title,
            "date": str(analysis_payload.get("date") or current_date.today()),
            "transcript_text": transcript_text,
            "analysis": analysis_payload or None,
        },
        ensure_ascii=False,
    )
    history_id = save_meeting_history(
        title=display_title,
        date=str(analysis_payload.get("date") or current_date.today()),
        entry_type=ENTRY_TYPE_AUDIO_TRANSCRIPT,
        report_type=None,
        structured_data=structured_data,
        markdown_content=markdown_content,
        docx_content=docx_content,
    )
    logger.info("Saved audio transcript history: id=%s title=%s", history_id, display_title)
    return history_id


def _update_audio_transcript_history_entry(history_id: int, title: str, transcript_text: str) -> bool:
    record = get_meeting_history_record(history_id)
    if not record or str(record.get("entry_type") or "") != ENTRY_TYPE_AUDIO_TRANSCRIPT:
        return False

    display_title = _safe_title(title, "\u4f1a\u8bae\u8bed\u97f3\u8f6c\u5199")
    transcript_request = AudioTranscriptDownloadRequest(title=display_title, transcript_text=transcript_text)
    markdown_content = _render_transcript_markdown(display_title, transcript_text)
    docx_content = _build_transcript_docx_bytes(transcript_request).getvalue()
    structured_data = _load_structured_data(record)
    structured_data["display_title"] = display_title
    structured_data["transcript_text"] = transcript_text

    updated = update_audio_transcript_history(
        history_id,
        title=display_title,
        structured_data=json.dumps(structured_data, ensure_ascii=False),
        markdown_content=markdown_content,
        docx_content=docx_content,
    )
    if updated:
        logger.info("Updated audio transcript history: id=%s title=%s", history_id, display_title)
    return updated


def _run_workflow(text: str, report_type: str = "management", max_iterations: int = 2) -> dict[str, Any]:
    state: dict[str, Any] = {
        "raw_text": text,
        "report_type": report_type,
        "max_iterations": max_iterations,
        "iteration": 0,
    }
    result = meeting_workflow.invoke(state)
    return {
        "title": result.get("title", "会议结构化提取结果"),
        "date": result.get("date", ""),
        "weekly_period": result.get("weekly_period", ""),
        "decisions": result.get("decisions", []),
        "actions": result.get("actions", []),
        "risks": result.get("risks", []),
        "validation": result.get("validation", {}),
        "report_markdown": result.get("report_markdown", ""),
    }


def _download_bytes_response(content: bytes, media_type: str, filename: str) -> StreamingResponse:
    return StreamingResponse(io.BytesIO(content), media_type=media_type, headers=_build_download_headers(filename))


def _download_meeting_analysis_record(record: dict[str, Any], option: str) -> StreamingResponse:
    if option not in MEETING_DOWNLOAD_OPTIONS:
        raise HTTPException(status_code=400, detail="会议解析记录仅支持管理版/项目版的 Markdown 或 Word 下载")

    report_type, file_format = option.split("-", 1)
    structured_data = _load_structured_data(record)
    weekly_request = _build_weekly_request_from_payload(structured_data, report_type, title_override=str(record.get("title") or "会议纪要"))
    report_label = "管理版" if report_type == "management" else "项目版"

    if file_format == "markdown":
        markdown = render_report_markdown(_build_report_state(weekly_request))
        filename = f"{weekly_request.title}_{report_label}.md"
        return _download_bytes_response(markdown.encode("utf-8"), "text/markdown; charset=utf-8", filename)

    docx_bytes = _build_weekly_docx_bytes(weekly_request).getvalue()
    filename = f"{weekly_request.title}_{report_label}.docx"
    return _download_bytes_response(
        docx_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename,
    )



def _download_audio_transcript_record(record: dict[str, Any], option: str) -> StreamingResponse:
    if option not in AUDIO_DOWNLOAD_OPTIONS:
        raise HTTPException(status_code=400, detail="语音转写记录仅支持语音转文本 Markdown 或 Word 下载")

    title = _safe_title(record.get("title"), "会议语音转写")
    if option == "transcript-markdown":
        filename = f"{title}_语音转文本.md"
        content = str(record.get("markdown_content") or "")
        return _download_bytes_response(content.encode("utf-8"), "text/markdown; charset=utf-8", filename)

    filename = f"{title}_语音转文本.docx"
    docx_content = bytes(record.get("docx_content") or b"")
    return _download_bytes_response(
        docx_content,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze/text", response_model=AnalyzeResult)
def analyze_text(request: AnalyzeTextRequest) -> AnalyzeResult:
    payload = _run_workflow(request.text, report_type=request.report_type)
    result = AnalyzeResult(**payload)
    _save_meeting_analysis_history(result.model_dump(), request.report_type)
    return result


@app.post("/api/analyze/upload", response_model=AnalyzeResult)
async def analyze_upload(file: UploadFile = File(...), report_type: str = "management") -> AnalyzeResult:
    filename = file.filename or ""
    ext = filename.lower().split(".")[-1] if "." in filename else ""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    if ext in {"txt", "md"}:
        text = content.decode("utf-8", errors="ignore")
    elif ext == "docx":
        document = Document(io.BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    else:
        raise HTTPException(status_code=400, detail="仅支持 txt、md、docx 文件")

    payload = _run_workflow(text, report_type=report_type)
    result = AnalyzeResult(**payload)
    history_title = _build_source_title(filename, _safe_title(result.title, "会议纪要"))
    _save_meeting_analysis_history(result.model_dump(), report_type, title_override=history_title)
    return result


@app.post("/api/audio/transcribe", response_model=AudioTranscriptResponse)
async def transcribe_audio(file: UploadFile = File(...)) -> AudioTranscriptResponse:
    filename = file.filename or "meeting_audio.wav"
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 mp3、wav、m4a、mp4、aac、ogg、webm 音频文件")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传音频文件为空")

    try:
        transcript_text = transcribe_audio_with_speakers(filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ASRTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ASRServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected ASR failure: filename=%s", filename)
        raise HTTPException(status_code=502, detail=f"语音转写服务异常：{exc}") from exc

    title = _build_source_title(filename, "会议语音转写")
    analysis_payload: dict[str, Any] | None = None
    try:
        analysis_payload = _run_workflow(transcript_text, report_type="management")
    except Exception:
        logger.exception("Failed to analyze transcript for history persistence: filename=%s", filename)

    history_id: int | None = None
    try:
        history_id = _save_audio_transcript_history(title, transcript_text, analysis_payload)
    except Exception:
        logger.exception("Failed to persist audio transcript history: filename=%s", filename)

    return AudioTranscriptResponse(
        title=title,
        file_name=filename,
        transcript_text=transcript_text,
        markdown=_render_transcript_markdown(title, transcript_text),
        history_id=history_id,
    )


@app.post("/api/audio/transcribe/markdown")
def download_audio_transcript_markdown(request: AudioTranscriptDownloadRequest) -> StreamingResponse:
    markdown = _render_transcript_markdown(request.title, request.transcript_text)
    filename = f"{request.title}_转写稿.md"
    return _download_bytes_response(markdown.encode("utf-8"), "text/markdown; charset=utf-8", filename)


@app.post("/api/audio/transcribe/docx")
def download_audio_transcript_docx(request: AudioTranscriptDownloadRequest) -> StreamingResponse:
    filename = f"{request.title}_转写稿.docx"
    return _download_bytes_response(
        _build_transcript_docx_bytes(request).getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename,
    )


@app.put("/api/history/{history_id}/audio-transcript", response_model=AudioTranscriptResponse)
def update_audio_transcript_record(history_id: int, request: AudioTranscriptDownloadRequest) -> AudioTranscriptResponse:
    record = get_meeting_history_record(history_id)
    if not record:
        raise HTTPException(status_code=404, detail="\u672a\u627e\u5230\u5bf9\u5e94\u7684\u5386\u53f2\u8bb0\u5f55")
    if str(record.get("entry_type") or "") != ENTRY_TYPE_AUDIO_TRANSCRIPT:
        raise HTTPException(status_code=400, detail="\u8be5\u5386\u53f2\u8bb0\u5f55\u4e0d\u662f\u8bed\u97f3\u8f6c\u5199\u8bb0\u5f55")

    updated = _update_audio_transcript_history_entry(history_id, request.title, request.transcript_text)
    if not updated:
        raise HTTPException(status_code=500, detail="\u66f4\u65b0\u8bed\u97f3\u8f6c\u5199\u5386\u53f2\u5931\u8d25")

    display_title = _safe_title(request.title, "\u4f1a\u8bae\u8bed\u97f3\u8f6c\u5199")
    return AudioTranscriptResponse(
        title=display_title,
        file_name=display_title,
        transcript_text=request.transcript_text,
        markdown=_render_transcript_markdown(display_title, request.transcript_text),
        history_id=history_id,
    )


@app.post("/api/report/weekly", response_model=WeeklyReportResponse)
def generate_weekly_report(request: WeeklyReportRequest) -> WeeklyReportResponse:
    state = _build_report_state(request)
    return WeeklyReportResponse(markdown=render_report_markdown(state))


@app.post("/api/report/weekly/markdown")
def download_weekly_report_markdown(request: WeeklyReportRequest) -> StreamingResponse:
    markdown = render_report_markdown(_build_report_state(request))
    filename = f"{_safe_title(request.title, '会议纪要')}_{request.report_type}.md"
    return _download_bytes_response(markdown.encode("utf-8"), "text/markdown; charset=utf-8", filename)


@app.post("/api/report/weekly/docx")
def download_weekly_report_docx(request: WeeklyReportRequest) -> StreamingResponse:
    filename = f"{_safe_title(request.title, '会议纪要')}_{request.report_type}.docx"
    return _download_bytes_response(
        _build_weekly_docx_bytes(request).getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename,
    )


@app.get("/api/history", response_model=list[MeetingHistoryItem])
def get_history() -> list[MeetingHistoryItem]:
    items = list_meeting_history()
    return [MeetingHistoryItem(**item) for item in items]


@app.delete("/api/history/{history_id}")
def remove_history_item(history_id: int) -> dict[str, int | str]:
    deleted = delete_meeting_history(history_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到对应的历史记录")
    return {"message": "删除成功", "deleted_id": history_id}


@app.delete("/api/history")
def remove_all_history() -> dict[str, int | str]:
    deleted_count = clear_meeting_history()
    return {"message": "清空成功", "deleted_count": deleted_count}


@app.get("/api/history/{history_id}/download/{download_option}")
def download_history_file(history_id: int, download_option: str) -> StreamingResponse:
    record = get_meeting_history_record(history_id)
    if not record:
        raise HTTPException(status_code=404, detail="未找到对应的会议历史记录")

    option = download_option.strip().lower()
    entry_type = str(record.get("entry_type") or ENTRY_TYPE_MEETING_ANALYSIS)

    if entry_type == ENTRY_TYPE_AUDIO_TRANSCRIPT:
        return _download_audio_transcript_record(record, option)

    return _download_meeting_analysis_record(record, option)
