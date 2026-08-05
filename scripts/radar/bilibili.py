"""Collect public Bilibili account submission metadata without loading videos."""

from __future__ import annotations

import gzip
import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .models import ContentItem, SourceHealth
from .storage import Storage

BILIBILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
BILIBILI_SPACE_URL = "https://api.bilibili.com/x/space/wbi/arc/search"
BILIBILI_USER_ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")
FETCH_TIMEOUT_SECONDS = 20
CACHE_FALLBACK_HOURS = 72
BILIBILI_REQUEST_INTERVAL_SECONDS = 5.0
USER_AGENT = "Mozilla/5.0"
WBI_MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

FetchBilibili = Callable[[str, str, Storage], tuple[bytes, bool]]


def load_bilibili_accounts(path: Path) -> list[dict[str, str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Bilibili account file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Bilibili account file is invalid JSON: {error}") from error
    if not isinstance(value, list) or not value:
        raise ValueError("Bilibili account file must contain a non-empty array")

    accounts: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Bilibili account {index} must be an object")
        name = str(entry.get("name", "")).strip()
        user_id = str(entry.get("user_id", "")).strip()
        if not name or not BILIBILI_USER_ID_RE.fullmatch(user_id):
            raise ValueError(f"Bilibili account {index} has invalid name or user_id")
        if user_id in seen:
            raise ValueError(f"duplicate Bilibili user_id at account {index}")
        seen.add(user_id)
        accounts.append({"name": name, "user_id": user_id})
    return accounts


def _cache_is_recent(cache: dict[str, object]) -> bool:
    try:
        fetched_at = datetime.fromisoformat(str(cache["fetched_at"]))
    except (KeyError, ValueError):
        return False
    return datetime.now(timezone.utc) - fetched_at <= timedelta(
        hours=CACHE_FALLBACK_HOURS
    )


def _cached_body(cache: dict[str, object]) -> bytes:
    body = cache.get("body")
    if isinstance(body, bytes):
        return body
    if isinstance(body, (bytearray, memoryview)):
        return bytes(body)
    raise ValueError("cached Bilibili response body is not binary")


def _fetch_bytes(url: str, cache_key: str, storage: Storage) -> tuple[bytes, bool]:
    cache = storage.get_http_cache(cache_key)
    cache_query = urllib.parse.parse_qs(urllib.parse.urlparse(cache_key).query)
    user_id = str(cache_query.get("mid", [""])[0]).strip()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": (
            f"https://space.bilibili.com/{user_id}/video"
            if user_id
            else "https://www.bilibili.com/"
        ),
    }
    if cache:
        if cache.get("etag"):
            headers["If-None-Match"] = str(cache["etag"])
        if cache.get("last_modified"):
            headers["If-Modified-Since"] = str(cache["last_modified"])
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            body = response.read()
            if response.headers.get("Content-Encoding", "").casefold() == "gzip":
                body = gzip.decompress(body)
            if user_id:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    if cache and _cache_is_recent(cache):
                        return _cached_body(cache), True
                    raise ValueError("Bilibili account response is invalid JSON") from error
                if not isinstance(payload, dict) or payload.get("code") != 0:
                    if cache and _cache_is_recent(cache):
                        return _cached_body(cache), True
                    raise ValueError("Bilibili account request was rejected")
            storage.put_http_cache(
                cache_key,
                body,
                response.headers.get("ETag", ""),
                response.headers.get("Last-Modified", ""),
            )
            return body, False
    except urllib.error.HTTPError as error:
        if error.code == 304 and cache:
            return _cached_body(cache), True
        if cache and _cache_is_recent(cache) and error.code in {
            408, 412, 429, 500, 502, 503, 504
        }:
            return _cached_body(cache), True
        raise
    except (OSError, TimeoutError, urllib.error.URLError):
        if cache and _cache_is_recent(cache):
            return _cached_body(cache), True
        raise


def _image_key(value: object) -> str:
    parsed = urllib.parse.urlparse(str(value or ""))
    filename = Path(parsed.path).name
    key, _, extension = filename.partition(".")
    if not key or not extension:
        raise ValueError("Bilibili WBI image key is invalid")
    return key


def _wbi_mixin_key(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Bilibili navigation response must be an object")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Bilibili navigation response has no data")
    wbi_img = data.get("wbi_img")
    if not isinstance(wbi_img, dict):
        raise ValueError("Bilibili navigation response has no WBI keys")
    source = _image_key(wbi_img.get("img_url")) + _image_key(wbi_img.get("sub_url"))
    if len(source) < 64:
        raise ValueError("Bilibili WBI key material is incomplete")
    return "".join(source[index] for index in WBI_MIXIN_KEY_ENC_TAB)[:32]


def _encode_fingerprint(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode().rstrip("=")


def _signed_space_url(user_id: str, mixin_key: str, timestamp: int) -> str:
    dm_interaction = '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}'
    params = {
        "mid": user_id,
        "pn": "1",
        "ps": "20",
        "order": "pubdate",
        "platform": "web",
        "web_location": "1550101",
        "dm_img_list": "[]",
        "dm_img_str": _encode_fingerprint("WebGL 1"),
        "dm_cover_img_str": _encode_fingerprint(
            "ANGLE (Google, Inc. (NVIDIA))"
        ),
        "dm_img_inter": dm_interaction,
        "wts": str(timestamp),
    }
    sanitized = {
        key: "".join(character for character in value if character not in "!'()*")
        for key, value in params.items()
    }
    query = urllib.parse.urlencode(sorted(sanitized.items()))
    sanitized["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return f"{BILIBILI_SPACE_URL}?{urllib.parse.urlencode(sanitized)}"


def parse_bilibili_videos(
    payload: object,
    account: dict[str, str],
    cutoff: datetime,
) -> list[ContentItem]:
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ValueError("Bilibili account API returned a non-success status")
    data = payload.get("data")
    listing = data.get("list") if isinstance(data, dict) else None
    videos = listing.get("vlist") if isinstance(listing, dict) else None
    if not isinstance(videos, list):
        raise ValueError("Bilibili account API returned no video list")

    items: list[ContentItem] = []
    for video in videos:
        if not isinstance(video, dict):
            continue
        bvid = str(video.get("bvid", "")).strip()
        title = str(video.get("title", "")).strip()
        description = str(video.get("description", "")).strip()
        author_id = str(video.get("mid", "")).strip()
        try:
            published_at = datetime.fromtimestamp(
                int(video.get("created", 0)), tz=timezone.utc
            )
        except (OSError, OverflowError, TypeError, ValueError):
            continue
        if (
            not bvid.startswith("BV")
            or not title
            or author_id != account["user_id"]
            or published_at < cutoff
        ):
            continue
        items.append(
            ContentItem(
                item_id=bvid,
                source_type="bilibili",
                source=f"哔哩哔哩 · {account['name']}",
                title=title,
                published_at=published_at,
                url=f"https://www.bilibili.com/video/{urllib.parse.quote(bvid)}/",
                raw_source_text=description,
                extra="公开频道投稿元数据",
            )
        )
    return items


def fetch_bilibili(
    accounts: list[dict[str, str]],
    cutoff: datetime,
    storage: Storage,
    fetcher: FetchBilibili = _fetch_bytes,
) -> tuple[list[ContentItem], SourceHealth]:
    try:
        nav_body, nav_cached = fetcher(BILIBILI_NAV_URL, BILIBILI_NAV_URL, storage)
        mixin_key = _wbi_mixin_key(json.loads(nav_body.decode("utf-8")))
    except Exception:
        return [], SourceHealth(
            "bilibili", "error", 0, len(accounts), 0, "public WBI metadata unavailable"
        )

    items: list[ContentItem] = []
    failed_accounts: list[str] = []
    cached = int(nav_cached)
    accounts_with_items = 0
    def fetch_account(account: dict[str, str]) -> tuple[list[ContentItem], bool]:
        user_id = account["user_id"]
        cache_key = f"{BILIBILI_SPACE_URL}?mid={urllib.parse.quote(user_id)}"
        last_error: Exception | None = None
        for attempt in range(3):
            if attempt and fetcher is _fetch_bytes:
                time.sleep(BILIBILI_REQUEST_INTERVAL_SECONDS * attempt)
            url = _signed_space_url(user_id, mixin_key, int(time.time()))
            try:
                body, used_cache = fetcher(url, cache_key, storage)
                payload = json.loads(body.decode("utf-8"))
                return parse_bilibili_videos(payload, account, cutoff), used_cache
            except Exception as error:
                last_error = error
        assert last_error is not None
        raise last_error

    for index, account in enumerate(accounts):
        if index and fetcher is _fetch_bytes:
            time.sleep(BILIBILI_REQUEST_INTERVAL_SECONDS)
        try:
            account_items, used_cache = fetch_account(account)
        except Exception:
            failed_accounts.append(account["name"])
            continue
        items.extend(account_items)
        cached += int(used_cache)
        accounts_with_items += int(bool(account_items))

    failed = len(failed_accounts)
    fetched = len(accounts) - failed
    status = "error" if failed == len(accounts) else "partial" if failed else "ok"
    detail = (
        f"{len(accounts)} configured accounts; {fetched} fetched; "
        f"{accounts_with_items} with in-window submissions; "
        f"{fetched - accounts_with_items} without in-window submissions; "
        "full-account collection"
    )
    if failed_accounts:
        detail += f"; unavailable: {', '.join(sorted(failed_accounts))}"
    return items, SourceHealth(
        "bilibili", status, fetched, failed, cached, detail
    )
