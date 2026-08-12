"""Quota-safe, native-only YouTube transcript retrieval through Supadata."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .storage import atomic_write_text

SUPADATA_TRANSCRIPT_URL = "https://api.supadata.ai/v1/transcript"
REQUEST_TIMEOUT_SECONDS = 45
POLL_TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TRANSCRIPT_CHARACTERS = 2_000_000
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

SupadataRequester = Callable[[str, dict[str, str]], tuple[int, dict[str, object]]]


class TranscriptRequestError(RuntimeError):
    """A safe, user-facing transcript failure."""

    def __init__(self, message: str, *, consumes_quota: bool = False) -> None:
        super().__init__(message)
        self.consumes_quota = consumes_quota


def canonical_youtube_video_url(value: str) -> tuple[str, str]:
    raw = value.strip()
    if len(raw) > 2_048:
        raise ValueError("YouTube URL is too long")
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as error:
        raise ValueError("invalid YouTube URL") from error
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or host not in YOUTUBE_HOSTS:
        raise ValueError("only public HTTPS YouTube video URLs are supported")

    video_id = ""
    path_parts = [part for part in parsed.path.split("/") if part]
    if host == "youtu.be" and path_parts:
        video_id = path_parts[0]
    elif parsed.path == "/watch":
        video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    elif len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live"}:
        video_id = path_parts[1]
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError("URL does not identify a supported YouTube video")
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def _request_json(url: str, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "ai-news-skills/1.0", **headers},
    )
    try:
        response = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError) as error:
        raise TranscriptRequestError("字幕服务网络连接失败，请稍后再试。") from error
    with response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        status = int(response.getcode())
    if len(body) > MAX_RESPONSE_BYTES:
        raise TranscriptRequestError("Supadata response exceeded the safe size limit")
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TranscriptRequestError("Supadata returned an invalid response") from error
    if not isinstance(payload, dict):
        raise TranscriptRequestError("Supadata returned an invalid response")
    return status, payload


def _safe_error(status: int, payload: dict[str, object]) -> TranscriptRequestError:
    error_code = str(payload.get("error", "")).casefold()
    if status == 206 or error_code == "transcript-unavailable":
        return TranscriptRequestError(
            "该视频没有可用的原生字幕；未启用付费 AI 转写。",
            consumes_quota=True,
        )
    messages = {
        400: "YouTube 链接或请求参数无效。",
        401: "字幕服务认证失败，请联系管理员。",
        403: "该视频受限，字幕服务无法访问。",
        404: "视频不存在、不可公开访问或字幕任务已过期。",
        429: "字幕服务当前达到限额，请稍后再试。",
    }
    return TranscriptRequestError(messages.get(status, "字幕服务暂时不可用，请稍后再试。"))


def _transcript_content(payload: dict[str, object]) -> tuple[str, str, list[str]]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    assert isinstance(result, dict)
    content = result.get("content")
    if isinstance(content, str):
        transcript = content
    elif isinstance(content, list):
        transcript = "\n".join(
            str(segment.get("text", "")).strip()
            for segment in content
            if isinstance(segment, dict) and str(segment.get("text", "")).strip()
        )
    else:
        transcript = ""
    transcript = transcript.replace("\x00", "").strip()
    if not transcript:
        raise TranscriptRequestError("字幕内容为空。", consumes_quota=True)
    if len(transcript) > MAX_TRANSCRIPT_CHARACTERS:
        raise TranscriptRequestError("字幕内容超过安全长度限制。", consumes_quota=True)
    language = str(result.get("lang", "")).strip()
    available = result.get("availableLangs", [])
    languages = [str(value) for value in available] if isinstance(available, list) else []
    return transcript, language, languages


def fetch_native_transcript(
    video_url: str,
    api_key: str,
    *,
    requester: SupadataRequester = _request_json,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    key = api_key.strip()
    if not key:
        raise ValueError("Supadata API key is not configured")
    video_id, canonical_url = canonical_youtube_video_url(video_url)
    query = urllib.parse.urlencode(
        {"url": canonical_url, "text": "true", "mode": "native"}
    )
    status, payload = requester(
        f"{SUPADATA_TRANSCRIPT_URL}?{query}", {"x-api-key": key}
    )
    if status == 202:
        job_id = str(payload.get("jobId", "")).strip()
        if not JOB_ID_RE.fullmatch(job_id):
            raise TranscriptRequestError("Supadata returned an invalid job identifier")
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        job_url = f"{SUPADATA_TRANSCRIPT_URL}/{urllib.parse.quote(job_id)}"
        while time.monotonic() < deadline:
            sleeper(1)
            status, payload = requester(job_url, {"x-api-key": key})
            job_status = str(payload.get("status", "")).casefold()
            if status == 200 and job_status == "completed":
                break
            if job_status == "failed" or status not in {200, 202}:
                raise _safe_error(status, payload)
        else:
            raise TranscriptRequestError("字幕任务仍在处理中，请稍后重试。")
    if status not in {200}:
        raise _safe_error(status, payload)
    transcript, language, languages = _transcript_content(payload)
    return {
        "video_id": video_id,
        "url": canonical_url,
        "content": transcript,
        "language": language,
        "available_languages": languages,
    }


def write_transcript_artifact(
    state_dir: Path,
    request_date: str,
    request_id: str,
    result: dict[str, object],
) -> Path:
    path = state_dir / "transcripts" / "on-demand" / request_date / f"{request_id}.txt"
    text = (
        f"Source: {result['url']}\n"
        f"Language: {result.get('language') or 'unknown'}\n\n"
        f"{result['content']}\n"
    )
    atomic_write_text(path, text)
    return path
