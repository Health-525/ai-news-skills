"""Offline smoke tests for source quality, digest validation, and card rendering."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_pipeline import _handle_scheduled_group
from radar.approval import build_approval_card
from radar.delivery import _parse_bridge_payload, send_group_cards, send_personal_cards
from radar.digest import FrozenItem, build_card, build_cards, validate_frozen_digest
from radar.github_radar import (
    GitHubRateLimitError,
    fetch_github_trending,
    load_github_radar_config,
)
from radar.models import ContentItem
from radar.release_announcement import build_release_card, load_release_manifest
from radar.official_news import (
    OfficialSource,
    fetch_official_news,
    load_official_sources,
    parse_official_changelog,
    parse_official_feed,
    parse_qwen_api,
    parse_seed_router,
    parse_volcengine_router,
)
from radar.url_utils import canonical_url
from radar.source_material import source_text_status
from radar.sources import (
    _deduplicate_items,
    fetch_industry_digests,
    fetch_youtube,
    load_builders_x_accounts,
    parse_builders_x,
)
from radar.storage import Storage
from radar.subscriptions import build_subscription_result_card, validate_batch
from radar.workflow import (
    COLLECTION_LOOKBACK_HOURS,
    PRIMARY_WINDOW_HOURS,
    artifact_paths,
    doctor,
    scheduled_group_delivery_enabled,
)


def main() -> int:
    if not __debug__:
        print(json.dumps({"status": "failed", "error": "assertions are disabled by Python optimization"}))
        return 1
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
    assert len(official_sources) == 49
    github_config = load_github_radar_config(
        Path(__file__).resolve().parents[1] / "references" / "github-radar.json"
    )
    assert github_config["topics"] == [
        "ai-agents",
        "artificial-intelligence",
        "generative-ai",
        "large-language-models",
        "model-context-protocol",
    ]
    assert github_config["max_items"] == 12
    with tempfile.TemporaryDirectory() as temporary:
        release_manifest_path = Path(temporary) / "release.json"
        release_manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "version": "a" * 40,
                    "title": "GitHub 开源雷达上线",
                    "summary": "本次发布增加热门开源项目观察，并强化来源质量校验。",
                    "changes": [
                        "新增 GitHub 开源雷达与 Star 增量快照",
                        "强化官方来源健康检查和卡片分区",
                    ],
                    "verification": ["49 项离线测试通过", "生产 doctor 正常"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        release_manifest = load_release_manifest(release_manifest_path)
        release_card = build_release_card(release_manifest)
        repeated_release_card = build_release_card(release_manifest)
        release_card_text = json.dumps(release_card, ensure_ascii=False)
        assert repeated_release_card == release_card
        assert "AI News Skills · 更新公告" in release_card_text
        assert "GitHub 开源雷达上线" in release_card_text
        assert "版本 `aaaaaaa`" in release_card_text
        assert len(release_card_text.encode("utf-8")) < 20_000
        release_receipt = Path(temporary) / "release-receipt.json"
        release_cards_hash = hashlib.sha256(
            json.dumps([release_card], ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        release_card_hash = hashlib.sha256(
            json.dumps(release_card, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        release_receipt.write_text(
            json.dumps(
                {
                    "status": "sent",
                    "target_hash": hashlib.sha256(b"release-group").hexdigest(),
                    "cards_sha256": release_cards_hash,
                    "sent_card_hashes": [release_card_hash],
                }
            ),
            encoding="utf-8",
        )
        release_code, release_result = send_group_cards(
            [repeated_release_card],
            "release-group",
            release_receipt,
            Path(temporary) / "unused.mjs",
        )
        assert release_code == 0 and release_result["status"] == "skipped"
        release_manifest_path.write_text(
            json.dumps({"schema_version": 1, "version": "short"}), encoding="utf-8"
        )
        try:
            load_release_manifest(release_manifest_path)
        except ValueError as error:
            assert "40-character Git commit" in str(error)
        else:
            raise AssertionError("short release version was accepted")
        release_manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "version": "a" * 40,
                    "title": "AI ????? release",
                    "summary": "A valid-length release summary for validation.",
                    "changes": ["Valid release change"],
                    "verification": ["Valid release verification"],
                }
            ),
            encoding="utf-8",
        )
        try:
            load_release_manifest(release_manifest_path)
        except ValueError as error:
            assert "corrupted text" in str(error)
        else:
            raise AssertionError("corrupted release text was accepted")
    aws_whats_new = next(
        source for source in official_sources if source["name"] == "AWS What's New · AI"
    )
    assert aws_whats_new["url"].endswith("/about-aws/whats-new/recent/feed/")
    assert "Amazon Bedrock" in aws_whats_new["title_include_terms"]
    aws_release_notes = [
        source
        for source in official_sources
        if source["name"].startswith("Amazon ")
        and source.get("preserve_feed_entries")
    ]
    assert len(aws_release_notes) == 3
    required_platform_sources = {
        "Google Gemini Enterprise Agent Platform",
        "Cloudflare AI Changelog",
        "Databricks AI",
        "华为 AI",
        "硅基流动 SiliconFlow",
        "商汤 SenseNova",
    }
    assert required_platform_sources <= {source["name"] for source in official_sources}
    assert all(
        source.get("preserve_feed_entries")
        for source in official_sources
        if source["name"]
        in {
            "Google Gemini Enterprise Agent Platform",
            "Cloudflare AI Changelog",
            "Databricks AI",
        }
    )
    runway_source = next(
        source for source in official_sources if source["name"] == "Runway"
    )
    assert runway_source["index_url"] == "https://runway.com/research"
    assert runway_source["article_path_prefix"] == "/research/"
    assert "/research/publications" in runway_source["excluded_path_prefixes"]
    assert COLLECTION_LOOKBACK_HOURS == 96 > PRIMARY_WINDOW_HOURS == 24
    stable_release_sources = [
        source for source in official_sources if source.get("stable_releases_only")
    ]
    assert len(stable_release_sources) == 6
    industry_sources = load_official_sources(
        Path(__file__).resolve().parents[1]
        / "references"
        / "industry-digest-sources.json"
    )
    assert len(industry_sources) == 7
    assert industry_sources[0]["name"] == "DeepLearning.AI · The Batch"
    assert {source["name"] for source in industry_sources[-3:]} == {
        "MIT Technology Review AI",
        "The Register AI+ML",
        "InfoQ 中文站 · AI",
    }
    assert all(1 <= source["max_items"] <= 4 for source in industry_sources)
    with tempfile.TemporaryDirectory() as temporary:
        invalid_source_path = Path(temporary) / "invalid-source.json"
        invalid_source_path.write_text(
            json.dumps(
                [
                    {
                        "name": "Invalid limit",
                        "kind": "rss",
                        "url": "https://example.com/feed",
                        "allowed_hosts": ["example.com"],
                        "max_items": 0,
                    }
                ]
            ),
            encoding="utf-8",
        )
        try:
            load_official_sources(invalid_source_path)
        except ValueError as error:
            assert "max_items must be 1 through 30" in str(error)
        else:
            raise AssertionError("invalid source item limit was accepted")
        invalid_source_path.write_text(
            json.dumps(
                [
                    {
                        "name": "Implicit limit conversion",
                        "kind": "rss",
                        "url": "https://example.com/feed",
                        "allowed_hosts": ["example.com"],
                        "max_items": "3",
                    }
                ]
            ),
            encoding="utf-8",
        )
        try:
            load_official_sources(invalid_source_path)
        except ValueError as error:
            assert "invalid max_items" in str(error)
        else:
            raise AssertionError("string source item limit was accepted")
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
  <item>
    <title>Customer tutorial</title>
    <description>A detailed but intentionally excluded customer tutorial.</description>
    <link>https://example.com/news/customer-tutorial</link>
    <guid>customer-tutorial</guid>
    <pubDate>Tue, 21 Jul 2026 07:00:00 GMT</pubDate>
  </item>
  <item>
    <title>v1.2.0-rc.1</title>
    <description>A detailed prerelease that must not enter a stable release feed.</description>
    <link>https://example.com/news/prerelease</link>
    <guid>prerelease</guid>
    <pubDate>Tue, 21 Jul 2026 08:00:00 GMT</pubDate>
  </item>
</channel></rss>"""
    parsed_official = parse_official_feed(
        official_rss,
        {
            "name": "Example Lab",
            "kind": "rss",
            "url": "https://example.com/rss.xml",
            "allowed_hosts": ["example.com"],
            "title_exclude_terms": ["customer"],
            "stable_releases_only": True,
        },
        datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert len(parsed_official) == 1
    assert parsed_official[0].source_type == "official_news"
    assert parsed_official[0].raw_source_text.startswith("A detailed official")
    short_term_rss = official_rss.replace(
        b"<title>Official Model One</title>",
        b"<title>Training platform update</title>",
    )
    assert not parse_official_feed(
        short_term_rss,
        {
            "name": "Short term boundary",
            "kind": "rss",
            "url": "https://example.com/rss.xml",
            "allowed_hosts": ["example.com"],
            "title_include_terms": ["AI"],
        },
        datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert len(
        parse_official_feed(
            short_term_rss.replace(b"Training platform", b"AI platform"),
            {
                "name": "Short term boundary",
                "kind": "rss",
                "url": "https://example.com/rss.xml",
                "allowed_hosts": ["example.com"],
                "title_include_terms": ["AI"],
            },
            datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        )
    ) == 1
    try:
        parse_official_feed(
            b'<rss version="2.0"><channel></channel></rss>',
            {
                "name": "Empty Feed",
                "kind": "rss",
                "url": "https://example.com/empty.xml",
                "allowed_hosts": ["example.com"],
            },
            datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        )
    except ValueError as error:
        assert str(error) == "official feed contains no entries"
    else:
        raise AssertionError("empty official feed must fail closed")
    release_notes_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Runtime capability one</title>
    <description>A concrete first release-note entry with operational details.</description>
    <link>https://example.com/release-notes.html</link>
    <guid>release-note-one</guid>
    <pubDate>Tue, 21 Jul 2026 06:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Runtime capability two</title>
    <description>A distinct release-note entry that shares the same document URL.</description>
    <link>https://example.com/release-notes.html</link>
    <guid>release-note-two</guid>
    <pubDate>Tue, 21 Jul 2026 07:00:00 GMT</pubDate>
  </item>
</channel></rss>"""
    parsed_release_notes = parse_official_feed(
        release_notes_rss,
        {
            "name": "Example Release Notes",
            "kind": "rss",
            "url": "https://example.com/releases.rss",
            "allowed_hosts": ["example.com"],
            "preserve_feed_entries": True,
        },
        datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert len(parsed_release_notes) == 2
    assert len({item.url for item in parsed_release_notes}) == 2
    assert all("#entry-" in item.url for item in parsed_release_notes)
    assert len({item.dedup_identity for item in parsed_release_notes}) == 2
    assert all(item.extra == "官方 Release Notes" for item in parsed_release_notes)
    industry_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
  <title>The Batch weekly issue</title>
  <description><![CDATA[An editorial overview of current AI developments with attributed analysis.]]></description>
  <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/"><![CDATA[
    Full article body that must never become industry-digest evidence.
  ]]></content:encoded>
  <link>https://charonhub.deeplearning.ai/issue-test/</link>
  <guid>issue-test</guid>
  <pubDate>Tue, 21 Jul 2026 14:00:00 GMT</pubDate>
</item></channel></rss>"""
    with tempfile.TemporaryDirectory() as temporary:
        industry_storage = Storage(Path(temporary))
        industry_storage.initialize()

        def fake_industry_fetcher(_: str, __: Storage) -> tuple[bytes, bool]:
            return industry_rss, False

        industry_items, industry_health = fetch_industry_digests(
            industry_sources,
            datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc),
            industry_storage,
            fake_industry_fetcher,
        )
    assert len(industry_items) == 1 and industry_health.status == "ok"
    assert industry_health.source == "industry_digest"
    assert industry_items[0].source_type == "industry_digest"
    assert industry_items[0].source.startswith("行业精选 · ")
    assert industry_items[0].extra.startswith("编辑 RSS")
    assert "Full article body" not in industry_items[0].raw_source_text
    limited_industry_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Older enterprise AI update</title>
    <description>An older but valid enterprise AI report with concrete operational details.</description>
    <link>https://media.example.com/older</link><guid>older</guid>
    <pubDate>Tue, 21 Jul 2026 05:00:00 GMT</pubDate></item>
  <item><title>Newer enterprise AI update</title>
    <description>A newer enterprise AI report with concrete deployment and product details.</description>
    <link>https://media.example.com/newer</link><guid>newer</guid>
    <pubDate>Tue, 21 Jul 2026 07:00:00 GMT</pubDate></item>
  <item><title>Newest enterprise AI update</title>
    <description>The newest enterprise AI report with concrete infrastructure and pricing details.</description>
    <link>https://media.example.com/newest</link><guid>newest</guid>
    <pubDate>Tue, 21 Jul 2026 08:00:00 GMT</pubDate></item>
  <item><title>Partner Content: enterprise AI promotion</title>
    <description>A sponsored promotional article that must be rejected by the title filter.</description>
    <link>https://media.example.com/sponsored</link><guid>sponsored</guid>
    <pubDate>Tue, 21 Jul 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""
    with tempfile.TemporaryDirectory() as temporary:
        limited_storage = Storage(Path(temporary))
        limited_storage.initialize()

        def fake_limited_fetcher(_: str, __: Storage) -> tuple[bytes, bool]:
            return limited_industry_rss, False

        limited_items, limited_health = fetch_industry_digests(
            [
                {
                    "name": "Limited media",
                    "kind": "rss",
                    "url": "https://media.example.com/feed",
                    "allowed_hosts": ["media.example.com"],
                    "allow_content_fallback": False,
                    "title_exclude_terms": ["partner content"],
                    "max_items": 2,
                }
            ],
            datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc),
            limited_storage,
            fake_limited_fetcher,
        )
    assert [item.item_id for item in limited_items] == [
        hashlib.sha256(b"newest").hexdigest()[:24],
        hashlib.sha256(b"newer").hexdigest()[:24],
    ]
    assert limited_health.checks[0].detail.startswith("limited 3 matching items to 2;")
    youtube_feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <yt:videoId>{video_id}</yt:videoId>
    <title>{title}</title>
    <published>{published}</published>
    <link rel="alternate" href="https://www.youtube.com/watch?v={video_id}"/>
    <media:group><media:description>{description}</media:description></media:group>
  </entry>
</feed>"""
    active_channel_id = "UC" + "A" * 22
    quiet_channel_id = "UC" + "B" * 22
    off_topic_channel_id = "UC" + "C" * 22

    def fake_youtube_fetcher(url: str, _: Storage) -> tuple[bytes, bool]:
        active = url.endswith(active_channel_id)
        off_topic = url.endswith(off_topic_channel_id)
        return youtube_feed.format(
            video_id=("active-video" if active else "off-topic-video" if off_topic else "old-video"),
            title=("Active AI update" if active else "A cooking tutorial" if off_topic else "Old AI update"),
            published=("2026-07-21T08:00:00+00:00" if active or off_topic else "2026-07-19T08:00:00+00:00"),
            description=(
                "A useful company AI engineering update."
                if not off_topic
                else "A detailed recipe for a family dinner."
            ),
        ).encode("utf-8"), False

    with tempfile.TemporaryDirectory() as temporary:
        youtube_storage = Storage(Path(temporary))
        youtube_storage.initialize()
        youtube_items, youtube_health = fetch_youtube(
            [
                {"name": "Active", "channel_id": active_channel_id},
                {"name": "Quiet", "channel_id": quiet_channel_id},
                {"name": "Off topic", "channel_id": off_topic_channel_id},
            ],
            datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc),
            youtube_storage,
            fake_youtube_fetcher,
        )
    assert len(youtube_items) == 1 and youtube_health.status == "ok"
    assert "1 with relevant in-window items" in youtube_health.detail
    assert "2 without relevant in-window items" in youtube_health.detail
    assert "1 off-topic items filtered" in youtube_health.detail
    changelog_items = parse_official_changelog(
        b"""<html><body>
          <h2>July 21, 2026</h2>
          <p>Released a production model with a new stable API identifier.</p>
          <ul><li>Added structured tool calling and a documented migration path.</li></ul>
          <h2>July 19, 2026</h2><p>Old release outside the window.</p>
        </body></html>""",
        {
            "name": "Example API",
            "kind": "html_changelog",
            "url": "https://example.com/changelog",
            "allowed_hosts": ["example.com"],
        },
        datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc),
    )
    assert len(changelog_items) == 1
    assert changelog_items[0].extra == "官方 Changelog"
    assert changelog_items[0].url.endswith("#2026-07-21")
    assert "structured tool calling" in changelog_items[0].raw_source_text
    year_context_items = parse_official_changelog(
        b"""<html><body>
          <h3>August, 2024</h3><div>Aug 6</div>
          <p>Historical structured outputs release must remain in 2024.</p>
          <h3>August, 2026</h3><div>Aug 7</div>
          <p>Current production API release with a complete migration path.</p>
        </body></html>""",
        {
            "name": "Year Context API",
            "kind": "html_changelog",
            "url": "https://example.com/changelog-year-context",
            "allowed_hosts": ["example.com"],
        },
        datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
    )
    assert len(year_context_items) == 1
    assert year_context_items[0].published_at.date().isoformat() == "2026-08-07"
    assert year_context_items[0].url.endswith("#2026-08-07")
    assert "Historical structured outputs" not in year_context_items[0].raw_source_text
    try:
        parse_official_changelog(
            b"<html><body><h2>Release notes</h2><p>No dated entries.</p></body></html>",
            {
                "name": "Broken Changelog",
                "kind": "html_changelog",
                "url": "https://example.com/changelog-broken",
                "allowed_hosts": ["example.com"],
            },
            datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc),
        )
    except ValueError as error:
        assert str(error) == "official changelog has no parseable dated entries"
    else:
        raise AssertionError("undated official changelog must fail closed")
    overlap_changelog_items = parse_official_changelog(
        b"""<html><body>
          <h2>July 23, 2026</h2>
          <p>Release discovered during the overlapping collection window.</p>
        </body></html>""",
        {
            "name": "Example Overlap API",
            "kind": "html_changelog",
            "url": "https://example.com/changelog-overlap",
            "allowed_hosts": ["example.com"],
        },
        datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert len(overlap_changelog_items) == 1
    assert overlap_changelog_items[0].published_at.date().isoformat() == "2026-07-23"
    chinese_changelog_items = parse_official_changelog(
        """<html><body>
          <h2>2026年7月21日</h2>
          <p>模型服务新增稳定版本，并提供完整迁移说明和调用参数。</p>
          <table><tr><th>日期</th><th>模块</th><th>功能说明</th></tr>
          <tr><td>7月21日</td><td>平台功能</td><td>智能体记忆库正式商用并发布计费说明。</td></tr>
          <tr><td>7月19日</td><td>平台功能</td><td>窗口外的历史更新。</td></tr></table>
        </body></html>""".encode("utf-8"),
        {
            "name": "Example China API",
            "kind": "html_changelog",
            "url": "https://example.com/changelog-cn",
            "allowed_hosts": ["example.com"],
        },
        datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc),
    )
    assert len(chinese_changelog_items) == 1
    assert "智能体记忆库" in chinese_changelog_items[0].raw_source_text
    mintlify_changelog_items = parse_official_changelog(
        """<html><body>
          <div class="update-container">
            <button>2026.07.21</button>
            <div><h3>模型上新</h3><span>平台新增生产级智能体模型，并提供工具调用与迁移说明。</span></div>
          </div>
        </body></html>""".encode("utf-8"),
        {
            "name": "Example Mintlify API",
            "kind": "html_changelog",
            "url": "https://example.com/changelog-mintlify",
            "allowed_hosts": ["example.com"],
        },
        datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc),
    )
    assert len(mintlify_changelog_items) == 1
    assert "工具调用" in mintlify_changelog_items[0].raw_source_text

    qwen_items = parse_qwen_api(
        json.dumps(
            {
                "data": {
                    "articles": [
                        {
                            "id": "article-one",
                            "title": "Qwen Model One",
                            "path": "qwen-model-one",
                            "extra": {
                                "date": "2026-07-21T08:00:00+08:00",
                                "introduction": "<p>A concrete official Qwen model introduction.</p>",
                            },
                        }
                    ]
                }
            }
        ).encode(),
        {
            "name": "Qwen",
            "kind": "qwen_api",
            "url": "https://qwen.ai/api/articles",
            "article_url_template": "https://qwen.ai/blog?id={slug}",
            "allowed_hosts": ["qwen.ai"],
        },
        datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert len(qwen_items) == 1
    assert qwen_items[0].url.endswith("id=qwen-model-one")

    seed_payload = {
        "loaderData": {
            "(locale$)/blog/page": {
                "article_list": [
                    {
                        "ArticleMeta": {
                            "PublishDate": int(
                                datetime(
                                    2026, 7, 21, 8, 0, tzinfo=timezone.utc
                                ).timestamp()
                                * 1000
                            ),
                            "ResearchArea": [{"ResearchAreaName": "Models"}],
                        },
                        "ArticleSubContentZh": {
                            "Title": "Seed 模型发布",
                            "TitleKey": "seed-model-release",
                            "Abstract": "一项包含明确能力说明的官方模型发布。",
                        },
                    }
                ]
            }
        }
    }
    seed_items = parse_seed_router(
        (
            "<script>window._ROUTER_DATA = "
            + json.dumps(seed_payload, ensure_ascii=False)
            + "</script>"
        ).encode(),
        {
            "name": "ByteDance Seed",
            "kind": "seed_router",
            "url": "https://seed.bytedance.com/blog",
            "article_url_template": "https://seed.bytedance.com/zh/blog/{slug}",
            "allowed_categories": ["Models"],
            "allowed_hosts": ["seed.bytedance.com"],
        },
        datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert len(seed_items) == 1
    assert seed_items[0].title == "Seed 模型发布"

    seed_midnight_payload = json.loads(json.dumps(seed_payload))
    seed_midnight_payload["loaderData"]["(locale$)/blog/page"]["article_list"][0][
        "ArticleMeta"
    ]["PublishDate"] = int(
        datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    seed_midnight_items = parse_seed_router(
        (
            "<script>window._ROUTER_DATA = "
            + json.dumps(seed_midnight_payload, ensure_ascii=False)
            + "</script>"
        ).encode(),
        {
            "name": "ByteDance Seed",
            "kind": "seed_router",
            "url": "https://seed.bytedance.com/blog",
            "article_url_template": "https://seed.bytedance.com/zh/blog/{slug}",
            "allowed_categories": ["Models"],
            "allowed_hosts": ["seed.bytedance.com"],
        },
        datetime(2026, 7, 31, 0, 30, tzinfo=timezone.utc),
    )
    assert len(seed_midnight_items) == 1

    volcengine_payload = {
        "loaderData": {
            "__ssr_without_user/news/page": {
                "listOnlineArticle": {
                    "List": [
                        {
                            "DocumentID": 23,
                            "CategoryCode": "machinelearning",
                            "CategoryCodeName": "机器学习",
                            "Title": "火山方舟模型服务发布智能路由",
                            "Summary": "智能路由根据质量、时延和成本目标选择模型服务。",
                            "Description": "",
                            "CreatedTime": "2026-07-21T08:00:00+08:00",
                        },
                        {
                            "DocumentID": 24,
                            "CategoryCode": "database",
                            "CategoryCodeName": "数据库",
                            "Title": "数据库更新",
                            "Summary": "不属于人工智能与机器学习分类。",
                            "CreatedTime": "2026-07-21T09:00:00+08:00",
                        },
                    ]
                }
            }
        }
    }
    volcengine_items = parse_volcengine_router(
        (
            "<script>window._ROUTER_DATA = "
            + json.dumps(volcengine_payload, ensure_ascii=False)
            + "</script>"
        ).encode("utf-8"),
        {
            "name": "火山引擎",
            "kind": "volcengine_router",
            "url": "https://www.volcengine.com/news",
            "article_url_template": "https://www.volcengine.com/news/detail/{id}",
            "allowed_categories": ["machinelearning", "机器学习"],
            "allowed_hosts": ["volcengine.com"],
        },
        datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert len(volcengine_items) == 1
    assert volcengine_items[0].url.endswith("/23")

    source = {
        "items": [
            {
                "id": "video-1",
                "source_type": "youtube",
                "recency_status": "current",
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
                "recency_status": "recovered",
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
    assert len(items) == 2 and len(cards) == 2
    current_intro = cards[0]["body"]["elements"][0]["content"]
    recovered_intro = cards[1]["body"]["elements"][0]["content"]
    assert "今日最新 1 条信号" in current_intro and "当期 1 · 补录 0" in current_intro
    assert "补录 1 条信号" in recovered_intro and "当期 0 · 补录 1" in recovered_intro

    numeric_source = {
        "items": [
            {
                "id": "numeric-1",
                "source_type": "industry_digest",
                "source": "行业精选 · Example",
                "title": "Startup raises $20 million",
                "published_at": "2026-07-20T08:00:00+00:00",
                "recency_status": "current",
                "url": "https://example.com/numeric-1",
                "source_text_status": "available",
                "source_text": "The startup raised $20 million after eighteen months.",
                "unavailable_reason": "",
                "recommendation": "",
            }
        ]
    }
    numeric_markdown = """# AI 前哨 | 2026-07-20

### 1. [Startup raises $20 million](https://example.com/numeric-1)
- 来源：行业精选 · Example
- 重点：是
- 来源摘要：该公司在 18 个月后融资 2000 万美元。
"""
    validate_frozen_digest(numeric_source, numeric_markdown)
    try:
        validate_frozen_digest(
            numeric_source,
            numeric_markdown.replace("- 重点：是", "- 重点：否"),
        )
    except ValueError as error:
        assert "requires at least one highlight" in str(error)
    else:
        raise AssertionError("a populated current section accepted zero highlights")

    unavailable_current_source = {"items": [dict(source["items"][1])]}
    unavailable_current_source["items"][0]["recency_status"] = "current"
    unavailable_current_markdown = """# AI 前哨 | 2026-07-20

### 1. [Unavailable update](https://example.com/item-2)
- 来源：AIHOT · Example
- 重点：否
- 来源摘要：不可用（RSS 未提供足够的可用简介）
"""
    validate_frozen_digest(unavailable_current_source, unavailable_current_markdown)
    unsupported_numeric_source = json.loads(json.dumps(numeric_source))
    unsupported_numeric_source["items"][0]["title"] = "Startup raises $7.9 million"
    unsupported_numeric_source["items"][0]["source_text"] = (
        "The product is used by 5.3 million people around the world."
    )
    unsupported_numeric_markdown = numeric_markdown.replace(
        "Startup raises $20 million", "Startup raises $7.9 million"
    ).replace("18 个月后融资 2000 万美元", "获得 790 万美元融资")
    try:
        validate_frozen_digest(unsupported_numeric_source, unsupported_numeric_markdown)
    except ValueError as error:
        assert "unsupported numeric evidence" in str(error)
    else:
        raise AssertionError("title-only financing evidence was accepted")
    unsupported_chinese_source = json.loads(json.dumps(numeric_source))
    unsupported_chinese_source["items"][0]["title"] = "九项测试全面提升"
    unsupported_chinese_source["items"][0]["source_text"] = "两项基准测试取得提升。"
    unsupported_chinese_markdown = numeric_markdown.replace(
        "Startup raises $20 million", "九项测试全面提升"
    ).replace("该公司在 18 个月后融资 2000 万美元", "该模型在九项测试中全面提升")
    try:
        validate_frozen_digest(unsupported_chinese_source, unsupported_chinese_markdown)
    except ValueError as error:
        assert "unsupported numeric evidence" in str(error)
    else:
        raise AssertionError("title-only benchmark count was accepted")

    duplicate_time = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    duplicate_items = _deduplicate_items(
        [
            ContentItem(
                item_id="release-a",
                source_type="official_news",
                source="官方发布 · Example A",
                title="Same product availability update",
                published_at=duplicate_time,
                url="https://docs.example.com/releases/a",
            ),
            ContentItem(
                item_id="release-b",
                source_type="official_news",
                source="官方发布 · Example B",
                title="Same product availability update",
                published_at=duplicate_time,
                url="https://docs.example.com/releases/b",
            ),
            ContentItem(
                item_id="release-next-day",
                source_type="official_news",
                source="官方发布 · Example A",
                title="Same product availability update",
                published_at=duplicate_time + timedelta(days=1),
                url="https://docs.example.com/releases/c",
            ),
        ]
    )
    assert {item.item_id for item in duplicate_items} == {"release-a", "release-next-day"}
    cross_media_items = _deduplicate_items(
        [
            ContentItem(
                item_id="official-event",
                source_type="official_news",
                source="官方发布 · Example",
                title="OpenAI launches enterprise agent platform for production teams",
                published_at=duplicate_time,
                url="https://example.com/news/agent-platform",
            ),
            ContentItem(
                item_id="media-duplicate",
                source_type="industry_digest",
                source="行业精选 · Example Media",
                title="OpenAI launches enterprise agent platform for production team",
                published_at=duplicate_time,
                url="https://media.example.net/openai-agent-platform",
            ),
            ContentItem(
                item_id="media-analysis",
                source_type="industry_digest",
                source="行业精选 · Example Media",
                title="Enterprise buyers compare agent governance and deployment costs",
                published_at=duplicate_time,
                url="https://media.example.net/agent-governance-analysis",
            ),
            ContentItem(
                item_id="media-next-day",
                source_type="industry_digest",
                source="行业精选 · Other Media",
                title="OpenAI launches enterprise agent platform for production teams",
                published_at=duplicate_time + timedelta(days=1),
                url="https://other.example.net/openai-agent-platform",
            ),
            ContentItem(
                item_id="second-official",
                source_type="official_news",
                source="官方发布 · Partner",
                title="OpenAI launches enterprise agent platform for production teams",
                published_at=duplicate_time,
                url="https://partner.example.org/official-announcement",
            ),
        ]
    )
    assert {item.item_id for item in cross_media_items} == {
        "official-event",
        "media-analysis",
        "media-next-day",
        "second-official",
    }
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
    github_record = {
        "full_name": "example/agent-runtime",
        "html_url": "https://github.com/example/agent-runtime",
        "description": "A production-oriented runtime for reliable AI agents and tool execution.",
        "created_at": "2026-07-29T00:00:00Z",
        "pushed_at": "2026-08-05T00:00:00Z",
        "stargazers_count": 1000,
        "forks_count": 120,
        "language": "Python",
        "license": {"spdx_id": "Apache-2.0"},
        "topics": ["ai-agents", "agent-runtime"],
        "archived": False,
        "fork": False,
    }
    test_github_config = {
        "discovery_days": 45,
        "bootstrap_days": 30,
        "min_initial_stars": 500,
        "min_initial_stars_per_day": 30,
        "min_star_gain": 100,
        "max_candidates_per_query": 20,
        "max_items": 12,
        "topics": ["ai-agents"],
    }

    def github_fetcher(_url: str, _storage: Storage) -> dict[str, object]:
        return {"payload": {"items": [github_record]}, "cache_mode": "fresh"}

    github_now = datetime(2026, 8, 5, 0, 30, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as temporary:
        github_storage = Storage(Path(temporary))
        github_storage.initialize()
        github_items, github_health = fetch_github_trending(
            test_github_config, github_storage, github_now, github_fetcher
        )
        assert len(github_items) == 1 and github_health.status == "ok"
        assert github_items[0].source_type == "github_trending"
        assert "production-oriented runtime" in github_items[0].raw_source_text
        assert "Star" not in github_items[0].raw_source_text
        assert "Fork" not in github_items[0].raw_source_text
        assert "2026-" not in github_items[0].raw_source_text
        assert github_items[0].dedup_identity.endswith("#trend-date=2026-08-05")
        stored_snapshot = github_storage.previous_github_snapshot(
            "example/agent-runtime", "2026-08-06"
        )
        assert stored_snapshot is not None
        assert stored_snapshot["stars"] == 1000
        assert stored_snapshot["forks"] == 120
    with tempfile.TemporaryDirectory() as temporary:
        github_storage = Storage(Path(temporary))
        github_storage.initialize()
        github_storage.put_github_snapshot(
            "example/agent-runtime",
            "2026-08-04",
            850,
            100,
            datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 4, 0, 30, tzinfo=timezone.utc),
        )
        github_items, _ = fetch_github_trending(
            test_github_config, github_storage, github_now, github_fetcher
        )
        assert len(github_items) == 1
        assert "production-oriented runtime" in github_items[0].raw_source_text
        assert "150 Star" not in github_items[0].raw_source_text
    with tempfile.TemporaryDirectory() as temporary:
        github_storage = Storage(Path(temporary))
        github_storage.initialize()
        rate_limited_config = dict(test_github_config)
        rate_limited_config["topics"] = ["ai-agents", "generative-ai"]
        rate_limit_calls = 0

        def rate_limited_fetcher(_url: str, _storage: Storage) -> dict[str, object]:
            nonlocal rate_limit_calls
            rate_limit_calls += 1
            raise GitHubRateLimitError("GitHub search rate limit exhausted")

        github_items, github_health = fetch_github_trending(
            rate_limited_config, github_storage, github_now, rate_limited_fetcher
        )
        assert not github_items and github_health.status == "error"
        assert rate_limit_calls == 1
        assert github_health.checks[1].detail == "skipped after rate limit"
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
            item_id="bilibili-highlight",
            source_type="bilibili",
            source="哔哩哔哩 · Example",
            title="Bilibili priority",
            url="https://www.bilibili.com/video/BV1priority/",
            summary="Bilibili priority summary",
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
        FrozenItem(
            item_id="bilibili-remaining",
            source_type="bilibili",
            source="哔哩哔哩 · Example",
            title="Bilibili remaining",
            url="https://www.bilibili.com/video/BV1remaining/",
            summary="Bilibili remaining summary",
            recommendation="",
            highlight=False,
        ),
        FrozenItem(
            item_id="industry-highlight",
            source_type="industry_digest",
            source="行业精选 · DeepLearning.AI · The Batch",
            title="Industry digest priority",
            url="https://charonhub.deeplearning.ai/issue-priority/",
            summary="Industry digest priority summary",
            recommendation="",
            highlight=True,
        ),
        FrozenItem(
            item_id="industry-remaining",
            source_type="industry_digest",
            source="行业精选 · DeepLearning.AI · The Batch",
            title="Industry digest remaining",
            url="https://charonhub.deeplearning.ai/issue-remaining/",
            summary="Industry digest remaining summary",
            recommendation="",
            highlight=False,
        ),
        FrozenItem(
            item_id="github-highlight",
            source_type="github_trending",
            source="GitHub 开源雷达 · Trending",
            title="example/agent-runtime",
            url="https://github.com/example/agent-runtime",
            summary="GitHub trend priority summary",
            recommendation="",
            highlight=True,
        ),
        FrozenItem(
            item_id="github-remaining",
            source_type="github_trending",
            source="GitHub 开源雷达 · Trending",
            title="example/model-router",
            url="https://github.com/example/model-router",
            summary="GitHub trend remaining summary",
            recommendation="",
            highlight=False,
        ),
    ]
    section_card = build_card("2026-07-20", section_items)
    section_elements = section_card["body"]["elements"]
    assert section_card["config"]["update_multi"] is True
    assert section_card["config"]["style"]["text_size"]["section_heading"] == {
        "default": "heading",
        "pc": "heading",
        "mobile": "heading",
    }
    source_headers = [
        element
        for element in section_elements
        if element.get("tag") == "markdown" and "text_size" in element
    ]
    assert len(source_headers) == 7
    assert all(element.get("text_size") == "section_heading" for element in source_headers)
    section_text = json.dumps(section_card, ensure_ascii=False)
    assert "编辑评分" not in section_text
    assert "原文语言" not in section_text
    assert "事件链" not in section_text
    assert "Official remaining" in section_text
    assert "Official remaining summary" not in section_text

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
        < element_position("**📺 哔哩哔哩**")
        < element_position("Bilibili priority")
        < element_position("其余 1 条 B站投稿")
        < element_position("**🧭 AIHOT**")
        < element_position("AIHOT priority")
        < element_position("其余 1 条 AIHOT 动态")
        < element_position("**🧩 GitHub 开源雷达**")
        < element_position("example/agent-runtime")
        < element_position("其余 1 条 GitHub 热门项目")
        < element_position("**📰 行业精选**")
        < element_position("Industry digest priority")
        < element_position("其余 1 条 行业周报")
        < element_position("**💬 Builders X**")
        < element_position("X priority")
        < element_position("其余 1 条 Builders X 动态")
    )
    recovered_item = replace(
        section_items[0],
        item_id="official-recovered",
        title="Recovered official update",
        url="https://example.com/news/recovered",
        summary="补录（2026-07-19）：Recovered official summary",
        highlight=False,
        recency_status="recovered",
    )
    window_cards = build_cards("2026-07-20", [*section_items, recovered_item])
    assert len(window_cards) == 2
    assert window_cards[0]["header"]["title"]["content"].startswith("📗 AI 前哨｜")
    assert window_cards[1]["header"]["title"]["content"].startswith("📙 AI 前哨补录｜")
    assert "Recovered official update" not in json.dumps(
        window_cards[0], ensure_ascii=False
    )
    recovered_text = json.dumps(window_cards[1], ensure_ascii=False)
    assert "Recovered official update" in recovered_text
    assert "Recovered official summary" not in recovered_text
    assert "不属于当期 24 小时窗口" in recovered_text
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
            rank_score={
                "official_news": 10,
                "youtube": 20,
                "bilibili": 30,
                "aihot": 40,
                "github_trending": 50,
                "industry_digest": 60,
                "builders_x": 99,
            }[source_type],
        )
        for source_type, source, url in (
            ("official_news", "官方发布 · Example", "https://example.com/news/split"),
            ("builders_x", "Builders X · Example", "https://x.com/example/status/3001"),
            ("aihot", "AIHOT · Example", "https://example.com/split-aihot"),
            (
                "github_trending",
                "GitHub 开源雷达 · Trending",
                "https://github.com/example/split",
            ),
            ("youtube", "YouTube · Example", "https://www.youtube.com/watch?v=split"),
            (
                "bilibili",
                "哔哩哔哩 · Example",
                "https://www.bilibili.com/video/BV1split/",
            ),
            (
                "industry_digest",
                "行业精选 · DeepLearning.AI · The Batch",
                "https://charonhub.deeplearning.ai/issue-split/",
            ),
        )
    ]
    split_cards = build_cards("2026-07-20", split_items)
    assert len(split_cards) == 7
    split_text = [json.dumps(card, ensure_ascii=False) for card in split_cards]
    assert all("今日必看" not in text for text in split_text)
    assert all("subtitle" not in card["header"] for card in split_cards)
    assert "📡 官方发布" in split_text[0]
    assert "🎬 YouTube" in split_text[1]
    assert "📺 哔哩哔哩" in split_text[2]
    assert "🧭 AIHOT" in split_text[3]
    assert "🧩 GitHub 开源雷达" in split_text[4]
    assert "📰 行业精选" in split_text[5]
    assert "💬 Builders X" in split_text[6]
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
    assert _parse_bridge_payload(
        'OpenClaw log\n{"status":"sent","message_id":"message-test"}\n'
    ) == {"status": "sent", "message_id": "message-test"}
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
        dynamic_date_index = (
            '<a href="/news/model-two">Official Model Two '
            "A concrete official model description.</a>"
        ).encode()
        slash_date_index = (
            '<a href="/news/slash-model">2026/07/21 Official Slash Model '
            "A concrete official model description.</a>"
        ).encode()
        slash_date_article = b"""<html><head>
          <meta property="og:title" content="Official Slash Model">
          <meta property="og:description" content="A concrete slash-date model description with capabilities.">
        </head></html>"""

        def fake_official_fetcher(url: str, _: Storage) -> tuple[bytes, bool]:
            if url == "https://example.com/news":
                return html_index, False
            if url == "https://example.com/dynamic-news":
                return dynamic_date_index, False
            if url == "https://example.com/slash-news":
                return slash_date_index, False
            if url == "https://example.com/empty-news":
                return b"<html><body><p>No matching newsroom links.</p></body></html>", False
            if url == "https://example.com/news/model-two":
                return html_article, False
            if url == "https://example.com/news/slash-model":
                return slash_date_article, False
            raise OSError("unavailable")

        html_items, html_health = fetch_official_news(
            [html_source],
            datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            storage,
            fake_official_fetcher,
        )
        assert len(html_items) == 1 and html_health.status == "ok"
        assert html_items[0].title == "Official Model Two"
        html_health_payload = html_health.to_dict()
        assert len(html_health_payload["checks"]) == 1
        assert {
            key: html_health_payload["checks"][0][key]
            for key in ("name", "status", "items", "cached")
        } == {"name": "Example Lab", "status": "ok", "items": 1, "cached": 0}
        assert html_health_payload["checks"][0]["detail"].startswith("fetched;")
        empty_index_source: OfficialSource = {
            **html_source,
            "name": "Empty Index Lab",
            "index_url": "https://example.com/empty-news",
        }
        mixed_items, mixed_health = fetch_official_news(
            [html_source, empty_index_source],
            datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            storage,
            fake_official_fetcher,
        )
        assert len(mixed_items) == 1 and mixed_health.status == "warn"
        mixed_checks = mixed_health.to_dict()["checks"]
        assert len(mixed_checks) == 2
        assert {
            key: mixed_checks[1][key]
            for key in ("name", "status", "items", "cached")
        } == {"name": "Empty Index Lab", "status": "error", "items": 0, "cached": 0}
        assert mixed_checks[1]["detail"].startswith(
            "official index contains no matching article links;"
        )
        slash_date_source: OfficialSource = {
            **html_source,
            "name": "Slash Date Lab",
            "index_url": "https://example.com/slash-news",
        }
        slash_date_items, slash_date_health = fetch_official_news(
            [slash_date_source],
            datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            storage,
            fake_official_fetcher,
        )
        assert len(slash_date_items) == 1 and slash_date_health.status == "ok"
        midnight_article = b"""<html><head>
          <meta property="og:title" content="Official Midnight Model">
          <meta property="og:description" content="A concrete official model description with capabilities.">
          <script type="application/ld+json">{"datePublished":"2026-07-31T00:00:00Z"}</script>
        </head></html>"""
        midnight_index = (
            '<a href="/news/midnight-model">Jul 31, 2026 Official Midnight Model '
            'A concrete official model description.</a>'
        ).encode()

        def fake_midnight_fetcher(url: str, _: Storage) -> tuple[bytes, bool]:
            if url == "https://example.com/news":
                return midnight_index, False
            if url == "https://example.com/news/midnight-model":
                return midnight_article, False
            raise OSError("unavailable")

        midnight_items, midnight_health = fetch_official_news(
            [html_source],
            datetime(2026, 7, 31, 0, 30, tzinfo=timezone.utc),
            storage,
            fake_midnight_fetcher,
        )
        assert len(midnight_items) == 1 and midnight_health.status == "ok"
        unsafe_date_source: OfficialSource = {
            **html_source,
            "name": "Dynamic Date Lab",
            "index_url": "https://example.com/dynamic-news",
            "allow_json_date": False,
        }
        unsafe_date_items, unsafe_date_health = fetch_official_news(
            [unsafe_date_source],
            datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            storage,
            fake_official_fetcher,
        )
        assert not unsafe_date_items and unsafe_date_health.status == "ok"
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
        changelog_day_one = ContentItem(
            item_id="changelog-one",
            source_type="official_news",
            source="官方发布 · Example API",
            title="Example API · 2026-07-21 更新",
            published_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            url="https://example.com/changelog",
            raw_source_text="First dated changelog entry with sufficient source detail.",
            extra="官方 Changelog",
        )
        changelog_day_two = ContentItem(
            item_id="changelog-two",
            source_type="official_news",
            source="官方发布 · Example API",
            title="Example API · 2026-07-22 更新",
            published_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
            url="https://example.com/changelog",
            raw_source_text="Second dated changelog entry with sufficient source detail.",
            extra="官方 Changelog",
        )
        storage.add_new_items_to_digest(
            "2026-07-24", [changelog_day_one, changelog_day_two]
        )
        assert len(storage.items_for_digest("2026-07-24")) == 2
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
        replacement_id = "UC" + "C" * 22
        storage.seed_subscriptions(
            [{"name": "Replacement seed", "channel_id": replacement_id}]
        )
        assert storage.subscription_ids() == {new_id, replacement_id}

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
    assertion_count = sum(
        isinstance(node, ast.Assert)
        for node in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8")))
    )
    print(json.dumps({"status": "ok", "assertions": assertion_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
