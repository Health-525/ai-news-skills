"""Validate release notes and build a deterministic Feishu update card."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_CARD_BYTES = 20_000


def _text(value: object, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"release manifest {field} must be a string")
    normalized = re.sub(r"\s+", " ", value).strip()
    if not minimum <= len(normalized) <= maximum:
        raise ValueError(
            f"release manifest {field} must contain {minimum} through {maximum} characters"
        )
    if any(ord(char) < 32 for char in normalized):
        raise ValueError(f"release manifest {field} contains control characters")
    return normalized


def _text_list(
    value: object,
    field: str,
    minimum_items: int,
    maximum_items: int,
    maximum_length: int,
) -> list[str]:
    if not isinstance(value, list) or not minimum_items <= len(value) <= maximum_items:
        raise ValueError(
            f"release manifest {field} must contain {minimum_items} through {maximum_items} items"
        )
    return [
        _text(item, f"{field}[{index}]", 3, maximum_length)
        for index, item in enumerate(value)
    ]


def load_release_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid release manifest: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("release manifest requires schema_version 1")
    version = str(payload.get("version", "")).strip().casefold()
    if not VERSION_RE.fullmatch(version):
        raise ValueError("release manifest version must be a full 40-character Git commit")
    return {
        "schema_version": 1,
        "version": version,
        "title": _text(payload.get("title"), "title", 4, 80),
        "summary": _text(payload.get("summary"), "summary", 10, 300),
        "changes": _text_list(payload.get("changes"), "changes", 1, 8, 180),
        "verification": _text_list(
            payload.get("verification"), "verification", 1, 6, 140
        ),
    }


def build_release_card(
    manifest: dict[str, object],
    deployed_at: datetime | None = None,
) -> dict:
    deployed_at = deployed_at or datetime.now(ZoneInfo("Asia/Shanghai"))
    if deployed_at.tzinfo is None:
        deployed_at = deployed_at.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    version = str(manifest["version"])
    changes = manifest["changes"]
    verification = manifest["verification"]
    if not isinstance(changes, list) or not isinstance(verification, list):
        raise ValueError("release manifest lists are invalid")
    change_text = "\n".join(f"{index}. {item}" for index, item in enumerate(changes, start=1))
    verification_text = "\n".join(f"- {item}" for item in verification)
    card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "AI News Skills · 更新公告"},
            "subtitle": {
                "tag": "plain_text",
                "content": deployed_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
                    "%Y-%m-%d %H:%M 生效"
                ),
            },
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**{manifest['title']}**\n"
                        f"<font color='grey'>版本 `{version[:7]}` · 已完成生产发布</font>\n\n"
                        f"{manifest['summary']}"
                    ),
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "text_size": "heading",
                    "content": "**本次更新**",
                },
                {"tag": "markdown", "content": change_text},
                {
                    "tag": "collapsible_panel",
                    "expanded": False,
                    "header": {
                        "title": {"tag": "plain_text", "content": "发布验证"},
                        "vertical_align": "center",
                        "icon": {
                            "tag": "standard_icon",
                            "token": "down-small-ccm_outlined",
                            "size": "16px 16px",
                        },
                        "icon_position": "right",
                        "icon_expanded_angle": -180,
                    },
                    "border": {"color": "grey", "corner_radius": "8px"},
                    "elements": [{"tag": "markdown", "content": verification_text}],
                },
                {
                    "tag": "markdown",
                    "content": (
                        "<font color='grey'>后续日报将自动使用该版本；"
                        "同一版本公告只发送一次。</font>"
                    ),
                },
            ]
        },
    }
    encoded = json.dumps(card, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_CARD_BYTES:
        raise ValueError("release announcement exceeds the card size limit")
    return card
