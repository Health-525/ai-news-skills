"""Offline smoke tests for source quality, digest validation, and card rendering."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_pipeline import _handle_scheduled_group
from radar.approval import build_approval_card
from radar.delivery import _parse_bridge_payload, send_group_cards, send_personal_cards
from radar.digest import FrozenItem, build_card, build_cards, validate_frozen_digest
from radar.models import ContentItem
from radar.official_news import (
    OfficialSource,
    fetch_official_news,
    load_official_sources,
    parse_official_feed,
)
from radar.url_utils import canonical_url
from radar.source_material import source_text_status
from radar.sources import load_builders_x_accounts, parse_builders_x
from radar.storage import Storage
from radar.subscriptions import build_subscription_result_card, validate_batch
from radar.workflow import artifact_paths, doctor, scheduled_group_delivery_enabled


def main() -> int:
    status, text, reason = source_text_status(
        "This release introduces a concrete agent evaluation workflow with reproducible metrics."
    )
    assert status == "available" and text and not reason
    status, text, reason = source_text_status("Subscribe now https://example.com")
    assert status == "unavailable" and not text and reason
    assert reason == "来源未提供足够的可用简介"

    official_sources = load_official_sources(
        Path(__file__).resolve().parents[1] / "references" / "official-news-sources.json"
    )
    assert len(official_sources) == 8
    assert canonical_url(
        "https://www.example.com/news/model/?utm_source=test&ref=home"
    ) == "https://example.com/news/model"
    official_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Official Model One</title>
    <description><![CDATA[<p>A detailed official model announcement with concrete capabilities.</p>]]></description>
    <link>https://example.com/news/model-one</link>
    <guid>model-one</guid>
    <pubDate>Tue, 21 Jul 2026 06:00:00 GMT</pubDate>
    <category>Product</category>
  </item>
  <item>
    <title>Old Model</title>
    <description>Outside the collection window.</description>
    <link>https://example.com/news/old-model</link>
    <guid>old-model</guid>
    <pubDate>Sun, 19 Jul 2026 06:00:00 GMT</pubDate>
  </item>
</channel></rss>"""
    parsed_official = parse_official_feed(
        official_rss,
        {
            "name": "Example Lab",
            "kind": "rss",
            "url": "https://example.com/rss.xml",
            "allowed_hosts": ["example.com"],
        },
        datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert len(parsed_official) == 1
    assert parsed_official[0].source_type == "official_news"
    assert parsed_official[0].raw_source_text.startswith("A detailed official")

    source = {
        "items": [
            {
                "id": "video-1",
                "source_type": "youtube",
                "source": "YouTube · Example",
                "title": "Example update",
                "url": "https://www.youtube.com/watch?v=video-1",
                "source_text_status": "available",
                "source_text": "A sufficiently detailed publisher description for testing.",
                "unavailable_reason": "",
                "recommendation": "",
            },
            {
                "id": "item-2",
                "source_type": "aihot",
                "source": "AIHOT · Example",
                "title": "Unavailable update",
                "url": "https://example.com/item-2",
                "source_text_status": "unavailable",
                "source_text": "",
                "unavailable_reason": "RSS 未提供足够的可用简介",
                "recommendation": "",
            },
        ]
    }
    markdown = """# AI 前哨 | 2026-07-20

### 1. [Example update](https://www.youtube.com/watch?v=video-1)
- 来源：YouTube · Example
- 重点：是
- 来源摘要：该来源介绍了一套可复现指标的智能体评估流程。

### 2. [Unavailable update](https://example.com/item-2)
- 来源：AIHOT · Example
- 重点：否
- 来源摘要：不可用（RSS 未提供足够的可用简介）
"""
    items = validate_frozen_digest(source, markdown)
    cards = build_cards("2026-07-20", items)
    assert len(items) == 2 and len(cards) == 1
    now = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    accounts = [{"name": "Swyx", "handle": "swyx"}]
    x_payload = {
        "generatedAt": "2026-07-21T07:10:35Z",
        "x": [
            {
                "handle": "swyx",
                "tweets": [
                    {
                        "id": "1001",
                        "text": (
                            "This AI agent evaluation compares model inference quality "
                            "with reproducible benchmark results."
                        ),
                        "createdAt": "2026-07-21T06:00:00Z",
                        "url": "https://x.com/swyx/status/1001",
                        "likes": 12,
                        "retweets": 3,
                        "replies": 2,
                    },
                    {
                        "id": "1002",
                        "text": "A long personal update about travel, food, and a quiet weekend at home.",
                        "createdAt": "2026-07-21T05:00:00Z",
                        "url": "https://x.com/swyx/status/1002",
                    },
                    {
                        "id": "1003",
                        "text": "https://t.co/example",
                        "createdAt": "2026-07-21T04:00:00Z",
                        "url": "https://x.com/swyx/status/1003",
                    },
                    {
                        "id": "1004",
                        "text": "This AI model update is detailed enough but outside the collection window.",
                        "createdAt": "2026-07-19T04:00:00Z",
                        "url": "https://x.com/swyx/status/1004",
                    },
                    {
                        "id": "1005",
                        "text": (
                            "We're hiring a senior engineer to build our new AI agent product. "
                            "Apply here and join our team."
                        ),
                        "createdAt": "2026-07-21T03:00:00Z",
                        "url": "https://x.com/swyx/status/1005",
                    },
                    {
                        "id": "1006",
                        "text": (
                            "Gemini Flash is widely adopted by enterprises for its combination "
                            "of price, intelligence, and speed."
                        ),
                        "createdAt": "2026-07-20T12:00:00Z",
                        "url": "https://x.com/swyx/status/1006",
                    },
                ],
            },
            {
                "handle": "not-allowlisted",
                "tweets": [],
            },
        ],
    }
    x_items, x_stats = parse_builders_x(
        x_payload, accounts, now - timedelta(hours=24), now
    )
    assert len(x_items) == 2
    assert {item.item_id for item in x_items} == {"1001", "1006"}
    assert all(item.source_type == "builders_x" for item in x_items)
    assert x_stats == {
        "posts": 6,
        "accepted": 2,
        "filtered": 4,
        "unknown_accounts": 1,
        "invalid": 0,
        "outside_snapshot": 1,
        "too_short": 1,
        "no_ai_topic": 1,
        "no_signal": 0,
        "promotion": 1,
    }
    x_card = build_card(
        "2026-07-20",
        [
            FrozenItem(
                item_id="1001",
                source_type="builders_x",
                source="Builders X · Swyx (@swyx)",
                title="Swyx：AI agent evaluation update",
                url="https://x.com/swyx/status/1001",
                summary="该作者介绍了一项可复现的 AI 智能体评测。",
                recommendation="",
                highlight=False,
            )
        ],
    )
    x_card_text = json.dumps(x_card, ensure_ascii=False)
    assert "X 1" in x_card_text and "Builders X 动态" in x_card_text
    section_items = [
        FrozenItem(
            item_id="official-highlight",
            source_type="official_news",
            source="官方发布 · Example Lab",
            title="Official priority",
            url="https://example.com/news/priority",
            summary="Official priority summary",
            recommendation="",
            highlight=True,
        ),
        FrozenItem(
            item_id="official-remaining",
            source_type="official_news",
            source="官方发布 · Example Lab",
            title="Official remaining",
            url="https://example.com/news/remaining",
            summary="Official remaining summary",
            recommendation="",
            highlight=False,
        ),
        FrozenItem(
            item_id="x-highlight",
            source_type="builders_x",
            source="Builders X · Example",
            title="X priority",
            url="https://x.com/example/status/2001",
            summary="X priority summary",
            recommendation="",
            highlight=True,
        ),
        FrozenItem(
            item_id="youtube-remaining",
            source_type="youtube",
            source="YouTube · Example",
            title="YouTube remaining",
            url="https://www.youtube.com/watch?v=remaining",
            summary="YouTube remaining summary",
            recommendation="",
            highlight=False,
        ),
        FrozenItem(
            item_id="aihot-highlight",
            source_type="aihot",
            source="AIHOT · Example",
            title="AIHOT priority",
            url="https://example.com/aihot-priority",
            summary="AIHOT priority summary",
            recommendation="",
            highlight=True,
        ),
        FrozenItem(
            item_id="youtube-highlight",
            source_type="youtube",
            source="YouTube · Example",
            title="YouTube priority",
            url="https://www.youtube.com/watch?v=priority",
            summary="YouTube priority summary",
            recommendation="",
            highlight=True,
        ),
        FrozenItem(
            item_id="x-remaining",
            source_type="builders_x",
            source="Builders X · Example",
            title="X remaining",
            url="https://x.com/example/status/2002",
            summary="X remaining summary",
            recommendation="",
            highlight=False,
        ),
        FrozenItem(
            item_id="aihot-remaining",
            source_type="aihot",
            source="AIHOT · Example",
            title="AIHOT remaining",
            url="https://example.com/aihot-remaining",
            summary="AIHOT remaining summary",
            recommendation="",
            highlight=False,
        ),
    ]
    section_card = build_card("2026-07-20", section_items)
    section_elements = section_card["body"]["elements"]

    def element_position(marker: str) -> int:
        for index, element in enumerate(section_elements):
            if element.get("tag") == "markdown":
                value = element.get("content", "")
            else:
                value = element.get("header", {}).get("title", {}).get("content", "")
            if marker in value:
                return index
        raise AssertionError(f"card marker not found: {marker}")

    assert (
        element_position("**📡 官方发布**")
        < element_position("Official priority")
        < element_position("其余 1 条 官方动态")
        < element_position("**🎬 YouTube**")
        < element_position("YouTube priority")
        < element_position("其余 1 条 YouTube 视频")
        < element_position("**🧭 AIHOT**")
        < element_position("AIHOT priority")
        < element_position("其余 1 条 AIHOT 动态")
        < element_position("**💬 Builders X**")
        < element_position("X priority")
        < element_position("其余 1 条 Builders X 动态")
    )
    long_summary = "signal " * 2_200
    split_items = [
        FrozenItem(
            item_id=f"split-{source_type}",
            source_type=source_type,
            source=source,
            title=f"{source_type} split",
            url=url,
            summary=long_summary,
            recommendation="",
            highlight=True,
        )
        for source_type, source, url in (
            ("official_news", "官方发布 · Example", "https://example.com/news/split"),
            ("builders_x", "Builders X · Example", "https://x.com/example/status/3001"),
            ("aihot", "AIHOT · Example", "https://example.com/split-aihot"),
            ("youtube", "YouTube · Example", "https://www.youtube.com/watch?v=split"),
        )
    ]
    split_cards = build_cards("2026-07-20", split_items)
    assert len(split_cards) == 4
    split_text = [json.dumps(card, ensure_ascii=False) for card in split_cards]
    assert "📡 官方发布" in split_text[0]
    assert "🎬 YouTube" in split_text[1]
    assert "🧭 AIHOT" in split_text[2]
    assert "💬 Builders X" in split_text[3]
    try:
        parse_builders_x(
            {"generatedAt": "2026-07-18T00:00:00Z", "x": []},
            accounts,
            now - timedelta(hours=24),
            now,
        )
    except ValueError as error:
        assert "stale" in str(error)
    else:
        raise AssertionError("a stale Builders X feed was accepted")
    reference_accounts = load_builders_x_accounts(
        Path(__file__).resolve().parents[1] / "references" / "builders-x-accounts.json"
    )
    assert len(reference_accounts) == 26
    assert _parse_bridge_payload('OpenClaw log\n{"status":"sent"}\n') == {"status": "sent"}
    approval_card = build_approval_card("digest-test")
    assert "通过日报 digest-test" in approval_card["body"]["elements"][0]["content"]
    original_auto_group = os.environ.get("AI_NEWS_AUTO_GROUP_DELIVERY")
    os.environ.pop("AI_NEWS_AUTO_GROUP_DELIVERY", None)
    assert not scheduled_group_delivery_enabled()
    os.environ["AI_NEWS_AUTO_GROUP_DELIVERY"] = "1"
    assert scheduled_group_delivery_enabled()
    os.environ["AI_NEWS_AUTO_GROUP_DELIVERY"] = "false"
    assert not scheduled_group_delivery_enabled()
    if original_auto_group is None:
        os.environ.pop("AI_NEWS_AUTO_GROUP_DELIVERY", None)
    else:
        os.environ["AI_NEWS_AUTO_GROUP_DELIVERY"] = original_auto_group

    original_state = os.environ.get("AI_NEWS_STATE_DIR")
    original_group_target = os.environ.get("AI_NEWS_FEISHU_GROUP_TARGET")
    original_auto_group = os.environ.get("AI_NEWS_AUTO_GROUP_DELIVERY")
    with tempfile.TemporaryDirectory() as temporary:
        os.environ["AI_NEWS_STATE_DIR"] = temporary
        paths = artifact_paths("2026-07-20")
        assert paths["state"] == Path(temporary)
        paths["source"].parent.mkdir(parents=True, exist_ok=True)
        paths["source"].write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        paths["digest"].write_text(markdown, encoding="utf-8")
        os.environ["AI_NEWS_FEISHU_GROUP_TARGET"] = "group"
        os.environ["AI_NEWS_AUTO_GROUP_DELIVERY"] = "1"
        output = io.StringIO()
        with redirect_stdout(output):
            scheduled_code = _handle_scheduled_group("2026-07-20", dry_run=True)
        scheduled_result = json.loads(output.getvalue())
        assert scheduled_code == 0
        assert scheduled_result == {
            "status": "dry_run",
            "target_type": "group",
            "cards": len(cards),
            "delivery_mode": "scheduled_group",
        }
        target = "private-test-target"
        cards_bytes = json.dumps(cards, ensure_ascii=False, sort_keys=True).encode("utf-8")
        target_hash = hashlib.sha256(target.encode("utf-8")).hexdigest()
        cards_hash = hashlib.sha256(cards_bytes).hexdigest()
        card_hashes = [
            hashlib.sha256(
                json.dumps(card, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            for card in cards
        ]
        paths["receipt"].parent.mkdir(parents=True, exist_ok=True)
        paths["receipt"].write_text(
            json.dumps(
                {
                    "status": "sent",
                    "target_hash": target_hash,
                    "cards_sha256": cards_hash,
                    "sent_card_hashes": card_hashes,
                }
            ),
            encoding="utf-8",
        )
        code, receipt_result = send_personal_cards(
            cards,
            target,
            paths["receipt"],
            Path(temporary) / "unused.mjs",
        )
        assert code == 0 and receipt_result["status"] == "skipped"

        storage = Storage(Path(temporary))
        storage.initialize()
        html_source: OfficialSource = {
            "name": "Example Lab",
            "kind": "html_index",
            "index_url": "https://example.com/news",
            "article_path_prefix": "/news/",
            "excluded_path_prefixes": [],
            "allowed_hosts": ["example.com"],
            "max_candidates": 5,
        }
        html_index = (
            '<a href="/news/model-two">Jul 21, 2026 Official Model Two '
            "A concrete official model description.</a>"
        ).encode()
        html_article = b"""<html><head>
          <meta property="og:title" content="Official Model Two">
          <meta property="og:description" content="A concrete official model description with capabilities.">
          <script type="application/ld+json">{"datePublished":"2026-07-21T08:00:00Z"}</script>
        </head></html>"""

        def fake_official_fetcher(url: str, _: Storage) -> tuple[bytes, bool]:
            if url == "https://example.com/news":
                return html_index, False
            if url == "https://example.com/news/model-two":
                return html_article, False
            raise OSError("unavailable")

        html_items, html_health = fetch_official_news(
            [html_source],
            datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            storage,
            fake_official_fetcher,
        )
        assert len(html_items) == 1 and html_health.status == "ok"
        assert html_items[0].title == "Official Model Two"
        official_duplicate = ContentItem(
            item_id="official-duplicate",
            source_type="official_news",
            source="官方发布 · Example",
            title="Official duplicate",
            published_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            url="https://example.com/news/shared/",
            raw_source_text="Official source text with enough concrete detail for summarization.",
        )
        aihot_duplicate = ContentItem(
            item_id="aihot-duplicate",
            source_type="aihot",
            source="AIHOT · Example",
            title="AIHOT duplicate",
            published_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            url="https://example.com/news/shared",
            raw_source_text="Secondary source text for the same canonical announcement.",
        )
        storage.add_new_items_to_digest(
            "2026-07-23", [official_duplicate, aihot_duplicate]
        )
        duplicate_items = storage.items_for_digest("2026-07-23")
        assert len(duplicate_items) == 1
        assert duplicate_items[0].source_type == "official_news"
        storage.add_new_items_to_digest("2026-07-21", x_items)
        storage.add_new_items_to_digest("2026-07-22", x_items)
        assert len(storage.items_for_digest("2026-07-21")) == 2
        assert storage.items_for_digest("2026-07-22") == []
        existing_id = "UC" + "B" * 22
        new_id = "UC" + "A" * 22
        storage.seed_subscriptions([{"name": "Existing", "channel_id": existing_id}])

        def fake_fetcher(url: str) -> bytes:
            channel_id = url.rsplit("=", 1)[-1]
            title = "New Channel" if channel_id == new_id else "Existing"
            return (
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<feed xmlns='http://www.w3.org/2005/Atom' "
                "xmlns:yt='http://www.youtube.com/xml/schemas/2015'>"
                f"<yt:channelId>{channel_id}</yt:channelId><title>{title}</title></feed>"
            ).encode("utf-8")

        results = validate_batch(f"{new_id}\n{existing_id}\nnot-youtube", storage, fetcher=fake_fetcher)
        assert [result["status"] for result in results] == ["valid", "duplicate", "invalid"]
        proposal_id = storage.create_subscription_proposal("owner", results)
        result_card = build_subscription_result_card(proposal_id, results)
        assert result_card["schema"] == "2.0"
        assert all(element.get("tag") != "action" for element in result_card["body"]["elements"])
        try:
            storage.confirm_subscription_proposal("someone-else", proposal_id)
        except ValueError:
            pass
        else:
            raise AssertionError("a different requester confirmed a proposal")
        confirmed_id, added = storage.confirm_subscription_proposal("owner", proposal_id)
        assert confirmed_id == proposal_id and added == 1
        assert len(storage.active_channels()) == 2

        draft_id, created = storage.create_digest_draft(
            "2026-07-20", "owner", "group", cards
        )
        duplicate_id, duplicate_created = storage.create_digest_draft(
            "2026-07-20", "owner", "group", cards
        )
        assert created and not duplicate_created and duplicate_id == draft_id
        try:
            storage.claim_digest_draft("someone-else", "group", draft_id)
        except ValueError:
            pass
        else:
            raise AssertionError("a different requester claimed a digest")
        claimed = storage.claim_digest_draft("owner", "group", draft_id)
        assert claimed["draft_id"] == draft_id and claimed["cards"] == cards
        storage.mark_digest_sent(draft_id)
        assert storage.digest_draft_status("owner", "group", draft_id) == "sent"

        group_receipt = Path(temporary) / "group-receipt.json"
        group_receipt.write_text(
            json.dumps(
                {
                    "status": "sent",
                    "target_hash": hashlib.sha256(b"group").hexdigest(),
                    "cards_sha256": cards_hash,
                    "sent_card_hashes": card_hashes,
                }
            ),
            encoding="utf-8",
        )
        code, group_result = send_group_cards(
            cards, "group", group_receipt, Path(temporary) / "unused.mjs"
        )
        assert code == 0 and group_result == {
            "status": "skipped",
            "reason": "matching success receipt already exists",
            "target_type": "group",
            "cards": len(cards),
        }
    if original_state is None:
        os.environ.pop("AI_NEWS_STATE_DIR", None)
    else:
        os.environ["AI_NEWS_STATE_DIR"] = original_state
    if original_group_target is None:
        os.environ.pop("AI_NEWS_FEISHU_GROUP_TARGET", None)
    else:
        os.environ["AI_NEWS_FEISHU_GROUP_TARGET"] = original_group_target
    if original_auto_group is None:
        os.environ.pop("AI_NEWS_AUTO_GROUP_DELIVERY", None)
    else:
        os.environ["AI_NEWS_AUTO_GROUP_DELIVERY"] = original_auto_group

    diagnostics = doctor()
    assert diagnostics["status"] == "ok", json.dumps(diagnostics, ensure_ascii=False)
    print(json.dumps({"status": "ok", "tests": 29}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
