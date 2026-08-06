from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from radar.newsroom import build_breaking_report, enrich_and_rank_records


class NewsroomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 6, 8, tzinfo=timezone.utc)

    def _record(self, item_id: str, **changes: object) -> dict[str, object]:
        record: dict[str, object] = {
            "id": item_id,
            "source_type": "official_news",
            "source": "官方发布 · OpenAI",
            "title": "OpenAI launches GPT-6 model",
            "published_at": (self.now - timedelta(hours=2)).isoformat(),
            "url": f"https://openai.com/{item_id}",
            "source_text_status": "available",
            "source_text": "OpenAI announced the GPT-6 model.",
            "signal_type": "model_release",
            "topics": ["models"],
            "entities": ["OpenAI"],
            "audiences": ["engineering", "product"],
            "evidence_level": "first_party",
        }
        record.update(changes)
        return record

    def test_event_graph_distinguishes_publisher_from_channel(self) -> None:
        official = self._record("official")
        channel = self._record(
            "channel",
            source_type="youtube",
            source="YouTube · OpenAI",
            title="GPT-6 launch explained",
            url="https://youtube.com/watch?v=gpt6",
            evidence_level="publisher_description",
        )
        records = enrich_and_rank_records([official, channel], self.now)
        self.assertEqual(len({record["event_id"] for record in records}), 1)
        self.assertEqual({record["source_diversity"] for record in records}, {1})
        self.assertEqual({record["verification_status"] for record in records}, {"first_party"})

    def test_independent_publisher_creates_cross_verified_event(self) -> None:
        official = self._record("official")
        editorial = self._record(
            "editorial",
            source_type="industry_digest",
            source="TechCrunch AI",
            title="OpenAI launches GPT-6 model for developers",
            url="https://techcrunch.com/gpt-6",
            evidence_level="editorial_synthesis",
        )
        records = enrich_and_rank_records([official, editorial], self.now)
        self.assertEqual(len({record["event_id"] for record in records}), 1)
        self.assertEqual({record["source_diversity"] for record in records}, {2})
        self.assertEqual(
            {record["verification_status"] for record in records}, {"cross_verified"}
        )

    def test_opaque_version_notice_is_downranked(self) -> None:
        feature = self._record("feature", signal_type="api_update")
        version = self._record(
            "version",
            title="v2.1.223",
            url="https://openai.com/v2.1.223",
            signal_type="api_update",
        )
        records = enrich_and_rank_records([feature, version], self.now)
        by_id = {record["id"]: record for record in records}
        self.assertGreater(by_id["feature"]["rank_score"], by_id["version"]["rank_score"])
        self.assertEqual(
            by_id["version"]["rank_components"]["specificity_adjustment"], -15.0
        )

    def test_shared_vendor_product_does_not_merge_distinct_cases(self) -> None:
        first = self._record(
            "first",
            source="官方发布 · AWS AI",
            entities=["AWS"],
            title="Run production agents in n8n with Amazon Bedrock AgentCore",
            url="https://aws.amazon.com/n8n-agentcore",
            signal_type="infrastructure",
            topics=["agents", "infrastructure"],
        )
        second = self._record(
            "second",
            source="官方发布 · AWS AI",
            entities=["AWS"],
            title="Automated web insight extraction with Amazon Bedrock AgentCore",
            url="https://aws.amazon.com/web-insight-agentcore",
            signal_type="infrastructure",
            topics=["agents", "infrastructure"],
        )
        records = enrich_and_rank_records([first, second], self.now)
        self.assertEqual(len({record["event_id"] for record in records}), 2)

    def test_breaking_report_emits_one_leader_per_event(self) -> None:
        records = enrich_and_rank_records(
            [
                self._record("official"),
                self._record(
                    "editorial",
                    source_type="industry_digest",
                    source="TechCrunch AI",
                    title="OpenAI launches GPT-6 model for developers",
                    url="https://techcrunch.com/gpt-6",
                    evidence_level="editorial_synthesis",
                ),
            ],
            self.now,
        )
        report, markdown = build_breaking_report(
            {"date": "2026-08-06", "items": records}, minimum_score=0
        )
        self.assertEqual(report["total"], 1)
        self.assertIn("GPT-6", markdown)

    def test_breaking_report_applies_marginal_diversity(self) -> None:
        items: list[dict[str, object]] = []
        for index in range(5):
            items.append(
                {
                    "id": f"security-{index}",
                    "event_id": f"security-event-{index}",
                    "title": f"GHSA-{index} · Open WebUI: advisory {index}",
                    "url": f"https://github.com/advisories/{index}",
                    "source": "GitHub Advisory Database",
                    "source_type": "security_advisory",
                    "signal_type": "security",
                    "rank_score": 95 - index,
                    "rank_position": index + 1,
                    "alert_level": "high",
                    "change_type": "advisory",
                }
            )
        for index, entity in enumerate(("OpenAI", "Google", "Anthropic", "Meta")):
            items.append(
                {
                    "id": f"release-{index}",
                    "event_id": f"release-event-{index}",
                    "title": f"{entity} model release",
                    "url": f"https://example.com/{entity}",
                    "source": entity,
                    "source_type": "official_news",
                    "signal_type": "model_release",
                    "entities": [entity],
                    "rank_score": 89 - index,
                    "rank_position": 10 + index,
                    "alert_level": "high",
                    "change_type": "release",
                }
            )
        report, _ = build_breaking_report(
            {"date": "2026-08-06", "items": items}, limit=6, minimum_score=0
        )
        selected_security = [
            item for item in report["items"] if item["source_type"] == "security_advisory"
        ]
        self.assertEqual(len(selected_security), 2)


if __name__ == "__main__":
    unittest.main()
