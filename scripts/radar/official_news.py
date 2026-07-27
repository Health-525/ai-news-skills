"""Collect first-party model-lab announcements from RSS or bounded news indexes."""

from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, NotRequired, TypedDict
from xml.etree import ElementTree as ET

from .models import ContentItem, SourceHealth
from .storage import Storage
from .url_utils import canonical_url, normalized_host

Fetcher = Callable[[str, Storage], tuple[bytes, bool]]
MONTH_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)
DATE_PUBLISHED_RE = re.compile(
    r"""\\?["']datePublished\\?["']\s*:\s*\\?["']([^"'\\]+)""",
    re.IGNORECASE,
)


class OfficialSource(TypedDict):
    name: str
    kind: str
    allowed_hosts: list[str]
    url: NotRequired[str]
    index_url: NotRequired[str]
    article_path_prefix: NotRequired[str]
    excluded_path_prefixes: NotRequired[list[str]]
    max_candidates: NotRequired[int]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


class _IndexParser(HTMLParser):
    def __init__(
        self,
        base_url: str,
        allowed_hosts: set[str],
        article_prefix: str,
        excluded_prefixes: tuple[str, ...],
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.allowed_hosts = allowed_hosts
        self.article_prefix = article_prefix
        self.excluded_prefixes = excluded_prefixes
        self.current: dict[str, object] | None = None
        self.records: dict[str, dict[str, str]] = {}
        self.order: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        href = attributes.get("href", "").strip()
        if not href:
            return
        url = urllib.parse.urljoin(self.base_url, href)
        parsed = urllib.parse.urlsplit(url)
        host = normalized_host(parsed.hostname or "")
        if (
            parsed.scheme != "https"
            or host not in self.allowed_hosts
            or not parsed.path.startswith(self.article_prefix)
            or parsed.path.rstrip("/") == self.article_prefix.rstrip("/")
            or any(parsed.path.startswith(prefix) for prefix in self.excluded_prefixes)
        ):
            return
        clean_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        self.current = {
            "url": clean_url,
            "label": attributes.get("label", "").strip(),
            "description": attributes.get("description", "").strip(),
            "text": [],
        }

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            parts = self.current["text"]
            assert isinstance(parts, list)
            parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self.current is None:
            return
        url = str(self.current["url"])
        text_parts = self.current["text"]
        assert isinstance(text_parts, list)
        text = _clean_text(" ".join(str(part) for part in text_parts))
        record = self.records.get(url)
        if record is None:
            record = {"url": url, "label": "", "description": "", "text": ""}
            self.records[url] = record
            self.order.append(url)
        for key in ("label", "description"):
            value = str(self.current[key]).strip()
            if value and not record[key]:
                record[key] = value
        if text and text not in record["text"]:
            record["text"] = _clean_text(f"{record['text']} {text}")
        self.current = None

    def candidates(self, limit: int) -> list[dict[str, str]]:
        return [self.records[url] for url in self.order[:limit]]


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").casefold()
            content = attributes.get("content", "").strip()
            if key and content:
                self.meta[key] = content
        elif tag.casefold() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


def _validate_https_url(value: object, label: str) -> str:
    url = str(value or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{label} must be an absolute HTTPS URL")
    return url


def load_official_sources(path: Path) -> list[OfficialSource]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"official source file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"official source file is invalid JSON: {error}") from error
    if not isinstance(payload, list) or not payload:
        raise ValueError("official source file must contain a non-empty array")

    sources: list[OfficialSource] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(payload, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"official source {index} must be an object")
        name = str(entry.get("name", "")).strip()
        kind = str(entry.get("kind", "")).strip()
        normalized_name = name.casefold()
        if not name or normalized_name in seen_names:
            raise ValueError(f"official source {index} has an invalid or duplicate name")
        if kind not in {"rss", "html_index"}:
            raise ValueError(f"official source {index} has an unsupported kind")
        allowed_hosts_value = entry.get("allowed_hosts")
        if not isinstance(allowed_hosts_value, list) or not allowed_hosts_value:
            raise ValueError(f"official source {index} must define allowed_hosts")
        allowed_hosts = sorted(
            {normalized_host(str(host)) for host in allowed_hosts_value if str(host).strip()}
        )
        if not allowed_hosts:
            raise ValueError(f"official source {index} has no valid allowed host")

        normalized: OfficialSource = {
            "name": name,
            "kind": kind,
            "allowed_hosts": allowed_hosts,
        }
        if kind == "rss":
            url = _validate_https_url(entry.get("url"), f"official source {index} url")
            if normalized_host(urllib.parse.urlsplit(url).hostname or "") not in allowed_hosts:
                raise ValueError(f"official source {index} URL host is not allowlisted")
            normalized["url"] = url
        else:
            index_url = _validate_https_url(
                entry.get("index_url"), f"official source {index} index_url"
            )
            if normalized_host(urllib.parse.urlsplit(index_url).hostname or "") not in allowed_hosts:
                raise ValueError(f"official source {index} index host is not allowlisted")
            article_prefix = str(entry.get("article_path_prefix", "")).strip()
            if not article_prefix.startswith("/"):
                raise ValueError(f"official source {index} has an invalid article path prefix")
            excluded = entry.get("excluded_path_prefixes", [])
            if not isinstance(excluded, list) or any(
                not str(prefix).startswith("/") for prefix in excluded
            ):
                raise ValueError(f"official source {index} has invalid excluded path prefixes")
            try:
                max_candidates = int(entry.get("max_candidates", 12))
            except (TypeError, ValueError) as error:
                raise ValueError(f"official source {index} has invalid max_candidates") from error
            if not 1 <= max_candidates <= 30:
                raise ValueError(f"official source {index} max_candidates must be 1 through 30")
            normalized.update(
                {
                    "index_url": index_url,
                    "article_path_prefix": article_prefix,
                    "excluded_path_prefixes": [str(prefix) for prefix in excluded],
                    "max_candidates": max_candidates,
                }
            )
        seen_names.add(normalized_name)
        sources.append(normalized)
    return sources


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value)
    except ValueError:
        return _clean_text(re.sub(r"<[^>]+>", " ", value))
    return _clean_text(" ".join(parser.parts))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _child_text(element: ET.Element, *names: str) -> str:
    accepted = {name.casefold() for name in names}
    for child in element:
        if _local_name(child.tag) in accepted and child.text:
            return child.text.strip()
    return ""


def _parse_published(value: str) -> tuple[datetime, bool]:
    normalized = value.strip()
    if not normalized:
        raise ValueError("published date is empty")
    date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized))
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(normalized)
        date_only = False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), date_only


def _within_window(published_at: datetime, date_only: bool, cutoff: datetime) -> bool:
    return published_at.date() >= cutoff.date() if date_only else published_at >= cutoff


def _month_date(value: str) -> tuple[datetime, bool] | None:
    match = MONTH_DATE_RE.search(value)
    if not match:
        return None
    for date_format in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return (
                datetime.strptime(match.group(0), date_format).replace(tzinfo=timezone.utc),
                True,
            )
        except ValueError:
            continue
    return None


def _allowed_article_url(value: str, allowed_hosts: set[str]) -> bool:
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme == "https" and normalized_host(parsed.hostname or "") in allowed_hosts


def parse_official_feed(
    body: bytes,
    source: OfficialSource,
    cutoff: datetime,
) -> list[ContentItem]:
    root = ET.fromstring(body)
    root_name = _local_name(root.tag)
    if root_name == "rss":
        entries = root.findall("./channel/item")
    elif root_name == "feed":
        entries = [child for child in root if _local_name(child.tag) == "entry"]
    else:
        raise ValueError("official feed is neither RSS nor Atom")

    allowed_hosts = {str(host) for host in source["allowed_hosts"]}
    items: list[ContentItem] = []
    for entry in entries:
        title = _child_text(entry, "title")
        published_text = _child_text(entry, "pubDate", "published", "updated", "date")
        description = _child_text(entry, "description", "summary", "content", "encoded")
        guid = _child_text(entry, "guid", "id")
        category = _child_text(entry, "category")
        link = _child_text(entry, "link")
        if not link:
            for child in entry:
                if _local_name(child.tag) == "link":
                    rel = child.attrib.get("rel", "alternate")
                    href = child.attrib.get("href", "")
                    if rel in {"", "alternate"} and href:
                        link = href.strip()
                        break
        if not title or not published_text or not link or not _allowed_article_url(
            link, allowed_hosts
        ):
            continue
        try:
            published_at, date_only = _parse_published(published_text)
        except (TypeError, ValueError):
            continue
        if not _within_window(published_at, date_only, cutoff):
            continue
        url_key = canonical_url(link)
        item_id = hashlib.sha256((guid or url_key).encode("utf-8")).hexdigest()[:24]
        items.append(
            ContentItem(
                item_id=item_id,
                source_type="official_news",
                source=f"官方发布 · {source['name']}",
                title=_clean_text(title),
                published_at=published_at,
                url=link,
                raw_source_text=_html_to_text(description),
                extra=f"官方 RSS{f' · {category}' if category else ''}",
            )
        )
    return items


def _extract_published(
    metadata: _MetadataParser,
    raw_html: str,
    index_text: str,
) -> tuple[datetime, bool] | None:
    for key in ("article:published_time", "datepublished", "date", "publish_date"):
        value = metadata.meta.get(key, "")
        if value:
            try:
                return _parse_published(value)
            except ValueError:
                pass
    normalized_html = html.unescape(raw_html).replace('\\"', '"')
    date_match = DATE_PUBLISHED_RE.search(normalized_html)
    if date_match:
        try:
            return _parse_published(date_match.group(1))
        except ValueError:
            pass
    return _month_date(index_text)


def _article_metadata(body: bytes, index_text: str) -> tuple[str, str, datetime, bool] | None:
    raw_html = body.decode("utf-8", errors="replace")
    parser = _MetadataParser()
    parser.feed(raw_html)
    published = _extract_published(parser, raw_html, index_text)
    if published is None:
        return None
    title = (
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or _clean_text(" ".join(parser.title_parts))
    )
    description = (
        parser.meta.get("og:description")
        or parser.meta.get("description")
        or parser.meta.get("twitter:description")
        or ""
    )
    return _clean_text(title), _html_to_text(description), *published


def _parse_official_index(
    index_body: bytes,
    source: OfficialSource,
    cutoff: datetime,
    storage: Storage,
    fetcher: Fetcher,
) -> tuple[list[ContentItem], int, int]:
    index_url = source.get("index_url", "")
    article_prefix = source.get("article_path_prefix", "")
    excluded_prefixes = source.get("excluded_path_prefixes", [])
    max_candidates = source.get("max_candidates", 12)
    if not index_url or not article_prefix:
        raise ValueError("HTML official source is missing its validated index configuration")
    allowed_hosts = {str(host) for host in source["allowed_hosts"]}
    parser = _IndexParser(
        index_url,
        allowed_hosts,
        article_prefix,
        tuple(excluded_prefixes),
    )
    parser.feed(index_body.decode("utf-8", errors="replace"))
    candidates = parser.candidates(max_candidates)
    items: list[ContentItem] = []
    cached_requests = 0
    failed_articles = 0
    for candidate in candidates:
        index_published = _month_date(candidate["text"])
        if index_published is not None and not _within_window(*index_published, cutoff):
            continue
        try:
            article_body, cached = fetcher(candidate["url"], storage)
            cached_requests += int(cached)
            metadata = _article_metadata(article_body, candidate["text"])
        except Exception:
            failed_articles += 1
            continue
        if metadata is None:
            continue
        title, description, published_at, date_only = metadata
        if not title:
            title = candidate["label"]
        if not description:
            description = candidate["description"]
        if not title or not _within_window(published_at, date_only, cutoff):
            continue
        url_key = canonical_url(candidate["url"])
        items.append(
            ContentItem(
                item_id=hashlib.sha256(url_key.encode("utf-8")).hexdigest()[:24],
                source_type="official_news",
                source=f"官方发布 · {source['name']}",
                title=title,
                published_at=published_at,
                url=candidate["url"],
                raw_source_text=description,
                extra="官方 Newsroom",
            )
        )
    return items, cached_requests, failed_articles


def fetch_official_news(
    sources: list[OfficialSource],
    cutoff: datetime,
    storage: Storage,
    fetcher: Fetcher,
) -> tuple[list[ContentItem], SourceHealth]:
    items: list[ContentItem] = []
    fetched_sources = 0
    failed_sources: list[str] = []
    cached_requests = 0
    failed_articles = 0
    for source in sources:
        try:
            if source["kind"] == "rss":
                feed_url = source.get("url", "")
                if not feed_url:
                    raise ValueError("RSS official source is missing its validated feed URL")
                body, cached = fetcher(feed_url, storage)
                source_items = parse_official_feed(body, source, cutoff)
                cached_requests += int(cached)
            else:
                index_url = source.get("index_url", "")
                if not index_url:
                    raise ValueError("HTML official source is missing its validated index URL")
                body, cached = fetcher(index_url, storage)
                cached_requests += int(cached)
                source_items, article_cache, article_failures = _parse_official_index(
                    body, source, cutoff, storage, fetcher
                )
                cached_requests += article_cache
                failed_articles += article_failures
            items.extend(source_items)
            fetched_sources += 1
        except Exception:
            failed_sources.append(str(source["name"]))

    if not fetched_sources:
        status = "error"
    elif failed_sources or failed_articles:
        status = "warn"
    else:
        status = "ok"
    detail = (
        f"{len(sources)} configured sources; {fetched_sources} fetched; "
        f"{len(failed_sources)} source failures; {failed_articles} article failures; "
        f"{len(items)} items"
    )
    if failed_sources:
        detail += f"; unavailable: {', '.join(failed_sources)}"
    return items, SourceHealth(
        "official_news",
        status,
        fetched_sources,
        len(failed_sources),
        cached_requests,
        detail,
    )
