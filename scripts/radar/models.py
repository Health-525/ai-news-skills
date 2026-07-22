"""Shared immutable runtime models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ContentItem:
    item_id: str
    source_type: str
    source: str
    title: str
    published_at: datetime
    url: str
    raw_source_text: str = ""
    recommendation: str = ""
    extra: str = ""

    @property
    def key(self) -> str:
        return f"{self.source_type}:{self.item_id}"


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source: str
    status: str
    fetched: int
    failed: int
    cached: int
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "status": self.status,
            "fetched": self.fetched,
            "failed": self.failed,
            "cached": self.cached,
            "detail": self.detail,
        }
