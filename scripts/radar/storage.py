"""SQLite state, HTTP cache, and atomic private artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Literal

from .models import ContentItem

SCHEMA_VERSION = 3


class _ClosingConnection(sqlite3.Connection):
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Storage:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.database_path = state_dir / "state.db"

    def initialize(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.state_dir, 0o700)
        with self._connect() as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version > SCHEMA_VERSION:
                raise ValueError(
                    f"state database schema {current_version} is newer than supported {SCHEMA_VERSION}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS items (
                    item_key TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    url TEXT NOT NULL,
                    raw_source_text TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    extra TEXT NOT NULL,
                    discovered_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS digest_items (
                    digest_date TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (digest_date, item_key),
                    FOREIGN KEY (item_key) REFERENCES items(item_key)
                );
                CREATE TABLE IF NOT EXISTS http_cache (
                    url TEXT PRIMARY KEY,
                    etag TEXT NOT NULL,
                    last_modified TEXT NOT NULL,
                    body BLOB NOT NULL,
                    fetched_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS github_repository_snapshots (
                    repository TEXT NOT NULL,
                    observed_date TEXT NOT NULL,
                    stars INTEGER NOT NULL,
                    forks INTEGER NOT NULL,
                    pushed_at TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (repository, observed_date)
                );
                CREATE INDEX IF NOT EXISTS idx_github_snapshots_repository
                ON github_repository_snapshots (repository, observed_date DESC);
                CREATE TABLE IF NOT EXISTS subscriptions (
                    channel_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    added_at TEXT NOT NULL,
                    added_by_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscription_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    requester_hash TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    confirmed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_subscription_proposals_requester
                ON subscription_proposals (requester_hash, status, created_at);
                CREATE TABLE IF NOT EXISTS digest_drafts (
                    draft_id TEXT PRIMARY KEY,
                    digest_date TEXT NOT NULL,
                    requester_hash TEXT NOT NULL,
                    target_hash TEXT NOT NULL,
                    cards_json TEXT NOT NULL,
                    cards_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    claimed_at TEXT,
                    sent_at TEXT,
                    rejected_at TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_digest_drafts_requester
                ON digest_drafts (requester_hash, status, created_at);
                CREATE TABLE IF NOT EXISTS item_feedback (
                    item_key TEXT NOT NULL,
                    requester_hash TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (item_key, requester_hash),
                    FOREIGN KEY (item_key) REFERENCES items(item_key)
                );
                CREATE INDEX IF NOT EXISTS idx_item_feedback_updated
                ON item_feedback (updated_at, value);
                CREATE TABLE IF NOT EXISTS collection_runs (
                    digest_date TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    quality_gate_json TEXT NOT NULL,
                    source_health_json TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    available INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transcript_requests (
                    request_id TEXT PRIMARY KEY,
                    requester_hash TEXT NOT NULL,
                    request_date TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT '',
                    is_owner INTEGER NOT NULL,
                    requested_at TEXT NOT NULL,
                    completed_at TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_transcript_daily_quota
                ON transcript_requests (requester_hash, request_date)
                WHERE is_owner = 0 AND status IN ('pending', 'consumed');
                CREATE INDEX IF NOT EXISTS idx_transcript_requests_requested
                ON transcript_requests (requested_at, status);
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def get_http_cache(self, url: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT etag, last_modified, body, fetched_at FROM http_cache WHERE url = ?",
                (url,),
            ).fetchone()
        return dict(row) if row else None

    def put_http_cache(
        self,
        url: str,
        body: bytes,
        etag: str = "",
        last_modified: str = "",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO http_cache (url, etag, last_modified, body, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    etag=excluded.etag,
                    last_modified=excluded.last_modified,
                    body=excluded.body,
                    fetched_at=excluded.fetched_at
                """,
                (url, etag, last_modified, body, datetime.now(timezone.utc).isoformat()),
            )

    def previous_github_snapshot(
        self, repository: str, before_date: str
    ) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT stars, forks, pushed_at, captured_at, observed_date
                FROM github_repository_snapshots
                WHERE repository = ? AND observed_date < ?
                ORDER BY observed_date DESC
                LIMIT 1
                """,
                (repository.casefold(), before_date),
            ).fetchone()
        return dict(row) if row else None

    def put_github_snapshot(
        self,
        repository: str,
        observed_date: str,
        stars: int,
        forks: int,
        pushed_at: datetime,
        captured_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO github_repository_snapshots (
                    repository, observed_date, stars, forks, pushed_at, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, observed_date) DO UPDATE SET
                    stars=excluded.stars,
                    forks=excluded.forks,
                    pushed_at=excluded.pushed_at,
                    captured_at=excluded.captured_at
                """,
                (
                    repository.casefold(),
                    observed_date,
                    stars,
                    forks,
                    pushed_at.isoformat(),
                    captured_at.isoformat(),
                ),
            )

    def add_new_items_to_digest(self, date_str: str, items: list[ContentItem]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            existing_urls = {
                ContentItem(
                    item_id=str(row["item_id"]),
                    source_type=str(row["source_type"]),
                    source=str(row["source"]),
                    title=str(row["title"]),
                    published_at=datetime.fromisoformat(str(row["published_at"])),
                    url=str(row["url"]),
                    extra=str(row["extra"]),
                ).dedup_identity: str(row["item_key"])
                for row in connection.execute(
                    """
                    SELECT item_key, item_id, source_type, source, title,
                           published_at, url, extra
                    FROM items
                    """
                ).fetchall()
            }
            position_row = connection.execute(
                "SELECT COALESCE(MAX(position), 0) AS value FROM digest_items WHERE digest_date = ?",
                (date_str,),
            ).fetchone()
            position = int(position_row["value"])
            for item in items:
                url_key = item.dedup_identity
                existing_item_key = existing_urls.get(url_key)
                if existing_item_key is not None and existing_item_key != item.key:
                    continue
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO items (
                        item_key, item_id, source_type, source, title, published_at,
                        url, raw_source_text, recommendation, extra, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.key,
                        item.item_id,
                        item.source_type,
                        item.source,
                        item.title,
                        item.published_at.isoformat(),
                        item.url,
                        item.raw_source_text,
                        item.recommendation,
                        item.extra,
                        now,
                    ),
                )
                already_in_digest = connection.execute(
                    "SELECT 1 FROM digest_items WHERE digest_date = ? AND item_key = ?",
                    (date_str, item.key),
                ).fetchone()
                if cursor.rowcount != 1 and not already_in_digest:
                    continue
                if not already_in_digest:
                    position += 1
                    connection.execute(
                        "INSERT INTO digest_items (digest_date, item_key, position) VALUES (?, ?, ?)",
                        (date_str, item.key, position),
                    )
                existing_urls[url_key] = item.key

    def items_for_digest(self, date_str: str) -> list[ContentItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT i.* FROM digest_items d
                JOIN items i ON i.item_key = d.item_key
                WHERE d.digest_date = ?
                ORDER BY i.published_at DESC, d.position ASC
                """,
                (date_str,),
            ).fetchall()
        return [
            ContentItem(
                item_id=row["item_id"],
                source_type=row["source_type"],
                source=row["source"],
                title=row["title"],
                published_at=datetime.fromisoformat(row["published_at"]),
                url=row["url"],
                raw_source_text=row["raw_source_text"],
                recommendation=row["recommendation"],
                extra=row["extra"],
            )
            for row in rows
        ]

    @staticmethod
    def identity_hash(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identity must not be empty")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def reserve_transcript_request(
        self,
        requester_id: str,
        request_date: str,
        video_id: str,
        *,
        is_owner: bool,
    ) -> str:
        requester_hash = self.identity_hash(requester_id)
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(minutes=10)).isoformat()
        request_id = secrets.token_urlsafe(18)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE transcript_requests
                SET status = 'failed', completed_at = ?, last_error = 'stale reservation'
                WHERE status = 'pending' AND requested_at < ?
                """,
                (now.isoformat(), stale_before),
            )
            if not is_owner:
                existing = connection.execute(
                    """
                    SELECT 1 FROM transcript_requests
                    WHERE requester_hash = ? AND request_date = ?
                      AND status IN ('pending', 'consumed')
                    LIMIT 1
                    """,
                    (requester_hash, request_date),
                ).fetchone()
                if existing:
                    raise ValueError("daily transcript quota already used")
            connection.execute(
                """
                INSERT INTO transcript_requests (
                    request_id, requester_hash, request_date, video_id, status,
                    is_owner, requested_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    request_id,
                    requester_hash,
                    request_date,
                    video_id,
                    int(is_owner),
                    now.isoformat(),
                ),
            )
        return request_id

    def finish_transcript_request(
        self,
        request_id: str,
        *,
        consumed: bool,
        outcome: str,
        error: str = "",
    ) -> None:
        status = "consumed" if consumed else "failed"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE transcript_requests
                SET status = ?, outcome = ?, completed_at = ?, last_error = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (
                    status,
                    outcome[:40],
                    datetime.now(timezone.utc).isoformat(),
                    error[:240],
                    request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("transcript request is not pending")

    def seed_subscriptions(self, channels: list[dict[str, str]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        bundled_ids = [channel["channel_id"] for channel in channels]
        with self._connect() as connection:
            if bundled_ids:
                placeholders = ",".join("?" for _ in bundled_ids)
                connection.execute(
                    f"""
                    UPDATE subscriptions SET status = 'inactive'
                    WHERE added_by_hash = 'bundled-seed'
                      AND channel_id NOT IN ({placeholders})
                    """,
                    bundled_ids,
                )
            connection.executemany(
                """
                INSERT OR IGNORE INTO subscriptions (
                    channel_id, name, source_url, status, added_at, added_by_hash
                ) VALUES (?, ?, ?, 'active', ?, 'bundled-seed')
                """,
                (
                    (
                        channel["channel_id"],
                        channel["name"],
                        f"https://www.youtube.com/channel/{channel['channel_id']}",
                        now,
                    )
                    for channel in channels
                ),
            )
            connection.executemany(
                """
                UPDATE subscriptions SET status = 'active', name = ?, source_url = ?
                WHERE channel_id = ? AND added_by_hash = 'bundled-seed'
                """,
                (
                    (
                        channel["name"],
                        f"https://www.youtube.com/channel/{channel['channel_id']}",
                        channel["channel_id"],
                    )
                    for channel in channels
                ),
            )

    def active_channels(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT name, channel_id FROM subscriptions
                WHERE status = 'active'
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        return [{"name": row["name"], "channel_id": row["channel_id"]} for row in rows]

    def subscription_ids(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT channel_id FROM subscriptions WHERE status = 'active'"
            ).fetchall()
        return {row["channel_id"] for row in rows}

    def create_subscription_proposal(
        self,
        requester_id: str,
        results: list[dict[str, str]],
        *,
        expires_hours: int = 24,
    ) -> str:
        if not 1 <= expires_hours <= 72:
            raise ValueError("proposal expiry must be between 1 and 72 hours")
        now = datetime.now(timezone.utc)
        proposal_id = f"sub-{secrets.token_hex(4)}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO subscription_proposals (
                    proposal_id, requester_hash, results_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    self.identity_hash(requester_id),
                    json.dumps(results, ensure_ascii=False, separators=(",", ":")),
                    now.isoformat(),
                    (now + timedelta(hours=expires_hours)).isoformat(),
                ),
            )
        return proposal_id

    def _subscription_proposal(
        self,
        requester_id: str,
        proposal_id: str | None,
    ) -> sqlite3.Row:
        now = datetime.now(timezone.utc).isoformat()
        requester_hash = self.identity_hash(requester_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE subscription_proposals SET status = 'expired'
                WHERE status = 'pending' AND expires_at <= ?
                """,
                (now,),
            )
            arguments: list[str] = [requester_hash]
            proposal_filter = ""
            if proposal_id:
                proposal_filter = " AND proposal_id = ?"
                arguments.append(proposal_id)
            rows = connection.execute(
                f"""
                SELECT * FROM subscription_proposals
                WHERE requester_hash = ? AND status = 'pending'{proposal_filter}
                ORDER BY created_at DESC
                """,
                arguments,
            ).fetchall()
        if not rows:
            raise ValueError("no pending subscription proposal for this requester")
        if len(rows) > 1:
            raise ValueError("multiple proposals are pending; specify proposal_id")
        return rows[0]

    def confirm_subscription_proposal(
        self,
        requester_id: str,
        proposal_id: str | None,
    ) -> tuple[str, int]:
        proposal = self._subscription_proposal(requester_id, proposal_id)
        results = json.loads(proposal["results_json"])
        valid = [result for result in results if result.get("status") == "valid"]
        now = datetime.now(timezone.utc).isoformat()
        requester_hash = self.identity_hash(requester_id)
        with self._connect() as connection:
            for result in valid:
                connection.execute(
                    """
                    INSERT INTO subscriptions (
                        channel_id, name, source_url, status, added_at, added_by_hash
                    ) VALUES (?, ?, ?, 'active', ?, ?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        name=excluded.name,
                        source_url=excluded.source_url,
                        status='active',
                        added_at=excluded.added_at,
                        added_by_hash=excluded.added_by_hash
                    """,
                    (
                        result["channel_id"],
                        result["name"],
                        result["channel_url"],
                        now,
                        requester_hash,
                    ),
                )
            connection.execute(
                """
                UPDATE subscription_proposals
                SET status = 'confirmed', confirmed_at = ?
                WHERE proposal_id = ? AND status = 'pending'
                """,
                (now, proposal["proposal_id"]),
            )
        return str(proposal["proposal_id"]), len(valid)

    def cancel_subscription_proposal(
        self,
        requester_id: str,
        proposal_id: str | None,
    ) -> str:
        proposal = self._subscription_proposal(requester_id, proposal_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE subscription_proposals SET status = 'cancelled' WHERE proposal_id = ?",
                (proposal["proposal_id"],),
            )
        return str(proposal["proposal_id"])

    def create_digest_draft(
        self,
        date_str: str,
        requester_id: str,
        target_id: str,
        cards: list[dict],
        *,
        expires_hours: int = 24,
    ) -> tuple[str, bool]:
        requester_hash = self.identity_hash(requester_id)
        target_hash = self.identity_hash(target_id)
        cards_json = json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        cards_sha256 = hashlib.sha256(cards_json.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT draft_id FROM digest_drafts
                WHERE digest_date = ? AND requester_hash = ? AND target_hash = ?
                  AND cards_sha256 = ? AND status = 'pending' AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (date_str, requester_hash, target_hash, cards_sha256, now.isoformat()),
            ).fetchone()
            if existing:
                return str(existing["draft_id"]), False
            connection.execute(
                """
                UPDATE digest_drafts SET status = 'superseded'
                WHERE digest_date = ? AND requester_hash = ? AND status = 'pending'
                """,
                (date_str, requester_hash),
            )
            draft_id = f"digest-{date_str.replace('-', '')}-{secrets.token_hex(3)}"
            connection.execute(
                """
                INSERT INTO digest_drafts (
                    draft_id, digest_date, requester_hash, target_hash, cards_json,
                    cards_sha256, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    date_str,
                    requester_hash,
                    target_hash,
                    cards_json,
                    cards_sha256,
                    now.isoformat(),
                    (now + timedelta(hours=expires_hours)).isoformat(),
                ),
            )
        return draft_id, True

    def claim_digest_draft(
        self,
        requester_id: str,
        target_id: str,
        draft_id: str | None,
    ) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(minutes=15)).isoformat()
        requester_hash = self.identity_hash(requester_id)
        target_hash = self.identity_hash(target_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE digest_drafts SET status = 'expired'
                WHERE status = 'pending' AND expires_at <= ?
                """,
                (now.isoformat(),),
            )
            arguments: list[str] = [requester_hash, target_hash, stale_before]
            draft_filter = ""
            if draft_id:
                draft_filter = " AND draft_id = ?"
                arguments.append(draft_id)
            rows = connection.execute(
                f"""
                SELECT * FROM digest_drafts
                WHERE requester_hash = ? AND target_hash = ?
                  AND (status = 'pending' OR (status = 'sending' AND claimed_at < ?))
                  {draft_filter}
                ORDER BY created_at DESC
                """,
                arguments,
            ).fetchall()
            if not rows:
                connection.rollback()
                raise ValueError("no deliverable digest draft for this requester")
            if len(rows) > 1:
                connection.rollback()
                raise ValueError("multiple digest drafts are pending; specify draft_id")
            row = rows[0]
            connection.execute(
                """
                UPDATE digest_drafts SET status = 'sending', claimed_at = ?, last_error = ''
                WHERE draft_id = ?
                """,
                (now.isoformat(), row["draft_id"]),
            )
            connection.commit()
        return {
            "draft_id": row["draft_id"],
            "date": row["digest_date"],
            "cards": json.loads(row["cards_json"]),
        }

    def mark_digest_sent(self, draft_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE digest_drafts SET status = 'sent', sent_at = ?, claimed_at = NULL
                WHERE draft_id = ? AND status = 'sending'
                """,
                (datetime.now(timezone.utc).isoformat(), draft_id),
            )

    def mark_digest_failed(self, draft_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE digest_drafts SET status = 'pending', claimed_at = NULL, last_error = ?
                WHERE draft_id = ? AND status = 'sending'
                """,
                (error[:1000], draft_id),
            )

    def reject_digest_draft(self, requester_id: str, draft_id: str | None) -> str:
        requester_hash = self.identity_hash(requester_id)
        with self._connect() as connection:
            arguments: list[str] = [requester_hash]
            draft_filter = ""
            if draft_id:
                draft_filter = " AND draft_id = ?"
                arguments.append(draft_id)
            rows = connection.execute(
                f"""
                SELECT draft_id FROM digest_drafts
                WHERE requester_hash = ? AND status = 'pending'{draft_filter}
                ORDER BY created_at DESC
                """,
                arguments,
            ).fetchall()
            if not rows:
                raise ValueError("no pending digest draft for this requester")
            if len(rows) > 1:
                raise ValueError("multiple digest drafts are pending; specify draft_id")
            selected = str(rows[0]["draft_id"])
            connection.execute(
                """
                UPDATE digest_drafts SET status = 'rejected', rejected_at = ?
                WHERE draft_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), selected),
            )
        return selected

    def digest_draft_status(
        self,
        requester_id: str,
        target_id: str,
        draft_id: str,
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status FROM digest_drafts
                WHERE draft_id = ? AND requester_hash = ? AND target_hash = ?
                """,
                (
                    draft_id,
                    self.identity_hash(requester_id),
                    self.identity_hash(target_id),
                ),
            ).fetchone()
        return str(row["status"]) if row else None

    def record_feedback(self, requester_id: str, item_id: str, value: str) -> str:
        if value not in {"useful", "not_useful"}:
            raise ValueError("feedback value must be useful or not_useful")
        normalized_id = item_id.strip()
        if not normalized_id:
            raise ValueError("feedback item_id must not be empty")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT item_key FROM items
                WHERE item_key = ? OR item_id = ?
                ORDER BY discovered_at DESC LIMIT 1
                """,
                (normalized_id, normalized_id),
            ).fetchone()
            if not row:
                raise ValueError("feedback item was not found")
            item_key = str(row["item_key"])
            connection.execute(
                """
                INSERT INTO item_feedback (
                    item_key, requester_hash, value, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_key, requester_hash) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (item_key, self.identity_hash(requester_id), value, now, now),
            )
        return item_key

    def feedback_summary(self, since: datetime) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT value, COUNT(*) AS total FROM item_feedback
                WHERE updated_at >= ? GROUP BY value
                """,
                (since.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
        counts = {"useful": 0, "not_useful": 0}
        counts.update({str(row["value"]): int(row["total"]) for row in rows})
        return counts

    def feedback_items(
        self, requester_id: str, limit: int = 200
    ) -> list[tuple[ContentItem, str]]:
        if not 1 <= limit <= 1000:
            raise ValueError("feedback item limit must be 1 through 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT i.*, f.value FROM item_feedback f
                JOIN items i ON i.item_key = f.item_key
                WHERE f.requester_hash = ?
                ORDER BY f.updated_at DESC
                LIMIT ?
                """,
                (self.identity_hash(requester_id), limit),
            ).fetchall()
        return [
            (
                ContentItem(
                    item_id=row["item_id"],
                    source_type=row["source_type"],
                    source=row["source"],
                    title=row["title"],
                    published_at=datetime.fromisoformat(row["published_at"]),
                    url=row["url"],
                    raw_source_text=row["raw_source_text"],
                    recommendation=row["recommendation"],
                    extra=row["extra"],
                ),
                str(row["value"]),
            )
            for row in rows
        ]

    def items_since(self, since: datetime) -> list[ContentItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM items WHERE published_at >= ?
                ORDER BY published_at DESC
                """,
                (since.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
        return [
            ContentItem(
                item_id=row["item_id"],
                source_type=row["source_type"],
                source=row["source"],
                title=row["title"],
                published_at=datetime.fromisoformat(row["published_at"]),
                url=row["url"],
                raw_source_text=row["raw_source_text"],
                recommendation=row["recommendation"],
                extra=row["extra"],
            )
            for row in rows
        ]

    def maintenance(self, retention_days: int = 30, *, dry_run: bool = True) -> dict[str, object]:
        if not 7 <= retention_days <= 365:
            raise ValueError("retention_days must be 7 through 365")
        now = datetime.now(timezone.utc)
        general_cutoff = (now - timedelta(days=retention_days)).isoformat()
        snapshot_cutoff = (now - timedelta(days=max(retention_days, 180))).date().isoformat()
        statements = {
            "http_cache": ("fetched_at < ?", general_cutoff),
            "subscription_proposals": ("expires_at < ? AND status != 'pending'", general_cutoff),
            "digest_drafts": ("expires_at < ? AND status NOT IN ('pending', 'sending')", general_cutoff),
            "github_repository_snapshots": ("observed_date < ?", snapshot_cutoff),
            "transcript_requests": ("requested_at < ? AND status != 'pending'", general_cutoff),
        }
        counts: dict[str, int] = {}
        with self._connect() as connection:
            for table, (where, value) in statements.items():
                count = connection.execute(
                    f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", (value,)
                ).fetchone()
                counts[table] = int(count["total"])
                if not dry_run:
                    connection.execute(f"DELETE FROM {table} WHERE {where}", (value,))
        return {
            "status": "dry_run" if dry_run else "completed",
            "retention_days": retention_days,
            "eligible_rows": counts,
            "deleted_rows": 0 if dry_run else sum(counts.values()),
            "schema_version": SCHEMA_VERSION,
        }

    def record_collection_run(
        self,
        date_str: str,
        status: str,
        quality_gate: dict[str, object],
        source_health: list[dict[str, object]],
        total: int,
        available: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO collection_runs (
                    digest_date, generated_at, status, quality_gate_json,
                    source_health_json, total, available
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(digest_date) DO UPDATE SET
                    generated_at=excluded.generated_at,
                    status=excluded.status,
                    quality_gate_json=excluded.quality_gate_json,
                    source_health_json=excluded.source_health_json,
                    total=excluded.total,
                    available=excluded.available
                """,
                (
                    date_str,
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    json.dumps(quality_gate, ensure_ascii=False, sort_keys=True),
                    json.dumps(source_health, ensure_ascii=False, sort_keys=True),
                    total,
                    available,
                ),
            )

    def collection_run_summary(self, since_date: str) -> dict[str, object]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, total, available, source_health_json
                FROM collection_runs WHERE digest_date >= ? ORDER BY digest_date
                """,
                (since_date,),
            ).fetchall()
        source_failures = 0
        for row in rows:
            health = json.loads(row["source_health_json"])
            source_failures += sum(int(entry.get("failed", 0)) for entry in health)
        total = sum(int(row["total"]) for row in rows)
        available = sum(int(row["available"]) for row in rows)
        return {
            "runs": len(rows),
            "failed_runs": sum(str(row["status"]) == "failed" for row in rows),
            "signals": total,
            "availability_ratio": round(available / total, 4) if total else 0,
            "source_failures": source_failures,
        }


def atomic_write_json(path: Path, payload: object) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)
