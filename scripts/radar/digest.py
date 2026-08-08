"""Validate frozen Markdown and render the maintained native Feishu card."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

MAX_CARD_BYTES = 25_000
SOURCE_ORDER = {
    "official_news": 0,
    "youtube": 1,
    "bilibili": 2,
    "aihot": 3,
    "github_trending": 4,
    "security_advisory": 5,
    "model_hub": 6,
    "industry_digest": 7,
    "builders_x": 8,
}
ITEM_PATTERN = re.compile(
    r"^###\s+\d+\.\s+\[(?P<title>.+)]\((?P<url>https?://[^)]+)\)\s*$"
)
FIELD_PREFIXES = {
    "source": "- 来源：",
    "highlight": "- 重点：",
    "summary": "- 来源摘要：",
    "recommendation": "- 💡 推荐理由：",
}
FORBIDDEN_PREFIXES = ("- 事实摘要：", "- 字幕摘要：")
RECOVERED_PREFIX_RE = re.compile(r"^补录（\d{4}-\d{2}-\d{2}）：")
ARABIC_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?P<scale>thousand|million|billion|万|亿))?",
    re.IGNORECASE,
)
CHINESE_QUANTITY_RE = re.compile(
    r"(?P<number>[零〇一二两三四五六七八九十百]+)"
    r"(?P<scale>万|亿)?\s*"
    r"(?P<unit>项|个|条|类|倍|年|月|天|秒|种|座|单|英里|字符|张|段|帧|美元|欧元|测试)"
)
ENGLISH_NUMBER_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
    r"billion)(?:[ -]+(?:one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"thousand|million|billion))*\b",
    re.IGNORECASE,
)
ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
ENGLISH_NUMBER_VALUES = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
SCALE_VALUES = {
    "thousand": Decimal(1_000),
    "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
    "万": Decimal(10_000),
    "亿": Decimal(100_000_000),
}


@dataclass(frozen=True, slots=True)
class FrozenItem:
    item_id: str
    source_type: str
    source: str
    title: str
    url: str
    summary: str
    recommendation: str
    highlight: bool
    recency_status: str = "current"
    signal_type: str = "general"
    evidence_level: str = "unclassified"
    topics: tuple[str, ...] = ()
    audiences: tuple[str, ...] = ()
    language: str = "en"
    event_id: str = ""
    story_role: str = "primary"
    story_items: int = 1
    source_diversity: int = 1
    verification_status: str = "single_source"
    confidence_score: int = 0
    rank_score: float = 0.0
    rank_reason: tuple[str, ...] = ()
    alert_level: str = "normal"
    change_type: str = "report"


def _english_number(value: str) -> Decimal | None:
    total = 0
    current = 0
    for token in re.split(r"[ -]+", value.casefold()):
        if token in ENGLISH_NUMBER_VALUES:
            current += ENGLISH_NUMBER_VALUES[token]
        elif token == "hundred":
            current = max(current, 1) * 100
        elif token in {"thousand", "million", "billion"}:
            scale = int(SCALE_VALUES[token])
            total += max(current, 1) * scale
            current = 0
        else:
            return None
    return Decimal(total + current)


def _chinese_number(value: str) -> Decimal | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100}
    total = 0
    current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in units:
            total += max(current, 1) * units[char]
            current = 0
        else:
            return None
    return Decimal(total + current)


def _numeric_concepts(value: str) -> set[Decimal]:
    normalized = value.replace("，", ",")
    concepts: set[Decimal] = set()
    for match in ARABIC_QUANTITY_RE.finditer(normalized):
        try:
            number = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            continue
        scale = (match.group("scale") or "").casefold()
        concepts.add(number * SCALE_VALUES.get(scale, 1))
    for match in CHINESE_QUANTITY_RE.finditer(normalized):
        number = _chinese_number(match.group("number"))
        if number is not None:
            if number == 1 and match.group("unit") in {"项", "个", "条", "种"}:
                continue
            concepts.add(number * SCALE_VALUES.get(match.group("scale") or "", 1))
    for match in ENGLISH_NUMBER_RE.finditer(normalized):
        number = _english_number(match.group(0))
        if number is not None:
            concepts.add(number)
    lowered = normalized.casefold()
    concepts.update(
        Decimal(month)
        for name, month in ENGLISH_MONTHS.items()
        if re.search(rf"\b{name}\b", lowered)
    )
    if re.search(r"\bhalf (?:a )?century\b|半个多?世纪", lowered):
        concepts.add(Decimal(50))
    return concepts


def _validate_numeric_evidence(record: dict, summary: str) -> None:
    summary_without_recency = RECOVERED_PREFIX_RE.sub("", summary, count=1)
    unsupported = _numeric_concepts(summary_without_recency) - _numeric_concepts(
        str(record.get("source_text", ""))
    )
    if unsupported:
        values = ", ".join(format(value, "f") for value in sorted(unsupported))
        raise ValueError(
            f"source summary contains unsupported numeric evidence ({values}): "
            f"{record.get('title', '')}"
        )


def _parse_markdown(markdown: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    last_field: str | None = None

    def flush() -> None:
        nonlocal current, last_field
        if current is not None:
            items.append(current)
        current = None
        last_field = None

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        match = ITEM_PATTERN.match(line)
        if match:
            flush()
            current = {"title": match.group("title"), "url": match.group("url")}
            continue
        if current is None:
            continue
        if line.startswith(FORBIDDEN_PREFIXES):
            raise ValueError("frozen digest must use 来源摘要, not 事实摘要 or 字幕摘要")
        matched_field = False
        for key, prefix in FIELD_PREFIXES.items():
            if line.startswith(prefix):
                current[key] = line[len(prefix) :].strip()
                last_field = key
                matched_field = True
                break
        if matched_field or not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            last_field = None
            continue
        if last_field in {"summary", "recommendation"}:
            current[last_field] = f"{current.get(last_field, '')} {line}".strip()
    flush()
    if not items:
        raise ValueError("frozen digest contains no parseable items")
    return items


def validate_frozen_digest(source_payload: dict, markdown: str) -> list[FrozenItem]:
    records = source_payload.get("items")
    if not isinstance(records, list):
        raise ValueError("source payload items must be an array")
    by_url: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not record.get("url"):
            raise ValueError("source payload contains an invalid record")
        url = str(record["url"])
        if url in by_url:
            raise ValueError("source payload contains duplicate URLs")
        by_url[url] = record

    parsed = _parse_markdown(markdown)
    parsed_urls = [item.get("url", "") for item in parsed]
    if len(parsed_urls) != len(set(parsed_urls)):
        raise ValueError("frozen digest contains duplicate URLs")
    if set(parsed_urls) != set(by_url):
        missing = len(set(by_url) - set(parsed_urls))
        invented = len(set(parsed_urls) - set(by_url))
        raise ValueError(f"frozen digest source mismatch: missing={missing}, invented={invented}")

    frozen: list[FrozenItem] = []
    for item in parsed:
        record = by_url[item["url"]]
        required = ("source", "highlight", "summary")
        if any(not item.get(field, "").strip() for field in required):
            raise ValueError("every frozen item requires source, highlight, and source summary")
        if item["title"].strip() != str(record.get("title", "")).strip():
            raise ValueError("frozen digest title differs from source payload")
        if item["source"].strip() != str(record.get("source", "")).strip():
            raise ValueError("frozen digest source differs from source payload")
        if item["highlight"] not in {"是", "否"}:
            raise ValueError("highlight must be 是 or 否")

        status = str(record.get("source_text_status", ""))
        if status == "unavailable":
            expected = f"不可用（{record.get('unavailable_reason', '')}）"
            if item["summary"] != expected:
                raise ValueError("unavailable source must keep its exact unavailable reason")
        elif status == "available":
            if item["summary"].startswith("不可用"):
                raise ValueError("available source requires a source-bounded summary")
            _validate_numeric_evidence(record, item["summary"])
        else:
            raise ValueError("source record has an unknown source_text_status")

        recency_status = str(record.get("recency_status", "current"))
        if recency_status not in {"current", "recovered"}:
            raise ValueError("source record has an unknown recency_status")

        recommendation = item.get("recommendation", "").strip()
        source_recommendation = str(record.get("recommendation", "")).strip()
        if recommendation and recommendation != source_recommendation:
            raise ValueError("recommendation must match the source payload exactly")
        frozen.append(
            FrozenItem(
                item_id=str(record.get("id", "")),
                source_type=str(record.get("source_type", "")),
                source=item["source"].strip(),
                title=item["title"].strip(),
                url=item["url"].strip(),
                summary=item["summary"].strip(),
                recommendation=recommendation,
                highlight=item["highlight"] == "是",
                recency_status=recency_status,
                signal_type=str(record.get("signal_type", "general")),
                evidence_level=str(record.get("evidence_level", "unclassified")),
                topics=tuple(
                    str(topic)
                    for topic in record.get("topics", [])
                    if isinstance(topic, str)
                ),
                audiences=tuple(
                    str(audience)
                    for audience in record.get("audiences", [])
                    if isinstance(audience, str)
                ),
                language=str(record.get("language", "en")),
                event_id=str(record.get("event_id", "")),
                story_role=str(record.get("story_role", "primary")),
                story_items=int(record.get("story_items", 1)),
                source_diversity=int(record.get("source_diversity", 1)),
                verification_status=str(
                    record.get("verification_status", "single_source")
                ),
                confidence_score=int(record.get("confidence_score", 0)),
                rank_score=float(record.get("rank_score", 0)),
                rank_reason=tuple(
                    str(reason)
                    for reason in record.get("rank_reason", [])
                    if isinstance(reason, str)
                ),
                alert_level=str(record.get("alert_level", "normal")),
                change_type=str(record.get("change_type", "report")),
            )
        )
    return frozen


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _safe_title(item: FrozenItem) -> str:
    return _clean_text(item.title).replace("[", "").replace("]", "")


def _source_label(item: FrozenItem) -> str:
    return _clean_text(item.source.split("·", 1)[-1])


def _summary(value: str) -> str:
    text = value.strip()
    numbered = re.split(r"(?:^|\n)\s*\d+[.、]\s*", text)
    points = [_clean_text(part) for part in numbered if _clean_text(part)]
    if len(points) > 1 or re.match(r"^\s*1[.、]", text):
        return "\n".join(f"• {point}" for point in points)
    return text


def _item_markdown(item: FrozenItem, index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    lines = [
        f"**{prefix}[{_safe_title(item)}]({item.url})**",
        f"<font color='grey'>{_source_label(item)}</font>",
        "**来源摘要**",
        _summary(item.summary),
    ]
    if item.recommendation:
        lines.extend(["**💡 推荐理由**", item.recommendation])
    return "\n".join(lines)


def _collapsible(title: str, items: list[FrozenItem], element_id: str) -> dict:
    return {
        "tag": "collapsible_panel",
        "element_id": element_id,
        "expanded": False,
        "header": {
            "title": {"tag": "plain_text", "content": title},
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
        "padding": "8px 10px 8px 10px",
        "elements": [
            {"tag": "markdown", "content": _item_markdown(item)} for item in items
        ],
    }


def _source_section(
    label: str,
    icon: str,
    items: list[FrozenItem],
    remaining_label: str,
    element_id: str,
) -> list[dict]:
    if not items:
        return []
    highlights = [
        item for item in items if item.highlight and item.recency_status == "current"
    ]
    remaining = [
        item for item in items if not item.highlight and item.recency_status == "current"
    ]
    recovered = [item for item in items if item.recency_status == "recovered"]
    elements: list[dict] = [
        {
            "tag": "markdown",
            "text_size": "section_heading",
            "content": (
                f"**{icon} {label}**\n"
                f"<font color='grey'>AI 判断 {len(highlights)} 条重点 · "
                f"共 {len(items)} 条</font>"
            ),
        }
    ]
    elements.extend(
        {"tag": "markdown", "content": _item_markdown(item, index)}
        for index, item in enumerate(highlights, start=1)
    )
    if remaining:
        elements.append(
            _collapsible(
                f"其余 {len(remaining)} 条 {remaining_label}",
                remaining,
                element_id,
            )
        )
    if recovered:
        elements.append(
            _collapsible(
                f"补录 {len(recovered)} 条 {remaining_label}",
                recovered,
                f"{element_id}_recovered",
            )
        )
    return elements


def build_card(date_str: str, items: list[FrozenItem]) -> dict:
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("date must be YYYY-MM-DD") from error
    official_news = [item for item in items if item.source_type == "official_news"]
    youtube = [item for item in items if item.source_type == "youtube"]
    bilibili = [item for item in items if item.source_type == "bilibili"]
    aihot = [item for item in items if item.source_type == "aihot"]
    github_trending = [item for item in items if item.source_type == "github_trending"]
    security_advisory = [
        item for item in items if item.source_type == "security_advisory"
    ]
    model_hub = [item for item in items if item.source_type == "model_hub"]
    industry_digest = [
        item for item in items if item.source_type == "industry_digest"
    ]
    builders_x = [item for item in items if item.source_type == "builders_x"]
    if (
        len(official_news)
        + len(youtube)
        + len(bilibili)
        + len(aihot)
        + len(github_trending)
        + len(security_advisory)
        + len(model_hub)
        + len(industry_digest)
        + len(builders_x)
        != len(items)
    ):
        raise ValueError("card contains an unsupported source type")
    highlights = [item for item in items if item.highlight]
    current_count = sum(item.recency_status == "current" for item in items)
    recovered_count = len(items) - current_count
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"**本卡 {len(items)} 条信号**　当期 {current_count} · "
                f"补录 {recovered_count}\n"
                f"官方 {len(official_news)} · "
                f"YouTube {len(youtube)} · "
                f"B站 {len(bilibili)} · "
                f"AIHOT {len(aihot)} · GitHub {len(github_trending)} · "
                f"安全 {len(security_advisory)} · 模型 {len(model_hub)} · "
                f"行业精选 {len(industry_digest)} · "
                f"X {len(builders_x)}\n"
                f"<font color='grey'>模型判断 {len(highlights)} 条重点，其余按需展开</font>"
            ),
        }
    ]
    elements.extend(
        _source_section(
            "官方发布",
            "📡",
            official_news,
            "官方动态",
            "official_news_more",
        )
    )
    elements.extend(
        _source_section(
            "YouTube",
            "🎬",
            youtube,
            "YouTube 视频",
            "youtube_more",
        )
    )
    elements.extend(
        _source_section(
            "哔哩哔哩",
            "📺",
            bilibili,
            "B站投稿",
            "bilibili_more",
        )
    )
    elements.extend(
        _source_section(
            "AIHOT",
            "🧭",
            aihot,
            "AIHOT 动态",
            "aihot_more",
        )
    )
    elements.extend(
        _source_section(
            "GitHub 开源雷达",
            "🧩",
            github_trending,
            "GitHub 热门项目",
            "github_trending_more",
        )
    )
    elements.extend(
        _source_section(
            "AI 安全雷达",
            "🛡️",
            security_advisory,
            "安全公告",
            "security_advisory_more",
        )
    )
    elements.extend(
        _source_section(
            "模型 Hub 雷达",
            "🧠",
            model_hub,
            "新模型",
            "model_hub_more",
        )
    )
    elements.extend(
        _source_section(
            "行业精选",
            "📰",
            industry_digest,
            "行业周报",
            "industry_digest_more",
        )
    )
    elements.extend(
        _source_section(
            "Builders X",
            "💬",
            builders_x,
            "Builders X 动态",
            "builders_x_more",
        )
    )
    elements.append(
        {
            "tag": "markdown",
            "content": "<font color='grey'>AI News Skills · 重点优先，其余按需展开</font>",
        }
    )
    display_date = f"{parsed_date.year}年{parsed_date.month}月{parsed_date.day}日"
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "style": {
                "text_size": {
                    "section_heading": {
                        "default": "heading",
                        "pc": "heading",
                        "mobile": "heading",
                    }
                }
            },
        },
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": f"📗 AI 前哨｜{display_date}"},
        },
        "body": {
            "direction": "vertical",
            "vertical_spacing": "12px",
            "padding": "12px 12px 12px 12px",
            "elements": elements,
        },
    }


def build_cards(date_str: str, items: list[FrozenItem]) -> list[dict]:
    if not items:
        raise ValueError("cannot build a card without items")
    ordered_items = sorted(
        enumerate(items),
        key=lambda pair: (SOURCE_ORDER.get(pair[1].source_type, len(SOURCE_ORDER)), pair[0]),
    )
    cards: list[dict] = []
    current: list[FrozenItem] = []
    for _, item in ordered_items:
        candidate = [*current, item]
        candidate_card = build_card(date_str, candidate)
        size = len(json.dumps(candidate_card, ensure_ascii=False).encode("utf-8"))
        if current and size > MAX_CARD_BYTES:
            cards.append(build_card(date_str, current))
            current = [item]
        else:
            current = candidate
    if current:
        card = build_card(date_str, current)
        if len(json.dumps(card, ensure_ascii=False).encode("utf-8")) > MAX_CARD_BYTES:
            raise ValueError("one digest item exceeds the Feishu card size limit")
        cards.append(card)
    return cards
