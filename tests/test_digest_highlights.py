from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from radar.digest import validate_frozen_digest


class DigestHighlightTests(unittest.TestCase):
    def _source(self, count: int) -> dict[str, object]:
        return {
            "items": [
                {
                    "id": f"item-{index}",
                    "source_type": "official_news",
                    "source": f"官方发布 · Vendor {index}",
                    "title": f"Material AI release {index}",
                    "url": f"https://example.com/item-{index}",
                    "source_text_status": "available",
                    "source_text": f"Vendor {index} released a material AI product.",
                    "recency_status": "current",
                    "event_id": f"event-{index}",
                    "verification_status": "first_party",
                    "recommended_highlight": True,
                }
                for index in range(count)
            ]
        }

    def _markdown(self, source: dict[str, object], highlighted: set[int]) -> str:
        blocks = ["# AI 前哨 | 2026-08-18"]
        for index, item in enumerate(source["items"], start=1):
            blocks.append(
                "\n".join(
                    (
                        f"### {index}. [{item['title']}]({item['url']})",
                        f"- 来源：{item['source']}",
                        f"- 重点：{'是' if index - 1 in highlighted else '否'}",
                        f"- 来源摘要：Vendor {index - 1} 发布了一项重要 AI 产品。",
                    )
                )
            )
        return "\n\n".join(blocks) + "\n"

    def test_zero_highlights_is_valid(self) -> None:
        source = self._source(2)
        items = validate_frozen_digest(source, self._markdown(source, set()))
        self.assertFalse(any(item.highlight for item in items))

    def test_more_than_six_highlights_is_rejected(self) -> None:
        source = self._source(7)
        with self.assertRaisesRegex(ValueError, "cannot exceed 6"):
            validate_frozen_digest(source, self._markdown(source, set(range(7))))

    def test_duplicate_event_highlights_are_rejected(self) -> None:
        source = self._source(2)
        source["items"][1]["event_id"] = source["items"][0]["event_id"]
        with self.assertRaisesRegex(ValueError, "one highlight is allowed per event"):
            validate_frozen_digest(source, self._markdown(source, {0, 1}))

    def test_non_recommended_record_cannot_be_highlighted(self) -> None:
        source = self._source(1)
        source["items"][0]["recommended_highlight"] = False
        with self.assertRaisesRegex(ValueError, "deterministic quality gate"):
            validate_frozen_digest(source, self._markdown(source, {0}))


if __name__ == "__main__":
    unittest.main()
