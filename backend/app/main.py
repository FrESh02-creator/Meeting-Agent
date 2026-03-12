from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

try:
    from imageio_ffmpeg import get_ffmpeg_exe
except ImportError:
    get_ffmpeg_exe = None

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

app = FastAPI(title="Meeting Agent API", version="0.6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "mp4", "aac", "ogg", "webm"}
MAX_TRANSCRIPTION_CHUNK_MS = 20_000
MAX_TRANSCRIPTION_SPLIT_DEPTH = 4


def _get_zhipu_client() -> OpenAI:
    api_key = os.getenv("ZHIPU_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未配置 ZHIPU_API_KEY")

    timeout_seconds = float(os.getenv("ZHIPU_TIMEOUT_SECONDS", "40"))
    base_url = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    return OpenAI(api_key=api_key, timeout=timeout_seconds, base_url=base_url)


def _get_ffmpeg_binary() -> str:
    env_ffmpeg = os.getenv("FFMPEG_BINARY", "").strip()
    if env_ffmpeg:
        if Path(env_ffmpeg).exists():
            return env_ffmpeg
        logger.warning("Configured FFMPEG_BINARY does not exist: %s", env_ffmpeg)

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    if get_ffmpeg_exe is not None:
        try:
            return get_ffmpeg_exe()
        except Exception:
            logger.warning("imageio-ffmpeg is installed but ffmpeg executable resolution failed")

    raise RuntimeError("未找到 ffmpeg。请安装 ffmpeg 并加入 PATH，或在 backend/.env 中配置 FFMPEG_BINARY")


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


def _extract_transcript_text(response: Any) -> str:
    if hasattr(response, "text") and response.text:
        return str(response.text).strip()

    if hasattr(response, "model_dump"):
        payload = response.model_dump()
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {}

    if isinstance(payload.get("text"), str) and payload["text"].strip():
        return payload["text"].strip()

    choices = payload.get("choices", [])
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()

    raise RuntimeError("语音转写接口未返回可用文本")


def _extract_service_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return "语音服务未返回可读错误信息"

    match = re.search(r"[\"']message[\"']\s*:\s*[\"']([^\"']+)[\"']", message)
    if match:
        return match.group(1).strip()

    if " - " in message:
        tail = message.split(" - ", 1)[1].strip()
        if tail:
            return tail

    return message


def _is_duration_limit_error(message: str) -> bool:
    normalized = message.lower()
    return (
        "1214" in normalized
        or "0-30秒" in message
        or "30秒" in message
        or "文件时长限制" in message
        or "duration limit" in normalized
    )


def _should_retry_asr(exc: Exception) -> bool:
    return not _is_duration_limit_error(_extract_service_error_message(exc))


def _normalize_audio_to_wav_bytes(filename: str, content: bytes) -> bytes:
    suffix = Path(filename).suffix or ".wav"
    ffmpeg_binary = _get_ffmpeg_binary()

    with tempfile.TemporaryDirectory(prefix="meeting-audio-") as temp_dir:
        input_path = Path(temp_dir) / f"source{suffix}"
        output_path = Path(temp_dir) / "normalized.wav"
        input_path.write_bytes(content)

        command = [
            ffmpeg_binary,
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(output_path),
        ]

        logger.info("Normalizing audio with ffmpeg: filename=%s", filename)
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 or not output_path.exists():
            stderr = (result.stderr or "").strip()
            lowered = stderr.lower()
            if "invalid data found" in lowered or "could not find codec parameters" in lowered:
                raise ValueError("无法解析当前音频文件，请确认文件未损坏且格式为 mp3、wav、m4a、mp4、aac、ogg 或 webm")
            if "not found" in lowered and "ffmpeg" in lowered:
                raise RuntimeError("未找到 ffmpeg，请安装 ffmpeg 并加入 PATH，或在 backend/.env 中配置 FFMPEG_BINARY")
            raise RuntimeError(f"音频预处理失败：{stderr or 'ffmpeg 执行失败'}")

        return output_path.read_bytes()


def _split_wav_bytes(filename: str, wav_bytes: bytes) -> List[Tuple[str, bytes]]:
    stem = Path(filename).stem or "meeting_audio"
    chunks: List[Tuple[str, bytes]] = []

    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        params = wav_file.getparams()
        frames_per_chunk = int(params.framerate * (MAX_TRANSCRIPTION_CHUNK_MS / 1000))
        index = 1

        while True:
            frames = wav_file.readframes(frames_per_chunk)
            if not frames:
                break

            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as chunk_wav:
                chunk_wav.setparams(params)
                chunk_wav.writeframes(frames)

            chunks.append((f"{stem}_part_{index:03d}.wav", buffer.getvalue()))
            index += 1

    return chunks


def _split_audio_for_transcription(filename: str, content: bytes) -> List[Tuple[str, bytes]]:
    wav_bytes = _normalize_audio_to_wav_bytes(filename, content)
    chunks = _split_wav_bytes(filename, wav_bytes)
    if not chunks:
        raise RuntimeError("音频预处理完成，但未生成可转写分片")
    if len(chunks) > 1:
        logger.info("Audio split completed: filename=%s chunks=%s", filename, len(chunks))
    return chunks


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    retry=retry_if_exception(_should_retry_asr),
    reraise=True,
)
def _transcribe_audio_chunk(filename: str, content: bytes) -> str:
    client = _get_zhipu_client()
    model = os.getenv("ZHIPU_ASR_MODEL", "glm-asr-2512")
    audio_buffer = io.BytesIO(content)
    audio_buffer.name = filename or "meeting_audio_part.wav"
    logger.info("Calling Zhipu ASR: chunk=%s bytes=%s", audio_buffer.name, len(content))
    response = client.audio.transcriptions.create(model=model, file=audio_buffer)
    return _extract_transcript_text(response)


def _transcribe_audio_chunk_with_fallback(filename: str, content: bytes, depth: int = 0) -> str:
    try:
        return _transcribe_audio_chunk(filename, content).strip()
    except Exception as exc:
        message = _extract_service_error_message(exc)
        logger.warning("ASR chunk failed: chunk=%s depth=%s error=%s", filename, depth, message)

        if _is_duration_limit_error(message) and depth < MAX_TRANSCRIPTION_SPLIT_DEPTH:
            sub_chunks = _split_audio_for_transcription(filename, content)
            if len(sub_chunks) <= 1:
                raise RuntimeError("当前音频分片仍被判定超过 30 秒，请先手动裁剪更短后重试") from exc

            transcript_parts: List[str] = []
            for index, (chunk_name, chunk_bytes) in enumerate(sub_chunks, start=1):
                segment_text = _transcribe_audio_chunk_with_fallback(
                    f"{Path(chunk_name).stem}_retry_{index:03d}.wav",
                    chunk_bytes,
                    depth + 1,
                ).strip()
                if segment_text:
                    transcript_parts.append(segment_text)

            combined_text = "\n\n".join(transcript_parts).strip()
            if combined_text:
                return combined_text

        if _is_duration_limit_error(message):
            raise RuntimeError("当前智谱语音接口单次仅支持 30 秒内音频，系统自动切片后仍未成功，请先裁剪音频后重试") from exc

        raise RuntimeError(f"语音转写失败：{message}") from exc


def _transcribe_audio_with_zhipu(filename: str, content: bytes) -> str:
    chunks = _split_audio_for_transcription(filename, content)
    transcript_parts: List[str] = []
    for chunk_name, chunk_content in chunks:
        chunk_text = _transcribe_audio_chunk_with_fallback(chunk_name, chunk_content).strip()
        if chunk_text:
            transcript_parts.append(chunk_text)

    transcript_text = "\n\n".join(transcript_parts).strip()
    if not transcript_text:
        raise RuntimeError("语音转写结果为空")
    return transcript_text


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
        transcript_text = _transcribe_audio_with_zhipu(filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
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
