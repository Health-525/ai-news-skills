"""Prepare source artifacts and validate frozen digest cards."""

from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .digest import build_cards, validate_frozen_digest
from .official_news import load_official_sources
from .source_material import source_text_status
from .sources import collect_sources, load_builders_x_accounts, load_channels
from .storage import Storage, atomic_write_json

RUNTIME_ENV_KEYS = {
    "AI_NEWS_AUTO_GROUP_DELIVERY",
    "AI_NEWS_FEISHU_PERSONAL_TARGET",
    "AI_NEWS_FEISHU_GROUP_TARGET",
    "AI_NEWS_OFFICIAL_SOURCES_FILE",
    "AI_NEWS_OWNER_ID",
    "OPENCLAW_FEISHU_ACCOUNT_ID",
}


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def state_dir() -> Path:
    configured = os.environ.get("AI_NEWS_STATE_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".openclaw" / "state" / "ai-news-skills"


def load_runtime_env() -> None:
    """Load private deployment values without overriding the process environment."""
    env_path = state_dir() / "runtime.env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in RUNTIME_ENV_KEYS and key not in os.environ:
            os.environ[key] = value.strip()


def scheduled_group_delivery_enabled() -> bool:
    return os.environ.get("AI_NEWS_AUTO_GROUP_DELIVERY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def channels_file() -> Path:
    configured = os.environ.get("AI_NEWS_YOUTUBE_CHANNELS_FILE", "").strip()
    return Path(configured).expanduser() if configured else skill_root() / "references" / "youtube-channels.json"


def builders_x_accounts_file() -> Path:
    return skill_root() / "references" / "builders-x-accounts.json"


def official_news_sources_file() -> Path:
    configured = os.environ.get("AI_NEWS_OFFICIAL_SOURCES_FILE", "").strip()
    return (
        Path(configured).expanduser()
        if configured
        else skill_root() / "references" / "official-news-sources.json"
    )


def artifact_paths(date_str: str) -> dict[str, Path]:
    root = state_dir()
    reports = root / "reports"
    return {
        "state": root,
        "source": reports / f"{date_str}_rss_sources.json",
        "digest": reports / f"{date_str}_digest.md",
        "cards": reports / f"{date_str}_cards.json",
        "receipt": root / "receipts" / f"{date_str}.json",
        "lock": root / "locks" / "daily.lock",
    }


def _nearest_existing(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def doctor() -> dict[str, object]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    add(
        "python",
        "ok" if sys.version_info >= (3, 11) else "error",
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )
    try:
        channels = load_channels(channels_file())
    except ValueError as error:
        add("youtube-channels", "error", str(error))
    else:
        add("youtube-channels", "ok", f"{len(channels)} valid channels")
    try:
        builders_x_accounts = load_builders_x_accounts(builders_x_accounts_file())
    except ValueError as error:
        add("builders-x-accounts", "error", str(error))
    else:
        add(
            "builders-x-accounts",
            "ok",
            f"{len(builders_x_accounts)} valid allowlisted accounts",
        )
    try:
        official_sources = load_official_sources(official_news_sources_file())
    except ValueError as error:
        add("official-news-sources", "error", str(error))
    else:
        add("official-news-sources", "ok", f"{len(official_sources)} valid official sources")
    existing = _nearest_existing(state_dir())
    writable = existing.is_dir() and os.access(existing, os.W_OK)
    add(
        "state-storage",
        "ok" if writable else "error",
        "external state parent is writable" if writable else "external state parent is not writable",
    )
    add(
        "openclaw",
        "ok" if shutil.which("openclaw") else "warn",
        "OpenClaw CLI is available" if shutil.which("openclaw") else "OpenClaw CLI is not available in this environment",
    )
    add(
        "node",
        "ok" if shutil.which("node") else "warn",
        "Node.js is available" if shutil.which("node") else "Node.js is required only for native card delivery",
    )
    return {"status": "error" if any(check["status"] == "error" for check in checks) else "ok", "checks": checks}


@contextmanager
def daily_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        import fcntl

        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise RuntimeError("another daily run is active") from error
        try:
            yield
        finally:
            os.close(descriptor)
        return

    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError("another daily run is active") from error
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def prepare(date_str: str) -> tuple[int, dict[str, object]]:
    paths = artifact_paths(date_str)
    storage = Storage(paths["state"])
    try:
        with daily_lock(paths["lock"]):
            storage.initialize()
            storage.seed_subscriptions(load_channels(channels_file()))
            channels = storage.active_channels()
            official_sources = load_official_sources(official_news_sources_file())
            builders_x_accounts = load_builders_x_accounts(builders_x_accounts_file())
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=24)
            items, health = collect_sources(
                channels, official_sources, builders_x_accounts, cutoff, storage
            )
            if all(entry.status == "error" for entry in health):
                return 1, {
                    "status": "failed",
                    "stage": "collect",
                    "source_health": [entry.to_dict() for entry in health],
                }
            storage.add_new_items_to_digest(date_str, items)
            digest_items = storage.items_for_digest(date_str)
            records = []
            for item in digest_items:
                status, source_text, reason = source_text_status(item.raw_source_text)
                records.append(
                    {
                        "id": item.item_id,
                        "source_type": item.source_type,
                        "source": item.source,
                        "title": item.title,
                        "published_at": item.published_at.isoformat(),
                        "url": item.url,
                        "source_text_status": status,
                        "source_text": source_text,
                        "unavailable_reason": reason,
                        "recommendation": item.recommendation,
                        "extra": item.extra,
                    }
                )
            payload = {
                "schema_version": 1,
                "date": date_str,
                "generated_at": now.isoformat(),
                "summary_basis": "curated_source_text",
                "window": {"start": cutoff.isoformat(), "end": now.isoformat()},
                "source_health": [entry.to_dict() for entry in health],
                "items": records,
            }
            atomic_write_json(paths["source"], payload)
    except (OSError, RuntimeError, ValueError) as error:
        return 1, {"status": "failed", "stage": "prepare", "error": str(error)}

    available = sum(record["source_text_status"] == "available" for record in records)
    official_news = sum(record["source_type"] == "official_news" for record in records)
    youtube = sum(record["source_type"] == "youtube" for record in records)
    aihot = sum(record["source_type"] == "aihot" for record in records)
    builders_x = sum(record["source_type"] == "builders_x" for record in records)
    result_status = "prepared_with_warnings" if any(entry.status != "ok" for entry in health) else "prepared"
    return 0, {
        "status": result_status,
        "date": date_str,
        "total": len(records),
        "official_news": official_news,
        "youtube": youtube,
        "aihot": aihot,
        "builders_x": builders_x,
        "available": available,
        "unavailable": len(records) - available,
        "source_file": str(paths["source"]),
        "digest_file": str(paths["digest"]),
        "source_health": [entry.to_dict() for entry in health],
    }


def render_cards(date_str: str) -> tuple[int, dict[str, object]]:
    paths = artifact_paths(date_str)
    if not paths["source"].is_file():
        return 1, {"status": "failed", "error": "dated source file not found"}
    if not paths["digest"].is_file():
        return 1, {"status": "failed", "error": "frozen digest file not found"}
    try:
        source_payload = json.loads(paths["source"].read_text(encoding="utf-8"))
        markdown = paths["digest"].read_text(encoding="utf-8")
        items = validate_frozen_digest(source_payload, markdown)
        cards = build_cards(date_str, items)
        atomic_write_json(paths["cards"], {"date": date_str, "cards": cards})
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return 1, {"status": "failed", "error": str(error)}
    return 0, {
        "status": "valid",
        "date": date_str,
        "total": len(items),
        "highlighted": sum(item.highlight for item in items),
        "cards": len(cards),
        "card_file": str(paths["cards"]),
    }
