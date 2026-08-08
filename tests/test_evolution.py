from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from radar.huggingface_radar import fetch_huggingface_models, load_huggingface_radar_config
from radar.digest import FrozenItem, build_card, build_cards
from radar.intelligence import classify_item, verify_source_payload
from radar.models import ContentItem, SourceCheck, SourceHealth
from radar.security_advisories import (
    fetch_security_advisories,
    load_security_advisory_config,
)
from radar.storage import SCHEMA_VERSION, Storage, atomic_write_json, atomic_write_text
from radar.timezones import load_report_timezone
from radar.trends import build_trend_report
from radar.workflow import artifact_paths, daily_lock, evaluate_source_health, render_cards
from package_skill import build_archive


class EvolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temporary.name))
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_configs_and_schema_are_valid(self) -> None:
        security = load_security_advisory_config(ROOT / "references" / "security-advisories.json")
        models = load_huggingface_radar_config(ROOT / "references" / "huggingface-radar.json")
        self.assertGreaterEqual(len(security["packages"]), 15)
        self.assertGreaterEqual(len(models["organizations"]), 10)
        with self.storage._connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_report_timezone_falls_back_to_utc_plus_eight(self) -> None:
        with mock.patch(
            "radar.timezones.ZoneInfo",
            side_effect=ZoneInfoNotFoundError("Asia/Shanghai"),
        ):
            zone = load_report_timezone()
        offset = datetime(2026, 8, 6, tzinfo=zone).utcoffset()
        self.assertEqual(offset, timedelta(hours=8))

    def test_security_advisory_is_bounded_to_allowlist(self) -> None:
        config = {
            "packages": [{"ecosystem": "pip", "name": "open-webui"}],
            "severities": ["high"],
            "max_items": 5,
        }
        payload = [
            {
                "ghsa_id": "GHSA-test-1234",
                "cve_id": "CVE-2026-0001",
                "summary": "Open WebUI authorization bypass",
                "description": "A detailed reviewed advisory with impact and remediation guidance.",
                "html_url": "https://github.com/advisories/GHSA-test-1234",
                "published_at": "2026-08-05T00:00:00Z",
                "updated_at": "2026-08-05T01:00:00Z",
                "vulnerabilities": [
                    {
                        "package": {"ecosystem": "pip", "name": "open-webui"},
                        "vulnerable_version_range": ">= 0.10, < 0.11",
                        "first_patched_version": "0.11",
                    }
                ],
            }
        ]
        items, health = fetch_security_advisories(
            config,
            datetime(2026, 8, 4, tzinfo=timezone.utc),
            self.storage,
            lambda _url, _storage: (payload, False),
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source_type, "security_advisory")
        self.assertEqual(health.status, "ok")

    def test_huggingface_radar_uses_creation_time_and_org_allowlist(self) -> None:
        config = {
            "organizations": ["Qwen"],
            "max_candidates_per_organization": 5,
            "max_items": 5,
        }
        payload = [
            {
                "id": "Qwen/Qwen-Test",
                "createdAt": "2026-08-05T00:00:00Z",
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "downloads": 100,
                "likes": 10,
                "tags": ["license:apache-2.0"],
            },
            {
                "id": "untrusted/Other",
                "createdAt": "2026-08-05T00:00:00Z",
            },
        ]
        items, health = fetch_huggingface_models(
            config,
            datetime(2026, 8, 4, tzinfo=timezone.utc),
            self.storage,
            lambda _url, _storage: (payload, False),
        )
        self.assertEqual([item.item_id for item in items], ["Qwen/Qwen-Test"])
        self.assertEqual(health.status, "ok")

    def test_provenance_detects_tampering(self) -> None:
        item = ContentItem(
            item_id="release-1",
            source_type="official_news",
            source="OpenAI",
            title="OpenAI releases a secure agent API",
            published_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            url="https://openai.com/example",
            raw_source_text="OpenAI states that the release adds a secure agent API for developers.",
        )
        record = {
            "id": item.item_id,
            "source_text": item.raw_source_text,
            **classify_item(item),
        }
        record["record_sha256"] = hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        records = [record]
        payload = {
            "schema_version": 2,
            "items": records,
            "provenance": {
                "source_set_sha256": hashlib.sha256(
                    json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
            },
        }
        verify_source_payload(payload)
        record["source_text"] = "tampered"
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            verify_source_payload(payload)

    def test_runtime_archive_excludes_repository_only_files(self) -> None:
        output = Path(self.temporary.name) / "skill.zip"
        result = build_archive(output, "a" * 40)
        self.assertEqual(result["status"], "built")
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
        self.assertIn("SKILL.md", names)
        self.assertIn(".deployment-commit", names)
        self.assertIn("scripts/radar/security_advisories.py", names)
        self.assertIn("scripts/radar/newsroom.py", names)
        self.assertNotIn("README.md", names)
        self.assertFalse(any(name.startswith("tests/") for name in names))

    def test_schema_v2_source_to_card_artifact_is_auditable(self) -> None:
        original_state = os.environ.get("AI_NEWS_STATE_DIR")
        os.environ["AI_NEWS_STATE_DIR"] = self.temporary.name
        try:
            item = ContentItem(
                item_id="audit-1",
                source_type="official_news",
                source="OpenAI",
                title="Auditable agent API release",
                published_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
                url="https://openai.com/audit-1",
                raw_source_text="OpenAI states that this agent API release adds auditable enterprise controls.",
            )
            record = {
                "id": item.item_id,
                "source_type": item.source_type,
                "source": item.source,
                "title": item.title,
                "published_at": item.published_at.isoformat(),
                "recency_status": "current",
                "url": item.url,
                "source_text_status": "available",
                "source_text": item.raw_source_text,
                "unavailable_reason": "",
                "recommendation": "",
                "extra": item.extra,
                **classify_item(item),
            }
            record["record_sha256"] = hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            records = [record]
            payload = {
                "schema_version": 2,
                "date": "2026-08-06",
                "items": records,
                "provenance": {
                    "source_set_sha256": hashlib.sha256(
                        json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "code_version": "development",
                },
            }
            paths = artifact_paths("2026-08-06")
            atomic_write_json(paths["source"], payload)
            atomic_write_text(
                paths["digest"],
                "# AI 前哨 | 2026-08-06\n\n"
                "### 1. [Auditable agent API release](https://openai.com/audit-1)\n"
                "- 来源：OpenAI\n- 重点：是\n"
                "- 来源摘要：OpenAI states that this agent API release adds auditable enterprise controls.\n",
            )
            code, result = render_cards("2026-08-06")
            self.assertEqual(code, 0, result)
            cards_payload = json.loads(paths["cards"].read_text(encoding="utf-8"))
            self.assertEqual(cards_payload["schema_version"], 2)
            self.assertEqual(cards_payload["source_set_sha256"], payload["provenance"]["source_set_sha256"])
            self.assertEqual(len(cards_payload["digest_sha256"]), 64)
            self.assertEqual(len(cards_payload["cards_sha256"]), 64)
        finally:
            if original_state is None:
                os.environ.pop("AI_NEWS_STATE_DIR", None)
            else:
                os.environ["AI_NEWS_STATE_DIR"] = original_state

    def test_card_renders_security_and_models_without_internal_labels(self) -> None:
        items = [
            FrozenItem(
                "ghsa",
                "security_advisory",
                "GitHub Advisory Database",
                "Reviewed AI dependency advisory",
                "https://github.com/advisories/GHSA-test",
                "GitHub reviewed an affected dependency range and published a patched version.",
                "",
                True,
                signal_type="security",
                evidence_level="reviewed_advisory",
                topics=("security",),
                audiences=("security", "engineering"),
            ),
            FrozenItem(
                "model",
                "model_hub",
                "Hugging Face · Qwen",
                "Qwen 发布模型 Test",
                "https://huggingface.co/Qwen/Test",
                "Hugging Face metadata records a new repository from the allowlisted uploader.",
                "",
                False,
                signal_type="model_release",
                evidence_level="platform_metadata",
                topics=("models",),
                audiences=("engineering", "product"),
            ),
        ]
        card_text = json.dumps(build_card("2026-08-06", items), ensure_ascii=False)
        self.assertIn("AI 安全雷达", card_text)
        self.assertIn("模型 Hub 雷达", card_text)
        self.assertNotIn("面向 security/engineering", card_text)
        self.assertNotIn("reviewed_advisory", card_text)
        self.assertEqual(items[0].audiences, ("security", "engineering"))
        self.assertEqual(items[0].evidence_level, "reviewed_advisory")

    def test_cards_separate_current_and_recovered_with_compact_folds(self) -> None:
        items = [
            FrozenItem(
                "current-highlight",
                "official_news",
                "官方发布 · Example",
                "Current highlight",
                "https://example.com/current-highlight",
                "Current highlight full summary.",
                "",
                True,
            ),
            FrozenItem(
                "current-folded",
                "youtube",
                "YouTube · Example",
                "Current folded item",
                "https://example.com/current-folded",
                "Current folded summary must not consume card space.",
                "",
                False,
            ),
            FrozenItem(
                "recovered-folded",
                "aihot",
                "AIHOT · Example",
                "Recovered folded item",
                "https://example.com/recovered-folded",
                "Recovered summary must not consume card space.",
                "",
                False,
                recency_status="recovered",
            ),
        ]

        cards = build_cards("2026-08-08", items)

        self.assertEqual(len(cards), 2)
        current_text = json.dumps(cards[0], ensure_ascii=False)
        recovered_text = json.dumps(cards[1], ensure_ascii=False)
        self.assertIn("📗 AI 前哨｜", current_text)
        self.assertIn("Current highlight full summary.", current_text)
        self.assertIn("Current folded item", current_text)
        self.assertNotIn("Current folded summary", current_text)
        self.assertIn("📙 AI 前哨补录｜", recovered_text)
        self.assertIn("Recovered folded item", recovered_text)
        self.assertNotIn("Recovered summary", recovered_text)
        self.assertLess(len(json.dumps(cards[0], ensure_ascii=False).encode("utf-8")), 25_000)
        self.assertLess(len(json.dumps(cards[1], ensure_ascii=False).encode("utf-8")), 25_000)

    def test_quality_gate_and_owner_feedback_feed_trends(self) -> None:
        checks = tuple(SourceCheck(f"source-{index}", "ok" if index < 7 else "error", 0) for index in range(10))
        health = [SourceHealth("official_news", "warn", 7, 3, 0, checks=checks)]
        self.assertEqual(evaluate_source_health(health)["status"], "passed")
        original = os.environ.get("AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO")
        os.environ["AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO"] = "0.8"
        try:
            self.assertEqual(evaluate_source_health(health)["status"], "failed")
        finally:
            if original is None:
                os.environ.pop("AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO", None)
            else:
                os.environ["AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO"] = original

        item = ContentItem(
            item_id="trend-1",
            source_type="official_news",
            source="OpenAI",
            title="Agent security API release",
            published_at=datetime.now(timezone.utc),
            url="https://openai.com/trend-1",
            raw_source_text="A detailed agent security API release with enterprise controls.",
        )
        self.storage.add_new_items_to_digest(datetime.now().date().isoformat(), [item])
        self.storage.record_feedback("owner", item.item_id, "useful")
        report, markdown = build_trend_report(
            self.storage, datetime.now().date().isoformat(), 7
        )
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["feedback"]["useful"], 1)
        self.assertIn("AI 情报趋势", markdown)

    @unittest.skipUnless(os.name == "nt", "Windows lock recovery")
    def test_stale_windows_lock_is_recovered(self) -> None:
        lock = Path(self.temporary.name) / "daily.lock"
        lock.write_text("999999", encoding="ascii")
        stale = datetime.now(timezone.utc).timestamp() - 7 * 60 * 60
        os.utime(lock, (stale, stale))
        with daily_lock(lock):
            self.assertTrue(lock.exists())
        self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
