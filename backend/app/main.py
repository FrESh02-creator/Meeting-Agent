from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .asr import ASRServiceError, ASRTimeoutError, transcribe_audio_with_speakers
from .graph import meeting_workflow, render_report_markdown
from .schemas import (
    AnalyzeResult,
    AnalyzeTextRequest,
    AudioTranscriptDownloadRequest,
    AudioTranscriptResponse,
    WeeklyReportRequest,
    WeeklyReportResponse,
)

load_dotenv()

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(title="Meeting Agent API", version="0.7.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "mp4", "aac", "ogg", "webm"}


def _build_download_headers(filename: str) -> Dict[str, str]:
    ascii_filename = "".join(ch if ch.isascii() and ch not in '\\/:*?"<>|' else "_" for ch in filename)
    ascii_filename = ascii_filename.strip(" ._") or "report"
    return {
        "Content-Disposition": f"attachment; filename={ascii_filename}; filename*=UTF-8''{quote(filename)}",
    }


def _build_report_state(request: WeeklyReportRequest) -> Dict[str, Any]:
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
    document_title = "项目管理周报" if request.report_type == "management" else (request.title or "项目版会议纪要")
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
        document.add_paragraph("无")

    document.add_heading("行动项", level=2)
    if request.actions:
        for action in request.actions:
            document.add_paragraph(
                f"{action.task} | 负责人：{action.owner} | 截止时间：{action.deadline}",
                style="List Bullet",
            )
    else:
        document.add_paragraph("无")

    document.add_heading("风险项", level=2)
    if request.risks:
        for risk in request.risks:
            document.add_paragraph(risk, style="List Bullet")
    else:
        document.add_paragraph("无")

    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


def _run_workflow(text: str, report_type: str = "management", max_iterations: int = 2) -> Dict[str, Any]:
    state: Dict[str, Any] = {
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


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze/text", response_model=AnalyzeResult)
def analyze_text(request: AnalyzeTextRequest) -> AnalyzeResult:
    payload = _run_workflow(request.text, report_type=request.report_type)
    return AnalyzeResult(**payload)


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
    return AnalyzeResult(**payload)


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

    title = Path(filename).stem or "会议语音转写"
    return AudioTranscriptResponse(
        title=title,
        file_name=filename,
        transcript_text=transcript_text,
        markdown=_render_transcript_markdown(title, transcript_text),
    )


@app.post("/api/audio/transcribe/markdown")
def download_audio_transcript_markdown(request: AudioTranscriptDownloadRequest) -> StreamingResponse:
    markdown = _render_transcript_markdown(request.title, request.transcript_text)
    filename = f"{request.title}_转写稿.md"
    return StreamingResponse(
        io.BytesIO(markdown.encode("utf-8")),
        media_type="text/markdown; charset=utf-8",
        headers=_build_download_headers(filename),
    )


@app.post("/api/audio/transcribe/docx")
def download_audio_transcript_docx(request: AudioTranscriptDownloadRequest) -> StreamingResponse:
    filename = f"{request.title}_转写稿.docx"
    return StreamingResponse(
        _build_transcript_docx_bytes(request),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=_build_download_headers(filename),
    )


@app.post("/api/report/weekly", response_model=WeeklyReportResponse)
def generate_weekly_report(request: WeeklyReportRequest) -> WeeklyReportResponse:
    state = _build_report_state(request)
    return WeeklyReportResponse(markdown=render_report_markdown(state))


@app.post("/api/report/weekly/markdown")
def download_weekly_report_markdown(request: WeeklyReportRequest) -> StreamingResponse:
    state = _build_report_state(request)
    markdown = render_report_markdown(state)
    filename = f"{request.title or '会议纪要'}_{request.report_type}.md"
    return StreamingResponse(
        io.BytesIO(markdown.encode("utf-8")),
        media_type="text/markdown; charset=utf-8",
        headers=_build_download_headers(filename),
    )


@app.post("/api/report/weekly/docx")
def download_weekly_report_docx(request: WeeklyReportRequest) -> StreamingResponse:
    filename = f"{request.title or '会议纪要'}_{request.report_type}.docx"
    return StreamingResponse(
        _build_weekly_docx_bytes(request),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=_build_download_headers(filename),
    )
