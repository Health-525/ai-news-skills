"""Export validated digest items and publish them to a read-only news platform."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypedDict
from zoneinfo import ZoneInfo

from .digest import FrozenItem, validate_frozen_digest
from .storage import atomic_write_json

PLATFORM_SCHEMA_VERSION = 1
PLATFORM_TIMEOUT_SECONDS = 30
FEISHU_API_ROOT = "https://open.feishu.cn/open-apis"
FEISHU_TOKEN_URL = f"{FEISHU_API_ROOT}/auth/v3/tenant_access_token/internal"
SHANGHAI = ZoneInfo("Asia/Shanghai")
GITHUB_STARS_RE = re.compile(r"GitHub reports ([\d,]+) total Stars", re.IGNORECASE)
SECTION_NAMES = {
    "official_news": "官方动态",
    "youtube": "YouTube",
    "aihot": "AIHOT",
    "github_trending": "GitHub热门项目",
    "security_advisory": "安全公告",
    "model_hub": "模型Hub",
    "industry_digest": "行业精选",
    "builders_x": "X动态",
}


BITABLE_FIELDS = {
    "record_key": "记录键",
    "news_id": "新闻ID",
    "report_date": "日报日期",
    "section": "板块",
    "title": "标题",
    "summary": "中文摘要",
    "source_name": "来源名称",
    "original_url": "原文链接",
    "published_at": "发布时间",
    "is_highlight": "是否重点",
    "is_recovered": "是否补录",
    "github_total_stars": "GitHub总Star",
    "rank_position": "排序",
    "uploaded_at": "上传时间",
}


FeishuRequester = Callable[
    [str, str, dict[str, str], dict[str, object] | None], dict[str, object]
]


def _github_stars(record: dict[str, object]) -> int | None:
    if str(record.get("source_type", "")) != "github_trending":
        return None
    match = GITHUB_STARS_RE.search(str(record.get("source_text", "")))
    return int(match.group(1).replace(",", "")) if match else None


def _published_at(record: dict[str, object]) -> str:
    value = str(record.get("published_at", "")).strip()
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("platform record contains an invalid published_at") from error
    if parsed.tzinfo is None:
        raise ValueError("platform record published_at requires a timezone")
    return parsed.isoformat()


def build_platform_payload(
    source_payload: dict[str, object],
    markdown: str,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    date_str = str(source_payload.get("date", "")).strip()
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("platform export requires a valid report date") from error

    items = validate_frozen_digest(source_payload, markdown)
    source_records = source_payload.get("items")
    if not isinstance(source_records, list):
        raise ValueError("source payload items must be an array")
    by_url = {
        str(record["url"]): record
        for record in source_records
        if isinstance(record, dict) and record.get("url")
    }
    generated = _payload_generated_at(source_payload, date_str, generated_at)
    records: list[dict[str, object]] = []
    for position, item in enumerate(items, start=1):
        record = by_url[item.url]
        records.append(
            _platform_record(date_str, position, item, record, generated)
        )

    payload: dict[str, object] = {
        "schema_version": PLATFORM_SCHEMA_VERSION,
        "platform": "AI News Skills",
        "report_date": date_str,
        "generated_at": generated.isoformat(),
        "records": records,
    }
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["payload_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return payload


def _payload_generated_at(
    source_payload: dict[str, object],
    date_str: str,
    override: datetime | None,
) -> datetime:
    if override is not None:
        generated = override
    else:
        source_value = str(source_payload.get("generated_at", "")).strip()
        try:
            generated = datetime.fromisoformat(source_value.replace("Z", "+00:00"))
        except ValueError:
            generated = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if generated.tzinfo is None:
        raise ValueError("platform generated_at requires a timezone")
    return generated.astimezone(timezone.utc)


def _platform_record(
    date_str: str,
    position: int,
    item: FrozenItem,
    source_record: dict[str, object],
    generated_at: datetime,
) -> dict[str, object]:
    section = SECTION_NAMES.get(item.source_type)
    if not section:
        raise ValueError(f"unsupported platform source type: {item.source_type}")
    news_id = item.item_id.strip()
    if not news_id:
        raise ValueError("platform record requires a news ID")
    return {
        "record_key": f"{date_str}:{news_id}",
        "news_id": news_id,
        "report_date": date_str,
        "section": section,
        "source_type": item.source_type,
        "title": item.title,
        "summary": item.summary,
        "source_name": item.source,
        "original_url": item.url,
        "published_at": _published_at(source_record),
        "is_highlight": item.highlight,
        "is_recovered": item.recency_status == "recovered",
        "is_available": not item.summary.startswith("不可用（"),
        "github_total_stars": _github_stars(source_record),
        "rank_position": position,
        "uploaded_at": generated_at.isoformat(),
    }


def export_platform_payload(
    source_path: Path,
    digest_path: Path,
    output_path: Path,
) -> dict[str, object]:
    try:
        source_payload = json.loads(source_path.read_text(encoding="utf-8"))
        markdown = digest_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"platform input is invalid: {error}") from error
    if not isinstance(source_payload, dict):
        raise ValueError("platform source payload must be an object")
    payload = build_platform_payload(source_payload, markdown)
    atomic_write_json(output_path, payload)
    return payload


def _feishu_request(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, object] | None,
) -> dict[str, object]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "ai-news-skills/1.0",
        **headers,
    }
    body = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=PLATFORM_TIMEOUT_SECONDS) as response:
        response_body = response.read()
    result = json.loads(response_body.decode("utf-8")) if response_body else {}
    if not isinstance(result, dict):
        raise ValueError("Feishu response must be a JSON object")
    return result


def _configuration() -> dict[str, str]:
    app_id = os.environ.get("AI_NEWS_FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("AI_NEWS_FEISHU_APP_SECRET", "").strip()
    if bool(app_id) != bool(app_secret):
        raise ValueError("Bitable publisher Feishu credentials are incomplete")
    if not app_id:
        app_id, app_secret = _openclaw_feishu_credentials()
    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "app_token": os.environ.get("AI_NEWS_BITABLE_APP_TOKEN", "").strip(),
        "table_id": os.environ.get("AI_NEWS_BITABLE_TABLE_ID", "").strip(),
    }


def _openclaw_feishu_credentials() -> tuple[str, str]:
    config_path = Path(
        os.environ.get(
            "AI_NEWS_OPENCLAW_CONFIG",
            str(Path.home() / ".openclaw" / "openclaw.json"),
        )
    ).expanduser()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""

    candidates: list[tuple[tuple[str, ...], str, str]] = []

    def visit(value: object, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            normalized = {
                str(key).lower().replace("_", ""): child
                for key, child in value.items()
            }
            context = ".".join(path).lower()
            app_id = str(normalized.get("appid", "")).strip()
            app_secret = str(normalized.get("appsecret", "")).strip()
            if ("feishu" in context or "lark" in context) and app_id and app_secret:
                candidates.append((path, app_id, app_secret))
            for key, child in value.items():
                visit(child, (*path, str(key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))

    visit(payload)
    account_id = os.environ.get("OPENCLAW_FEISHU_ACCOUNT_ID", "").strip()
    if account_id:
        candidates = [
            candidate for candidate in candidates if account_id in candidate[0]
        ]
    if len(candidates) > 1:
        raise ValueError(
            "multiple OpenClaw Feishu accounts found; set OPENCLAW_FEISHU_ACCOUNT_ID"
        )
    if not candidates:
        return "", ""
    return candidates[0][1], candidates[0][2]


def validate_platform_configuration() -> bool:
    targets = {
        "app_token": os.environ.get("AI_NEWS_BITABLE_APP_TOKEN", "").strip(),
        "table_id": os.environ.get("AI_NEWS_BITABLE_TABLE_ID", "").strip(),
    }
    if not any(targets.values()):
        return False
    if not all(targets.values()):
        missing = ", ".join(key for key, value in targets.items() if not value)
        raise ValueError(f"Bitable publisher configuration is incomplete: {missing}")
    config = _configuration()
    if not config["app_id"] or not config["app_secret"]:
        raise ValueError(
            "Bitable publisher requires Feishu credentials in runtime.env or OpenClaw config"
        )
    return True


def _require_success(payload: dict[str, object], operation: str) -> dict[str, object]:
    if payload.get("code") != 0:
        raise RuntimeError(f"Feishu {operation} failed with code {payload.get('code', 'unknown')}")
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise RuntimeError(f"Feishu {operation} returned invalid data")
    return data


def _tenant_access_token(
    config: dict[str, str], requester: FeishuRequester
) -> str:
    payload = requester(
        "POST",
        FEISHU_TOKEN_URL,
        {},
        {"app_id": config["app_id"], "app_secret": config["app_secret"]},
    )
    if payload.get("code") != 0:
        raise RuntimeError(
            f"Feishu token request failed with code {payload.get('code', 'unknown')}"
        )
    token = str(payload.get("tenant_access_token", "")).strip()
    if not token:
        raise RuntimeError("Feishu token response contains no tenant_access_token")
    return token


def _text_field(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(part.get("text", ""))
            for part in value
            if isinstance(part, dict)
        )
    return ""


def _existing_records(
    config: dict[str, str],
    token: str,
    report_date: str,
    requester: FeishuRequester,
) -> dict[str, str]:
    base_url = (
        f"{FEISHU_API_ROOT}/bitable/v1/apps/{config['app_token']}"
        f"/tables/{config['table_id']}/records"
    )
    page_token = ""
    existing: dict[str, str] = {}
    report_filter = f'CurrentValue.[日报日期] = TODATE("{report_date}")'
    while True:
        query = {"page_size": "500", "filter": report_filter}
        if page_token:
            query["page_token"] = page_token
        url = f"{base_url}?{urllib.parse.urlencode(query)}"
        data = _require_success(
            requester("GET", url, {"Authorization": f"Bearer {token}"}, None),
            "list Bitable records",
        )
        items = data.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("Feishu list Bitable records returned invalid items")
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields", {})
            record_id = str(item.get("record_id", "")).strip()
            if not isinstance(fields, dict) or not record_id:
                continue
            key = _text_field(fields.get(BITABLE_FIELDS["record_key"])).strip()
            if key:
                existing[key] = record_id
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token", "")).strip()
        if not page_token:
            raise RuntimeError("Feishu pagination returned no page token")
    return existing


def _milliseconds(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Bitable datetime requires a timezone")
    return int(parsed.timestamp() * 1000)


def _bitable_fields(record: dict[str, object]) -> dict[str, object]:
    report_date = str(record["report_date"])
    report_timestamp = int(
        datetime.strptime(report_date, "%Y-%m-%d")
        .replace(tzinfo=SHANGHAI)
        .timestamp()
        * 1000
    )
    fields: dict[str, object] = {
        BITABLE_FIELDS["record_key"]: record["record_key"],
        BITABLE_FIELDS["news_id"]: record["news_id"],
        BITABLE_FIELDS["report_date"]: report_timestamp,
        BITABLE_FIELDS["section"]: record["section"],
        BITABLE_FIELDS["title"]: record["title"],
        BITABLE_FIELDS["summary"]: record["summary"],
        BITABLE_FIELDS["source_name"]: record["source_name"],
        BITABLE_FIELDS["original_url"]: {
            "text": record["title"],
            "link": record["original_url"],
        },
        BITABLE_FIELDS["is_highlight"]: record["is_highlight"],
        BITABLE_FIELDS["is_recovered"]: record["is_recovered"],
        BITABLE_FIELDS["rank_position"]: record["rank_position"],
        BITABLE_FIELDS["uploaded_at"]: _milliseconds(str(record["uploaded_at"])),
    }
    published_at = str(record.get("published_at", ""))
    if published_at:
        fields[BITABLE_FIELDS["published_at"]] = _milliseconds(published_at)
    stars = record.get("github_total_stars")
    if stars is not None:
        fields[BITABLE_FIELDS["github_total_stars"]] = stars
    return fields


def _batch_url(config: dict[str, str], operation: str, payload_hash: str) -> str:
    url = (
        f"{FEISHU_API_ROOT}/bitable/v1/apps/{config['app_token']}"
        f"/tables/{config['table_id']}/records/{operation}"
    )
    if operation == "batch_create":
        idempotency_basis = (
            f"{config['app_token']}:{config['table_id']}:{payload_hash}"
        )
        digest = hashlib.sha256(idempotency_basis.encode("utf-8")).hexdigest()
        client_token = str(uuid.UUID(digest[:32], version=4))
        return f"{url}?{urllib.parse.urlencode({'client_token': client_token})}"
    return url


def publish_platform_payload(
    payload: dict[str, object],
    receipt_path: Path,
    *,
    dry_run: bool = False,
    requester: FeishuRequester = _feishu_request,
) -> dict[str, object]:
    records = payload.get("records")
    payload_hash = str(payload.get("payload_sha256", ""))
    if not isinstance(records, list) or not records or len(payload_hash) != 64:
        raise ValueError("platform payload is incomplete")
    if dry_run:
        return {
            "status": "dry_run",
            "records": len(records),
            "report_date": payload.get("report_date"),
        }

    validate_platform_configuration()
    config = _configuration()
    target_basis = f"{config['app_token']}:{config['table_id']}"
    target_hash = hashlib.sha256(target_basis.encode("utf-8")).hexdigest()

    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            receipt = {}
        if (
            receipt.get("status") == "published"
            and receipt.get("payload_sha256") == payload_hash
            and receipt.get("target_sha256") == target_hash
        ):
            return {
                "status": "skipped",
                "reason": "matching successful platform receipt exists",
                "records": len(records),
            }

    try:
        token = _tenant_access_token(config, requester)
        existing = _existing_records(
            config, token, str(payload.get("report_date", "")), requester
        )
        creates: list[dict[str, object]] = []
        updates: list[dict[str, object]] = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("platform payload contains an invalid record")
            record_key = str(record.get("record_key", ""))
            fields = _bitable_fields(record)
            if record_key in existing:
                updates.append(
                    {"record_id": existing[record_key], "fields": fields}
                )
            else:
                creates.append({"fields": fields})

        headers = {"Authorization": f"Bearer {token}"}
        if creates:
            _require_success(
                requester(
                    "POST",
                    _batch_url(config, "batch_create", payload_hash),
                    headers,
                    {"records": creates},
                ),
                "create Bitable records",
            )
        if updates:
            _require_success(
                requester(
                    "POST",
                    _batch_url(config, "batch_update", payload_hash),
                    headers,
                    {"records": updates},
                ),
                "update Bitable records",
            )
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        raise RuntimeError(f"Bitable request failed: {type(error).__name__}") from error

    receipt = {
        "status": "published",
        "report_date": payload.get("report_date"),
        "records": len(records),
        "created": len(creates),
        "updated": len(updates),
        "payload_sha256": payload_hash,
        "target_sha256": target_hash,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(receipt_path, receipt)
    return {
        "status": "published",
        "records": len(records),
        "created": len(creates),
        "updated": len(updates),
    }
