from __future__ import annotations

import base64
import json
import logging
import os
import posixpath
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

try:
    from qcloud_cos import CosConfig, CosS3Client
    from qcloud_cos.cos_exception import CosClientError, CosServiceError
except ImportError:  # pragma: no cover - optional dependency at import time
    CosConfig = None
    CosS3Client = None
    CosClientError = Exception
    CosServiceError = Exception

try:
    from tencentcloud.asr.v20190614 import asr_client, models
    from tencentcloud.common import credential
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
except ImportError:  # pragma: no cover - optional dependency at import time
    asr_client = None
    models = None
    credential = None
    TencentCloudSDKException = Exception
    ClientProfile = None
    HttpProfile = None

load_dotenv()

logger = logging.getLogger(__name__)


class ASRServiceError(RuntimeError):
    """Raised when the external ASR provider fails."""


class ASRTimeoutError(ASRServiceError):
    """Raised when an ASR task exceeds the allowed waiting time."""


@dataclass(frozen=True)
class TencentASRSettings:
    secret_id: str
    secret_key: str
    region: str
    engine_model_type: str
    res_text_format: int
    speaker_diarization: int
    speaker_number: int
    poll_interval_seconds: int
    timeout_seconds: int
    cos_bucket: str
    cos_region: str
    cos_prefix: str
    cos_sign_expire_seconds: int


LARGE_MODEL_ENGINES = {"16k_zh_large", "16k_zh_en", "16k_multi_lang", "8k_zh_large"}
FREE_TIER_FALLBACK_ENGINE = "16k_zh"


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _normalize_cos_prefix(prefix: str) -> str:
    prefix = (prefix or "").strip().strip("/")
    return prefix


def _looks_like_placeholder(value: str) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith("your_") or normalized.startswith("your-") or "example" in normalized


@lru_cache(maxsize=1)
def _load_settings() -> TencentASRSettings:
    settings = TencentASRSettings(
        secret_id=os.getenv("TENCENT_SECRET_ID", "").strip(),
        secret_key=os.getenv("TENCENT_SECRET_KEY", "").strip(),
        region=os.getenv("TENCENT_ASR_REGION", "ap-shanghai").strip() or "ap-shanghai",
        engine_model_type=os.getenv("TENCENT_ASR_ENGINE_MODEL_TYPE", FREE_TIER_FALLBACK_ENGINE).strip()
        or FREE_TIER_FALLBACK_ENGINE,
        res_text_format=_env_int("TENCENT_ASR_RES_TEXT_FORMAT", 3),
        speaker_diarization=_env_int("TENCENT_ASR_SPEAKER_DIARIZATION", 1),
        speaker_number=_env_int("TENCENT_ASR_SPEAKER_NUMBER", 0),
        poll_interval_seconds=max(1, _env_int("TENCENT_ASR_POLL_INTERVAL_SECONDS", 3)),
        timeout_seconds=max(30, _env_int("TENCENT_ASR_TIMEOUT_SECONDS", 1800)),
        cos_bucket=os.getenv("TENCENT_COS_BUCKET", "").strip(),
        cos_region=os.getenv("TENCENT_COS_REGION", "").strip(),
        cos_prefix=_normalize_cos_prefix(os.getenv("TENCENT_COS_PREFIX", "meeting-agent/audio")),
        cos_sign_expire_seconds=max(300, _env_int("TENCENT_COS_SIGN_EXPIRE_SECONDS", 7200)),
    )
    if not settings.secret_id or not settings.secret_key:
        raise ASRServiceError("未配置腾讯云语音识别密钥，请设置 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY。")
    return settings


def _ensure_sdk_dependencies() -> None:
    if asr_client is None or models is None or credential is None:
        raise ASRServiceError(
            "缺少腾讯云 SDK，请安装 tencentcloud-sdk-python 和 cos-python-sdk-v5。"
        )


def _build_asr_client(settings: TencentASRSettings) -> Any:
    _ensure_sdk_dependencies()
    http_profile = HttpProfile(endpoint="asr.tencentcloudapi.com", reqTimeout=settings.timeout_seconds)
    client_profile = ClientProfile(httpProfile=http_profile)
    cred = credential.Credential(settings.secret_id, settings.secret_key)
    return asr_client.AsrClient(cred, settings.region, client_profile)


def _build_cos_client(settings: TencentASRSettings) -> Any:
    _ensure_sdk_dependencies()
    if CosConfig is None or CosS3Client is None:
        raise ASRServiceError("缺少腾讯云 COS SDK，请安装 cos-python-sdk-v5。")
    if not settings.cos_bucket or not settings.cos_region:
        raise ASRServiceError(
            "长音频识别需要先上传到 COS，请设置 TENCENT_COS_BUCKET 和 TENCENT_COS_REGION。"
        )
    if _looks_like_placeholder(settings.cos_bucket):
        raise ASRServiceError(
            "TENCENT_COS_BUCKET 仍然是示例值，请改成真实的 COS Bucket 名称，例如 bucket-1250000000。"
        )
    config = CosConfig(
        Region=settings.cos_region,
        SecretId=settings.secret_id,
        SecretKey=settings.secret_key,
        Scheme="https",
    )
    return CosS3Client(config)


def _build_cos_object_key(settings: TencentASRSettings, filename: str) -> str:
    suffix = Path(filename).suffix or ".wav"
    basename = f"{int(time.time())}-{uuid.uuid4().hex}{suffix}"
    if settings.cos_prefix:
        return posixpath.join(settings.cos_prefix, basename)
    return basename


def _should_use_cos(settings: TencentASRSettings, content: bytes) -> bool:
    if settings.cos_bucket and settings.cos_region and not _looks_like_placeholder(settings.cos_bucket):
        return True
    return len(content) > 5 * 1024 * 1024


def _create_source_payload(settings: TencentASRSettings, filename: str, content: bytes) -> tuple[dict[str, Any], str | None, str | None]:
    if _should_use_cos(settings, content):
        url, object_key = _upload_audio_to_cos(settings, filename, content)
        return {"SourceType": 0, "Url": url}, object_key, url

    if len(content) > 5 * 1024 * 1024:
        raise ASRServiceError(
            "当前音频超过 5MB，必须配置真实可用的腾讯云 COS Bucket 才能提交长音频识别。"
        )

    encoded_audio = base64.b64encode(content).decode("utf-8")
    return {"SourceType": 1, "Data": encoded_audio, "DataLen": len(content)}, None, None


def _upload_audio_to_cos(settings: TencentASRSettings, filename: str, content: bytes) -> tuple[str, str]:
    client = _build_cos_client(settings)
    object_key = _build_cos_object_key(settings, filename)
    logger.info("Uploading audio to Tencent COS: filename=%s object_key=%s", filename, object_key)
    try:
        client.put_object(Bucket=settings.cos_bucket, Body=content, Key=object_key)
        url = client.get_presigned_url(
            Method="GET",
            Bucket=settings.cos_bucket,
            Key=object_key,
            Expired=settings.cos_sign_expire_seconds,
        )
    except (CosClientError, CosServiceError) as exc:
        raise ASRServiceError(f"上传音频到腾讯云 COS 失败：{exc}") from exc
    return url, object_key


def _delete_cos_object(settings: TencentASRSettings, object_key: str | None) -> None:
    if not object_key:
        return
    try:
        client = _build_cos_client(settings)
        client.delete_object(Bucket=settings.cos_bucket, Key=object_key)
    except Exception as exc:  # pragma: no cover - cleanup should not break requests
        logger.warning("Failed to delete Tencent COS object: key=%s error=%s", object_key, exc)


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(TencentCloudSDKException),
)
def _create_rec_task(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    request = models.CreateRecTaskRequest()
    request.from_json_string(json.dumps(payload))
    response = client.CreateRecTask(request)
    response_json = json.loads(response.to_json_string())
    logger.info("Tencent ASR CreateRecTask response received")
    return response_json


def _is_user_has_no_amount_error(exc: Exception) -> bool:
    return "FailedOperation.UserHasNoAmount" in str(exc)


def _should_fallback_to_free_tier_engine(engine_model_type: str, exc: Exception) -> bool:
    return engine_model_type in LARGE_MODEL_ENGINES and _is_user_has_no_amount_error(exc)


def _build_rec_task_payload(settings: TencentASRSettings, source_payload: dict[str, Any], engine_model_type: str) -> dict[str, Any]:
    return {
        "EngineModelType": engine_model_type,
        "ChannelNum": 1,
        "ResTextFormat": settings.res_text_format,
        "SpeakerDiarization": settings.speaker_diarization,
        "SpeakerNumber": settings.speaker_number,
        **source_payload,
    }


def _submit_and_poll_task(
    client: Any,
    settings: TencentASRSettings,
    filename: str,
    source_payload: dict[str, Any],
    engine_model_type: str,
) -> dict[str, Any]:
    payload = _build_rec_task_payload(settings, source_payload, engine_model_type)
    logger.info(
        "Calling Tencent ASR: filename=%s source_type=%s engine=%s diarization=%s",
        filename,
        payload["SourceType"],
        engine_model_type,
        settings.speaker_diarization,
    )
    create_response = _create_rec_task(client, payload)
    task_id = _extract_task_id(create_response)
    logger.info("Tencent ASR task created: filename=%s task_id=%s engine=%s", filename, task_id, engine_model_type)
    task_data = _poll_task_result(client, task_id, settings)
    logger.info("Tencent ASR task completed: filename=%s task_id=%s engine=%s", filename, task_id, engine_model_type)
    return task_data


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(TencentCloudSDKException),
)
def _describe_rec_task(client: Any, task_id: int) -> dict[str, Any]:
    request = models.DescribeTaskStatusRequest()
    request.from_json_string(json.dumps({"TaskId": task_id}))
    response = client.DescribeTaskStatus(request)
    return json.loads(response.to_json_string())


def _extract_response_data(response_json: dict[str, Any]) -> dict[str, Any]:
    data = response_json.get("Data")
    if isinstance(data, dict):
        return data

    response = response_json.get("Response", {})
    data = response.get("Data")
    if isinstance(data, dict):
        return data

    if isinstance(response, dict) and response:
        return response

    return response_json


def _extract_task_id(response_json: dict[str, Any]) -> int:
    data = _extract_response_data(response_json)
    task_id = (
        data.get("TaskId")
        or response_json.get("TaskId")
        or response_json.get("Response", {}).get("TaskId")
        or response_json.get("Data", {}).get("TaskId")
    )
    if not task_id:
        raise ASRServiceError(f"腾讯云 CreateRecTask 未返回 TaskId：{response_json}")
    return int(task_id)


def _is_success_status(status: Any, data: dict[str, Any]) -> bool:
    status_text = str(status).strip().lower()
    if status in {2, "2"}:
        return True
    if status_text in {"success", "succeeded", "done", "finished", "completed"}:
        return True
    if any(keyword in status_text for keyword in ("success", "done", "finish", "complete")):
        return True
    return bool(data.get("Result") or data.get("ResultDetail"))


def _is_failed_status(status: Any) -> bool:
    status_text = str(status).strip().lower()
    if status in {-1, 3, "3"}:
        return True
    return any(keyword in status_text for keyword in ("fail", "error", "cancel"))


def _poll_task_result(client: Any, task_id: int, settings: TencentASRSettings) -> dict[str, Any]:
    deadline = time.monotonic() + settings.timeout_seconds
    while time.monotonic() < deadline:
        response_json = _describe_rec_task(client, task_id)
        data = _extract_response_data(response_json)
        status = data.get("Status", data.get("TaskStatus", data.get("StatusStr")))
        logger.info("Polling Tencent ASR task: task_id=%s status=%s", task_id, status)

        if _is_success_status(status, data):
            return data

        if _is_failed_status(status):
            message = (
                data.get("ErrorMsg")
                or data.get("FailedReason")
                or response_json.get("ErrorMsg")
                or response_json.get("Response", {}).get("ErrorMsg")
            )
            raise ASRServiceError(f"腾讯云语音识别任务失败：{message or '未知错误'}")

        time.sleep(settings.poll_interval_seconds)

    raise ASRTimeoutError(f"腾讯云语音识别任务超时，等待超过 {settings.timeout_seconds} 秒。")


def _speaker_label_map() -> dict[str, str]:
    return {}


def _normalize_speaker_label(raw_speaker: Any, label_map: dict[str, str]) -> str:
    key = str(raw_speaker if raw_speaker is not None else "0")
    if key not in label_map:
        label_map[key] = f"Speaker {len(label_map) + 1}"
    return label_map[key]


def _extract_detail_text(detail: dict[str, Any]) -> str:
    candidates = (
        detail.get("FinalSentence"),
        detail.get("Sentence"),
        detail.get("Text"),
        detail.get("SliceSentence"),
        detail.get("Transcript"),
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _join_turn_text(existing: str, new_text: str) -> str:
    if not existing:
        return new_text
    if existing.endswith(("。", "！", "？", "；", "，", ".", "!", "?", ";", ",")):
        return f"{existing}{new_text}"
    return f"{existing} {new_text}"


def _format_result_details(data: dict[str, Any]) -> str:
    details = data.get("ResultDetail")
    if not isinstance(details, list) or not details:
        result_text = str(data.get("Result", "")).strip()
        if not result_text:
            raise ASRServiceError("腾讯云语音识别未返回可用文本。")
        return f"[Speaker 1]: {result_text}"

    label_map = _speaker_label_map()
    turns: list[dict[str, str]] = []

    for detail in details:
        if not isinstance(detail, dict):
            continue
        text = _extract_detail_text(detail)
        if not text:
            continue
        speaker_label = _normalize_speaker_label(detail.get("SpeakerId"), label_map)
        if turns and turns[-1]["speaker"] == speaker_label:
            turns[-1]["text"] = _join_turn_text(turns[-1]["text"], text)
        else:
            turns.append({"speaker": speaker_label, "text": text})

    if not turns:
        result_text = str(data.get("Result", "")).strip()
        if result_text:
            return f"[Speaker 1]: {result_text}"
        raise ASRServiceError("腾讯云语音识别结果为空。")

    return "\n".join(f"[{turn['speaker']}]: {turn['text']}" for turn in turns)


def transcribe_audio_with_speakers(filename: str, content: bytes) -> str:
    if not filename:
        raise ValueError("缺少音频文件名。")
    if not content:
        raise ValueError("音频文件为空。")

    settings = _load_settings()
    client = _build_asr_client(settings)
    source_payload, object_key, source_url = _create_source_payload(settings, filename, content)
    if source_url:
        logger.info("Tencent ASR audio source URL prepared: filename=%s", filename)

    try:
        try:
            task_data = _submit_and_poll_task(client, settings, filename, source_payload, settings.engine_model_type)
        except TencentCloudSDKException as exc:
            if _should_fallback_to_free_tier_engine(settings.engine_model_type, exc):
                logger.warning(
                    "Tencent ASR large-model quota unavailable, retrying with free-tier compatible engine: filename=%s from=%s to=%s",
                    filename,
                    settings.engine_model_type,
                    FREE_TIER_FALLBACK_ENGINE,
                )
                task_data = _submit_and_poll_task(
                    client,
                    settings,
                    filename,
                    source_payload,
                    FREE_TIER_FALLBACK_ENGINE,
                )
            else:
                raise
        transcript_text = _format_result_details(task_data)
        logger.info("Tencent ASR completed: filename=%s text_length=%s", filename, len(transcript_text))
        return transcript_text
    except TencentCloudSDKException as exc:
        if _is_user_has_no_amount_error(exc):
            raise ASRServiceError(
                "腾讯云返回资源包额度不足。当前常见原因是调用了大模型版录音识别，但账号只有普通录音文件识别免费包。"
                " 已自动尝试降级到普通引擎；如果仍失败，请把 TENCENT_ASR_ENGINE_MODEL_TYPE 设置为 16k_zh，"
                "并确认账号已开通“录音文件识别”而不是仅开通“大模型版”。"
            ) from exc
        raise ASRServiceError(f"腾讯云语音识别调用失败：{exc}") from exc
    finally:
        _delete_cos_object(settings, object_key)
