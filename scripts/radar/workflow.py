"""Prepare source artifacts and validate frozen digest cards."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .digest import build_cards, validate_frozen_digest
from .github_radar import load_github_radar_config
from .huggingface_radar import HUGGINGFACE_MODELS_API, load_huggingface_radar_config
from .intelligence import classify_item, verify_source_payload
from .live_health import probe_endpoints
from .newsroom import (
    build_breaking_report,
    build_feedback_profile,
    enrich_and_rank_records,
    newsroom_summary,
)
from .official_news import COLLECTION_LOOKBACK_HOURS, load_official_sources
from .security_advisories import GITHUB_ADVISORIES_API, load_security_advisory_config
from .source_material import source_text_status
from .sources import (
    AIHOT_API_URL,
    BUILDERS_X_FEED_URL,
    collect_sources,
    load_builders_x_accounts,
    load_channels,
)
from .storage import Storage, atomic_write_json, atomic_write_text
from .trends import build_trend_report

RUNTIME_ENV_KEYS = {
    "AI_NEWS_AUTO_GROUP_DELIVERY",
    "AI_NEWS_FEISHU_PERSONAL_TARGET",
    "AI_NEWS_FEISHU_GROUP_TARGET",
    "AI_NEWS_INDUSTRY_DIGEST_SOURCES_FILE",
    "AI_NEWS_GITHUB_RADAR_FILE",
    "AI_NEWS_GITHUB_TOKEN",
    "AI_NEWS_HUGGINGFACE_RADAR_FILE",
    "AI_NEWS_HUGGINGFACE_TOKEN",
    "AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO",
    "AI_NEWS_OFFICIAL_SOURCES_FILE",
    "AI_NEWS_OWNER_ID",
    "AI_NEWS_REQUIRED_OFFICIAL_SOURCES",
    "AI_NEWS_RELEASE_ANNOUNCEMENTS",
    "AI_NEWS_SECURITY_ADVISORIES_FILE",
    "OPENCLAW_FEISHU_ACCOUNT_ID",
}
PRIMARY_WINDOW_HOURS = 24


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


def release_announcements_enabled() -> bool:
    return os.environ.get("AI_NEWS_RELEASE_ANNOUNCEMENTS", "").strip().lower() in {
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


def industry_digest_sources_file() -> Path:
    configured = os.environ.get("AI_NEWS_INDUSTRY_DIGEST_SOURCES_FILE", "").strip()
    return (
        Path(configured).expanduser()
        if configured
        else skill_root() / "references" / "industry-digest-sources.json"
    )


def github_radar_file() -> Path:
    configured = os.environ.get("AI_NEWS_GITHUB_RADAR_FILE", "").strip()
    return (
        Path(configured).expanduser()
        if configured
        else skill_root() / "references" / "github-radar.json"
    )


def security_advisories_file() -> Path:
    configured = os.environ.get("AI_NEWS_SECURITY_ADVISORIES_FILE", "").strip()
    return (
        Path(configured).expanduser()
        if configured
        else skill_root() / "references" / "security-advisories.json"
    )


def huggingface_radar_file() -> Path:
    configured = os.environ.get("AI_NEWS_HUGGINGFACE_RADAR_FILE", "").strip()
    return (
        Path(configured).expanduser()
        if configured
        else skill_root() / "references" / "huggingface-radar.json"
    )


def artifact_paths(date_str: str) -> dict[str, Path]:
    root = state_dir()
    reports = root / "reports"
    return {
        "state": root,
        "source": reports / f"{date_str}_rss_sources.json",
        "digest": reports / f"{date_str}_digest.md",
        "cards": reports / f"{date_str}_cards.json",
        "breaking_json": reports / f"{date_str}_breaking.json",
        "breaking_markdown": reports / f"{date_str}_breaking.md",
        "receipt": root / "receipts" / f"{date_str}.json",
        "lock": root / "locks" / "daily.lock",
    }


def _code_version() -> str:
    marker = skill_root() / ".deployment-commit"
    try:
        value = marker.read_text(encoding="utf-8").strip().casefold()
    except OSError:
        return "development"
    return value if len(value) == 40 and all(char in "0123456789abcdef" for char in value) else "unknown"


def _nearest_existing(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def doctor(*, live: bool = False) -> dict[str, object]:
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
    try:
        industry_sources = load_official_sources(industry_digest_sources_file())
    except ValueError as error:
        add("industry-digest-sources", "error", str(error))
    else:
        feed_label = "feed" if len(industry_sources) == 1 else "feeds"
        add(
            "industry-digest-sources",
            "ok",
            f"{len(industry_sources)} valid editorial {feed_label}",
        )
    try:
        github_config = load_github_radar_config(github_radar_file())
    except ValueError as error:
        add("github-radar", "error", str(error))
    else:
        add(
            "github-radar",
            "ok",
            f"{len(github_config['topics'])} valid topics; max {github_config['max_items']} items",
        )
    try:
        security_config = load_security_advisory_config(security_advisories_file())
    except ValueError as error:
        add("security-advisories", "error", str(error))
    else:
        add(
            "security-advisories",
            "ok",
            f"{len(security_config['packages'])} allowlisted packages",
        )
    try:
        huggingface_config = load_huggingface_radar_config(huggingface_radar_file())
    except ValueError as error:
        add("huggingface-radar", "error", str(error))
    else:
        add(
            "huggingface-radar",
            "ok",
            f"{len(huggingface_config['organizations'])} allowlisted organizations",
        )
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
    result: dict[str, object] = {
        "status": "error" if any(check["status"] == "error" for check in checks) else "ok",
        "checks": checks,
    }
    if live and result["status"] != "error":
        endpoints: list[tuple[str, str]] = []
        endpoints.extend(
            (f"official:{source['name']}", str(source.get("url") or source.get("index_url")))
            for source in official_sources
        )
        endpoints.extend(
            (f"editorial:{source['name']}", str(source.get("url") or source.get("index_url")))
            for source in industry_sources
        )
        endpoints.extend(
            (
                ("platform:aihot", AIHOT_API_URL),
                ("platform:builders-x", BUILDERS_X_FEED_URL),
                ("platform:github-advisories", f"{GITHUB_ADVISORIES_API}?per_page=1"),
                ("platform:huggingface", f"{HUGGINGFACE_MODELS_API}?limit=1"),
            )
        )
        live_result = probe_endpoints(endpoints)
        result["live"] = live_result
        if live_result["status"] == "error":
            result["status"] = "error"
        elif live_result["status"] == "warn" and result["status"] == "ok":
            result["status"] = "warn"
    return result


def evaluate_source_health(health: list[object]) -> dict[str, object]:
    entries = [entry for entry in health if hasattr(entry, "source")]
    if not entries or all(getattr(entry, "status", "error") == "error" for entry in entries):
        return {"status": "failed", "reasons": ["all source families failed"]}
    official = next(
        (entry for entry in entries if getattr(entry, "source", "") == "official_news"),
        None,
    )
    reasons: list[str] = []
    ratio = 0.0
    minimum_text = os.environ.get("AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO", "0.65").strip()
    try:
        minimum = float(minimum_text)
    except ValueError as error:
        raise ValueError("AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO must be numeric") from error
    if not 0 <= minimum <= 1:
        raise ValueError("AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO must be between 0 and 1")
    if official is None:
        reasons.append("official source family is missing")
    else:
        total = int(getattr(official, "fetched", 0)) + int(getattr(official, "failed", 0))
        ratio = int(getattr(official, "fetched", 0)) / total if total else 0.0
        if ratio < minimum:
            reasons.append(
                f"official source success ratio {ratio:.1%} is below {minimum:.1%}"
            )
        required = {
            value.strip().casefold()
            for value in os.environ.get("AI_NEWS_REQUIRED_OFFICIAL_SOURCES", "").split(",")
            if value.strip()
        }
        checks = getattr(official, "checks", ())
        check_status = {
            str(getattr(check, "name", "")).casefold(): str(getattr(check, "status", ""))
            for check in checks
        }
        missing = sorted(name for name in required if check_status.get(name) != "ok")
        if missing:
            reasons.append(f"required official sources unavailable: {', '.join(missing)}")
    return {
        "status": "failed" if reasons else "passed",
        "official_success_ratio": round(ratio, 4),
        "minimum_official_success_ratio": minimum,
        "reasons": reasons,
    }


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
        try:
            age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        except OSError:
            age = 0
        stale_seconds = 6 * 60 * 60
        if age <= stale_seconds:
            raise RuntimeError("another daily run is active") from error
        try:
            path.unlink()
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as recovery_error:
            raise RuntimeError("stale daily lock could not be recovered") from recovery_error
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
            industry_sources = load_official_sources(industry_digest_sources_file())
            github_config = load_github_radar_config(github_radar_file())
            security_config = load_security_advisory_config(security_advisories_file())
            huggingface_config = load_huggingface_radar_config(huggingface_radar_file())
            builders_x_accounts = load_builders_x_accounts(builders_x_accounts_file())
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=PRIMARY_WINDOW_HOURS)
            collection_cutoff = now - timedelta(hours=COLLECTION_LOOKBACK_HOURS)
            items, health = collect_sources(
                channels,
                official_sources,
                industry_sources,
                builders_x_accounts,
                github_config,
                security_config,
                huggingface_config,
                collection_cutoff,
                storage,
            )
            quality_gate = evaluate_source_health(health)
            if quality_gate["status"] == "failed":
                storage.record_collection_run(
                    date_str,
                    "failed",
                    quality_gate,
                    [entry.to_dict() for entry in health],
                    0,
                    0,
                )
                return 1, {
                    "status": "failed",
                    "stage": "quality_gate",
                    "quality_gate": quality_gate,
                    "source_health": [entry.to_dict() for entry in health],
                }
            storage.add_new_items_to_digest(date_str, items)
            digest_items = storage.items_for_digest(date_str)
            records = []
            for item in digest_items:
                status, source_text, reason = source_text_status(item.raw_source_text)
                record = {
                        "id": item.item_id,
                        "source_type": item.source_type,
                        "source": item.source,
                        "title": item.title,
                        "published_at": item.published_at.isoformat(),
                        "recency_status": (
                            "current" if item.published_at >= cutoff else "recovered"
                        ),
                        "url": item.url,
                        "source_text_status": status,
                        "source_text": source_text,
                        "unavailable_reason": reason,
                        "recommendation": item.recommendation,
                        "extra": item.extra,
                    }
                record.update(classify_item(item))
                records.append(record)
            owner_id = os.environ.get("AI_NEWS_OWNER_ID", "").strip()
            feedback_profile = (
                build_feedback_profile(storage.feedback_items(owner_id))
                if owner_id
                else {"samples": 0}
            )
            records = enrich_and_rank_records(records, now, feedback_profile)
            for record in records:
                record["source_text_sha256"] = hashlib.sha256(
                    str(record["source_text"]).encode("utf-8")
                ).hexdigest()
                record["record_sha256"] = hashlib.sha256(
                    json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
            source_set_sha256 = hashlib.sha256(
                json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            newsroom = newsroom_summary(records)
            newsroom["personalization_samples"] = int(feedback_profile.get("samples", 0))
            newsroom_sha256 = hashlib.sha256(
                json.dumps(newsroom, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            payload = {
                "schema_version": 2,
                "date": date_str,
                "generated_at": now.isoformat(),
                "summary_basis": "curated_source_text",
                "window": {"start": cutoff.isoformat(), "end": now.isoformat()},
                "collection_window": {
                    "start": collection_cutoff.isoformat(),
                    "end": now.isoformat(),
                },
                "source_health": [entry.to_dict() for entry in health],
                "quality_gate": quality_gate,
                "newsroom": newsroom,
                "provenance": {
                    "source_set_sha256": source_set_sha256,
                    "newsroom_sha256": newsroom_sha256,
                    "code_version": _code_version(),
                },
                "items": records,
            }
            atomic_write_json(paths["source"], payload)
            available_count = sum(
                record["source_text_status"] == "available" for record in records
            )
            storage.record_collection_run(
                date_str,
                "prepared_with_warnings"
                if any(entry.status != "ok" for entry in health)
                else "prepared",
                quality_gate,
                [entry.to_dict() for entry in health],
                len(records),
                available_count,
            )
    except (OSError, RuntimeError, ValueError) as error:
        return 1, {"status": "failed", "stage": "prepare", "error": str(error)}

    available = sum(record["source_text_status"] == "available" for record in records)
    official_news = sum(record["source_type"] == "official_news" for record in records)
    youtube = sum(record["source_type"] == "youtube" for record in records)
    aihot = sum(record["source_type"] == "aihot" for record in records)
    github_trending = sum(
        record["source_type"] == "github_trending" for record in records
    )
    security_advisory = sum(
        record["source_type"] == "security_advisory" for record in records
    )
    model_hub = sum(record["source_type"] == "model_hub" for record in records)
    industry_digest = sum(
        record["source_type"] == "industry_digest" for record in records
    )
    builders_x = sum(record["source_type"] == "builders_x" for record in records)
    result_status = "prepared_with_warnings" if any(entry.status != "ok" for entry in health) else "prepared"
    return 0, {
        "status": result_status,
        "date": date_str,
        "total": len(records),
        "official_news": official_news,
        "youtube": youtube,
        "aihot": aihot,
        "github_trending": github_trending,
        "security_advisory": security_advisory,
        "model_hub": model_hub,
        "industry_digest": industry_digest,
        "builders_x": builders_x,
        "available": available,
        "unavailable": len(records) - available,
        "source_file": str(paths["source"]),
        "digest_file": str(paths["digest"]),
        "source_health": [entry.to_dict() for entry in health],
        "quality_gate": quality_gate,
        "newsroom": newsroom,
    }


def render_cards(date_str: str) -> tuple[int, dict[str, object]]:
    paths = artifact_paths(date_str)
    if not paths["source"].is_file():
        return 1, {"status": "failed", "error": "dated source file not found"}
    if not paths["digest"].is_file():
        return 1, {"status": "failed", "error": "frozen digest file not found"}
    try:
        source_payload = json.loads(paths["source"].read_text(encoding="utf-8"))
        verify_source_payload(source_payload)
        markdown = paths["digest"].read_text(encoding="utf-8")
        items = validate_frozen_digest(source_payload, markdown)
        cards = build_cards(date_str, items)
        digest_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        cards_sha256 = hashlib.sha256(
            json.dumps(cards, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        provenance = source_payload.get("provenance", {})
        source_set_sha256 = (
            str(provenance.get("source_set_sha256", ""))
            if isinstance(provenance, dict)
            else ""
        ) or hashlib.sha256(
            json.dumps(source_payload.get("items", []), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        newsroom_sha256 = (
            str(provenance.get("newsroom_sha256", ""))
            if isinstance(provenance, dict)
            else ""
        )
        atomic_write_json(
            paths["cards"],
            {
                "schema_version": 2,
                "date": date_str,
                "source_set_sha256": source_set_sha256,
                "newsroom_sha256": newsroom_sha256,
                "digest_sha256": digest_sha256,
                "cards_sha256": cards_sha256,
                "cards": cards,
            },
        )
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


def render_trend_report(date_str: str, days: int = 7) -> tuple[int, dict[str, object]]:
    storage = Storage(state_dir())
    try:
        storage.initialize()
        report, markdown = build_trend_report(storage, date_str, days)
        reports = state_dir() / "reports"
        json_path = reports / f"{date_str}_trend_{days}d.json"
        markdown_path = reports / f"{date_str}_trend_{days}d.md"
        atomic_write_json(json_path, report)
        atomic_write_text(markdown_path, markdown)
    except (OSError, ValueError) as error:
        return 1, {"status": "failed", "error": str(error)}
    return 0, {
        "status": "generated",
        "date": date_str,
        "days": days,
        "total": report["total"],
        "json_file": str(json_path),
        "markdown_file": str(markdown_path),
    }


def render_breaking_report(
    date_str: str, limit: int = 10, minimum_score: float = 74
) -> tuple[int, dict[str, object]]:
    paths = artifact_paths(date_str)
    if not paths["source"].is_file():
        return 1, {"status": "failed", "error": "dated source file not found"}
    try:
        payload = json.loads(paths["source"].read_text(encoding="utf-8"))
        verify_source_payload(payload)
        report, markdown = build_breaking_report(
            payload, limit=limit, minimum_score=minimum_score
        )
        atomic_write_json(paths["breaking_json"], report)
        atomic_write_text(paths["breaking_markdown"], markdown)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return 1, {"status": "failed", "error": str(error)}
    return 0, {
        "status": "generated",
        "date": date_str,
        "total": report["total"],
        "minimum_score": minimum_score,
        "json_file": str(paths["breaking_json"]),
        "markdown_file": str(paths["breaking_markdown"]),
    }
