"""Bounded live endpoint diagnostics without mutating runtime state."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def _probe(name: str, url: str, timeout: int) -> dict[str, object]:
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, application/rss+xml, application/atom+xml, text/html;q=0.9, */*;q=0.8",
            "Range": "bytes=0-8191",
            "User-Agent": "ai-news-skills/2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(8192)
            status_code = int(getattr(response, "status", 200))
            if status_code not in {200, 206} or not body.strip():
                raise ValueError("empty or unsupported response")
        status = "ok"
        detail = f"HTTP {status_code}"
    except urllib.error.HTTPError as error:
        status = "warn" if error.code in {401, 403, 429} else "error"
        detail = f"HTTP {error.code}"
    except Exception as error:
        status = "error"
        detail = type(error).__name__
    return {
        "name": name,
        "status": status,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "detail": detail,
    }


def probe_endpoints(
    endpoints: list[tuple[str, str]], *, timeout: int = 12, workers: int = 10
) -> dict[str, object]:
    unique = list(dict.fromkeys(endpoints))
    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as executor:
        futures = {
            executor.submit(_probe, name, url, timeout): (name, url)
            for name, url in unique
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda result: str(result["name"]).casefold())
    errors = sum(result["status"] == "error" for result in results)
    warnings = sum(result["status"] == "warn" for result in results)
    success_ratio = (len(results) - errors) / len(results) if results else 0.0
    return {
        "status": (
            "error"
            if success_ratio < 0.8
            else "warn"
            if errors or warnings
            else "ok"
        ),
        "total": len(results),
        "ok": len(results) - errors - warnings,
        "warnings": warnings,
        "errors": errors,
        "success_ratio": round(success_ratio, 4),
        "checks": results,
    }
