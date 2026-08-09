"""Discover fast-rising AI repositories through GitHub's official REST API."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypedDict

from .models import ContentItem, SourceCheck, SourceHealth
from .storage import Storage

GITHUB_API = "https://api.github.com/search/repositories"
GITHUB_API_VERSION = "2022-11-28"
FETCH_TIMEOUT_SECONDS = 20
CACHE_FALLBACK_HOURS = 36
LOCAL_CACHE_REUSE_MINUTES = 30


class GitHubRateLimitError(RuntimeError):
    """Stop additional search requests after GitHub exhausts the search bucket."""


class GitHubRadarConfig(TypedDict):
    discovery_days: int
    bootstrap_days: int
    min_initial_stars: int
    min_initial_stars_per_day: int
    min_star_gain: int
    max_candidates_per_query: int
    max_items: int
    topics: list[str]


class GitHubFetchResult(TypedDict):
    payload: dict[str, object]
    cache_mode: str


GitHubFetcher = Callable[[str, Storage], GitHubFetchResult]


def load_github_radar_config(path: Path) -> GitHubRadarConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid GitHub radar configuration: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("GitHub radar configuration requires schema_version 1")

    integer_fields = {
        "discovery_days": (7, 180),
        "bootstrap_days": (1, 90),
        "min_initial_stars": (1, 1_000_000),
        "min_initial_stars_per_day": (1, 100_000),
        "min_star_gain": (1, 100_000),
        "max_candidates_per_query": (1, 100),
        "max_items": (1, 50),
    }
    validated: dict[str, object] = {}
    for field, (minimum, maximum) in integer_fields.items():
        value = payload.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValueError(f"GitHub radar {field} must be between {minimum} and {maximum}")
        validated[field] = value
    if int(validated["bootstrap_days"]) > int(validated["discovery_days"]):
        raise ValueError("GitHub radar bootstrap_days must not exceed discovery_days")

    topics = payload.get("topics")
    if not isinstance(topics, list) or not 1 <= len(topics) <= 10:
        raise ValueError("GitHub radar topics must contain one through ten entries")
    normalized_topics: list[str] = []
    for topic in topics:
        if not isinstance(topic, str):
            raise ValueError("GitHub radar topics must be strings")
        normalized = topic.strip().casefold()
        if not normalized or len(normalized) > 50 or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in normalized
        ):
            raise ValueError(f"invalid GitHub topic: {topic}")
        if normalized not in normalized_topics:
            normalized_topics.append(normalized)
    validated["topics"] = normalized_topics
    return GitHubRadarConfig(**validated)  # type: ignore[arg-type]


def _cache_is_recent(cache: dict[str, object]) -> bool:
    try:
        fetched_at = datetime.fromisoformat(str(cache["fetched_at"]))
    except (KeyError, ValueError):
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at <= timedelta(hours=CACHE_FALLBACK_HOURS)


def _cache_age(cache: dict[str, object]) -> timedelta | None:
    try:
        fetched_at = datetime.fromisoformat(str(cache["fetched_at"]))
    except (KeyError, ValueError):
        return None
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at


def _cached_payload(cache: dict[str, object]) -> dict[str, object]:
    body = cache.get("body", b"")
    if not isinstance(body, (bytes, bytearray, memoryview)):
        raise ValueError("GitHub API cache body is invalid")
    payload = json.loads(bytes(body).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub API response must be an object")
    return payload


def _fetch_github_json(url: str, storage: Storage) -> GitHubFetchResult:
    cache = storage.get_http_cache(url)
    cache_age = _cache_age(cache) if cache else None
    if cache and cache_age is not None and cache_age <= timedelta(minutes=LOCAL_CACHE_REUSE_MINUTES):
        return {"payload": _cached_payload(cache), "cache_mode": "local_reuse"}
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-news-skills/1.0",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    token = os.environ.get("AI_NEWS_GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cache:
        if cache.get("etag"):
            headers["If-None-Match"] = str(cache["etag"])
        if cache.get("last_modified"):
            headers["If-Modified-Since"] = str(cache["last_modified"])

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            body = response.read()
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("GitHub API response must be an object")
            storage.put_http_cache(
                url,
                body,
                response.headers.get("ETag", ""),
                response.headers.get("Last-Modified", ""),
            )
            return {"payload": payload, "cache_mode": "fresh"}
    except urllib.error.HTTPError as error:
        if error.code == 304 and cache:
            return {"payload": _cached_payload(cache), "cache_mode": "revalidated"}
        if cache and _cache_is_recent(cache) and error.code in {
            403,
            408,
            429,
            500,
            502,
            503,
            504,
        }:
            return {"payload": _cached_payload(cache), "cache_mode": "stale_fallback"}
        if error.code in {403, 429}:
            raise GitHubRateLimitError("GitHub search rate limit exhausted") from error
        raise
    except (OSError, TimeoutError, urllib.error.URLError):
        if cache and _cache_is_recent(cache):
            return {"payload": _cached_payload(cache), "cache_mode": "stale_fallback"}
        raise


def _search_url(topic: str, created_after: str, per_page: int) -> str:
    query = (
        f"topic:{topic} created:>={created_after} archived:false fork:false"
    )
    return f"{GITHUB_API}?{urllib.parse.urlencode({'q': query, 'sort': 'stars', 'order': 'desc', 'per_page': per_page})}"


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _candidate(record: object) -> dict[str, object] | None:
    if not isinstance(record, dict) or record.get("archived") or record.get("fork"):
        return None
    full_name = str(record.get("full_name", "")).strip()
    html_url = str(record.get("html_url", "")).strip()
    description = str(record.get("description") or "").strip()
    if (
        "/" not in full_name
        or not html_url.startswith("https://github.com/")
        or len(description) < 24
    ):
        return None
    try:
        created_at = _parse_datetime(record.get("created_at"))
        pushed_at = _parse_datetime(record.get("pushed_at"))
        stars = max(0, int(record.get("stargazers_count", 0)))
        forks = max(0, int(record.get("forks_count", 0)))
    except (TypeError, ValueError):
        return None
    topics = [
        str(topic).strip()
        for topic in record.get("topics", [])
        if isinstance(topic, str) and topic.strip()
    ] if isinstance(record.get("topics", []), list) else []
    return {
        "full_name": full_name,
        "url": html_url,
        "description": description,
        "created_at": created_at,
        "pushed_at": pushed_at,
        "stars": stars,
        "forks": forks,
        "language": str(record.get("language") or "未标注"),
        "license": str((record.get("license") or {}).get("spdx_id") or "未标注")
        if isinstance(record.get("license"), dict)
        else "未标注",
        "topics": topics[:8],
    }


def _source_text(
    candidate: dict[str, object],
    previous: dict[str, object] | None,
    observed_at: datetime,
) -> tuple[str, int, float]:
    created_at = candidate["created_at"]
    assert isinstance(created_at, datetime)
    stars = int(candidate["stars"])
    age_days = max((observed_at - created_at).total_seconds() / 86_400, 0.25)
    gain = 0
    if previous:
        gain = max(0, stars - int(previous["stars"]))
    description = " ".join(str(candidate["description"]).split())
    text = f"The repository owner describes {candidate['full_name']} as follows: {description}"
    return text, gain, stars / age_days


def fetch_github_trending(
    config: GitHubRadarConfig,
    storage: Storage,
    observed_at: datetime | None = None,
    fetcher: GitHubFetcher = _fetch_github_json,
) -> tuple[list[ContentItem], SourceHealth]:
    observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    observed_date = observed_at.date().isoformat()
    created_after = (observed_at - timedelta(days=config["discovery_days"])).date().isoformat()
    candidates: dict[str, dict[str, object]] = {}
    checks: list[SourceCheck] = []
    failed = 0
    cached = 0
    stale_fallbacks = 0
    rate_limited = False

    for topic in config["topics"]:
        if rate_limited:
            failed += 1
            checks.append(SourceCheck(topic, "error", 0, 0, "skipped after rate limit"))
            continue
        try:
            result = fetcher(
                _search_url(topic, created_after, config["max_candidates_per_query"]),
                storage,
            )
            cache_mode = result["cache_mode"]
            cached += int(cache_mode != "fresh")
            stale_fallbacks += int(cache_mode == "stale_fallback")
            records = result["payload"].get("items")
            if not isinstance(records, list):
                raise ValueError("GitHub search response contains no item array")
            accepted = 0
            for record in records:
                parsed = _candidate(record)
                if parsed is None:
                    continue
                candidates[str(parsed["full_name"]).casefold()] = parsed
                accepted += 1
            checks.append(
                SourceCheck(
                    name=topic,
                    status="warn" if cache_mode == "stale_fallback" else "ok",
                    items=accepted,
                    cached=int(cache_mode != "fresh"),
                    detail="stale cache fallback" if cache_mode == "stale_fallback" else cache_mode,
                )
            )
        except Exception as error:
            failed += 1
            if isinstance(error, GitHubRateLimitError):
                rate_limited = True
                detail = str(error)
            elif isinstance(error, urllib.error.HTTPError):
                detail = f"HTTP {error.code}"
            elif isinstance(error, ValueError):
                detail = str(error)[:160]
            else:
                detail = type(error).__name__
            checks.append(SourceCheck(topic, "error", 0, 0, detail))

    ranked: list[tuple[float, ContentItem]] = []
    for candidate in candidates.values():
        full_name = str(candidate["full_name"])
        previous = storage.previous_github_snapshot(full_name, observed_date)
        source_text, gain, initial_velocity = _source_text(candidate, previous, observed_at)
        storage.put_github_snapshot(
            full_name,
            observed_date,
            int(candidate["stars"]),
            int(candidate["forks"]),
            candidate["pushed_at"],
            observed_at,
        )
        created_at = candidate["created_at"]
        assert isinstance(created_at, datetime)
        age_days = (observed_at - created_at).total_seconds() / 86_400
        if previous:
            qualifies = gain >= config["min_star_gain"]
            score = float(gain)
        else:
            qualifies = (
                age_days <= config["bootstrap_days"]
                and int(candidate["stars"]) >= config["min_initial_stars"]
                and initial_velocity >= config["min_initial_stars_per_day"]
            )
            score = initial_velocity
        if not qualifies:
            continue
        item_id = hashlib.sha256(
            f"{full_name.casefold()}:{observed_date}".encode("utf-8")
        ).hexdigest()[:24]
        ranked.append(
            (
                score,
                ContentItem(
                    item_id=item_id,
                    source_type="github_trending",
                    source="GitHub 开源雷达 · Repository Search API",
                    title=full_name,
                    published_at=observed_at,
                    url=str(candidate["url"]),
                    raw_source_text=source_text,
                    extra="GitHub Repository Trend",
                ),
            )
        )

    ranked.sort(key=lambda pair: (pair[0], pair[1].title.casefold()), reverse=True)
    items = [item for _, item in ranked[: config["max_items"]]]
    if failed == len(config["topics"]):
        status = "error"
    elif failed or stale_fallbacks:
        status = "warn"
    else:
        status = "ok"
    detail = (
        f"{len(config['topics'])} topic queries; {len(candidates)} unique candidates; "
        f"{len(items)} trending items; {failed} failures; {stale_fallbacks} stale fallbacks"
    )
    return items, SourceHealth(
        "github_trending",
        status,
        len(config["topics"]) - failed,
        failed,
        cached,
        detail,
        tuple(checks),
    )
