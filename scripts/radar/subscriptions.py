"""Batch YouTube channel validation and native Feishu workflow cards."""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from xml.etree import ElementTree as ET

from .sources import CHANNEL_ID_RE, NS, USER_AGENT
from .storage import Storage

MAX_BATCH_SIZE = 50
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
CHANNEL_ID_PATTERNS = (
    re.compile(r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"'),
    re.compile(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"'),
    re.compile(r'itemprop="channelId"\s+content="(UC[A-Za-z0-9_-]{22})"'),
)


def extract_inputs(text: str) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        for token in re.split(r"[\s,，;；]+", line.strip()):
            value = token.strip("<>[](){}\"'")
            if value:
                values.append(value)
    if not values:
        raise ValueError("未找到频道链接或频道 ID")
    if len(values) > MAX_BATCH_SIZE:
        raise ValueError(f"一次最多校验 {MAX_BATCH_SIZE} 个频道")
    return values


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html, application/atom+xml, */*"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def resolve_channel_id(value: str, fetcher: Callable[[str], bytes] = _fetch) -> str:
    if CHANNEL_ID_RE.fullmatch(value):
        return value
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise ValueError("仅支持 YouTube 频道链接或频道 ID")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "channel" and CHANNEL_ID_RE.fullmatch(parts[1]):
        return parts[1]
    if not parts or not (parts[0].startswith("@") or parts[0] in {"c", "user"}):
        raise ValueError("请提供频道主页链接，不要提供视频链接")
    canonical = urllib.parse.urlunparse(("https", "www.youtube.com", parsed.path, "", "", ""))
    page = fetcher(canonical).decode("utf-8", errors="replace")
    for pattern in CHANNEL_ID_PATTERNS:
        match = pattern.search(page)
        if match:
            return match.group(1)
    raise ValueError("无法从频道主页识别频道 ID")


def inspect_channel(
    channel_id: str,
    fetcher: Callable[[str], bytes] = _fetch,
) -> dict[str, str]:
    feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + urllib.parse.quote(channel_id)
    root = ET.fromstring(fetcher(feed_url))
    feed_channel_id = root.findtext("yt:channelId", default="", namespaces=NS).strip()
    title = html.unescape(root.findtext("atom:title", default="", namespaces=NS).strip())
    if feed_channel_id not in {channel_id, channel_id.removeprefix("UC")} or not title:
        raise ValueError("YouTube RSS 未返回有效频道信息")
    return {
        "channel_id": channel_id,
        "name": title,
        "channel_url": f"https://www.youtube.com/channel/{channel_id}",
    }


def validate_batch(
    text: str,
    storage: Storage,
    *,
    fetcher: Callable[[str], bytes] = _fetch,
) -> list[dict[str, str]]:
    existing = storage.subscription_ids()
    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for raw_value in extract_inputs(text):
        result = {"input": raw_value[:300], "status": "invalid", "reason": ""}
        try:
            channel_id = resolve_channel_id(raw_value, fetcher)
            channel = inspect_channel(channel_id, fetcher)
        except (ValueError, ET.ParseError) as error:
            result["reason"] = str(error)
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
            result["status"] = "unavailable"
            result["reason"] = "YouTube 当前不可访问，未加入候选"
        else:
            result.update(channel)
            if channel_id in existing:
                result["status"] = "duplicate"
                result["reason"] = "已在订阅列表中"
            elif channel_id in seen:
                result["status"] = "duplicate"
                result["reason"] = "本次提交中重复"
            else:
                result["status"] = "valid"
                result["reason"] = "等待确认"
                seen.add(channel_id)
        results.append(result)
    return results


def build_subscription_form_card() -> dict:
    return {
        "schema": "2.0",
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "新增 YouTube 订阅"},
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "请直接回复这张卡片发送频道主页链接，**每行一个，可一次发送多个**。\n"
                        "支持 `youtube.com/@name`、`/channel/UC...` 或频道 ID；一次最多 50 个。\n\n"
                        "系统会逐项验证 RSS、标出有效/重复/失败项，再等待你确认后写入订阅库。"
                    ),
                }
            ]
        },
    }


def _result_line(index: int, result: dict[str, str]) -> str:
    labels = {"valid": "✅ 有效", "duplicate": "↩️ 重复", "invalid": "❌ 无效", "unavailable": "⚠️ 暂不可用"}
    name = result.get("name") or result.get("input", "")
    return f"{index}. **{labels.get(result['status'], result['status'])}** · {name}\n   {result.get('reason', '')}"


def build_subscription_result_card(proposal_id: str, results: list[dict[str, str]]) -> dict:
    counts = {status: sum(item["status"] == status for item in results) for status in ("valid", "duplicate", "invalid", "unavailable")}
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"共 {len(results)} 项：有效 **{counts['valid']}** · 重复 {counts['duplicate']} · "
                f"无效 {counts['invalid']} · 暂不可用 {counts['unavailable']}\n\n"
                + "\n\n".join(_result_line(index, item) for index, item in enumerate(results, 1))
            ),
        }
    ]
    if counts["valid"]:
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    f"确认添加：`确认添加有效项 {proposal_id}`\n"
                    f"取消本次：`取消订阅候选 {proposal_id}`"
                ),
            }
        )
    return {
        "schema": "2.0",
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "订阅批量校验结果"},
        },
        "body": {"elements": elements},
    }
