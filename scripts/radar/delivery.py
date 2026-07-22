"""Private owner delivery with bounded retries and hashed receipts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .storage import atomic_write_json

SEND_TIMEOUT_SECONDS = 60


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_receipt(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _retryable(error: str) -> bool:
    normalized = error.lower()
    return any(
        marker in normalized
        for marker in (
            "timeout",
            "timed out",
            "connection",
            "econn",
            "network",
            "temporarily",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "gateway",
        )
    )


def _parse_bridge_payload(stdout: str) -> dict[str, object] | None:
    candidates = [stdout.strip()] if stdout.strip() else []
    if "\n" in stdout:
        candidates.extend(line.strip() for line in stdout.splitlines() if line.strip())
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _send_one(bridge: Path, target: str, card: dict) -> tuple[bool, str]:
    node = shutil.which("node")
    if not node:
        return False, "Node.js is not available"
    arguments = [
        node,
        str(bridge),
        "--target",
        target,
        "--card-json",
        json.dumps(card, ensure_ascii=False, separators=(",", ":")),
    ]
    account = os.environ.get("OPENCLAW_FEISHU_ACCOUNT_ID", "").strip()
    if account:
        arguments.extend(("--account", account))
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SEND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "native card send failed").strip()
    payload = _parse_bridge_payload(result.stdout)
    if payload is None:
        return False, "native card bridge returned invalid JSON"
    return (True, "") if payload.get("status") == "sent" else (False, "native card send was not acknowledged")


def send_cards(
    cards: list[dict],
    target: str,
    receipt_path: Path,
    bridge: Path,
    *,
    target_type: str,
    dry_run: bool = False,
) -> tuple[int, dict[str, object]]:
    if target_type not in {"personal", "group"}:
        return 1, {"status": "failed", "error": "unsupported target type"}
    normalized_target = target.strip()
    if not normalized_target and not dry_run:
        return 1, {"status": "failed", "error": f"{target_type} target is not configured"}
    if not cards:
        return 1, {"status": "failed", "error": "no rendered cards available"}
    cards_bytes = json.dumps(cards, ensure_ascii=False, sort_keys=True).encode("utf-8")
    cards_hash = _sha256(cards_bytes)
    card_hashes = [
        _sha256(json.dumps(card, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        for card in cards
    ]
    target_hash = _sha256(normalized_target.encode("utf-8")) if normalized_target else "dry-run"
    if dry_run:
        return 0, {"status": "dry_run", "target_type": target_type, "cards": len(cards)}
    receipt = _read_receipt(receipt_path) if receipt_path.is_file() else None
    if receipt:
        matches = (
            receipt.get("target_hash") == target_hash
            and receipt.get("cards_sha256") == cards_hash
        )
        if matches and receipt.get("status") == "sent":
            return 0, {
                "status": "skipped",
                "reason": "matching success receipt already exists",
                "target_type": target_type,
                "cards": len(cards),
            }
        if not matches:
            return 1, {"status": "failed", "error": "receipt does not match current cards or target"}
    sent_card_hashes = set(receipt.get("sent_card_hashes", [])) if receipt else set()
    try:
        retries = max(1, min(int(os.environ.get("AI_NEWS_CARD_RETRIES", "3")), 5))
    except ValueError:
        retries = 3
    for card_index, card in enumerate(cards, start=1):
        card_hash = card_hashes[card_index - 1]
        if card_hash in sent_card_hashes:
            continue
        last_error = "native card send failed"
        for attempt in range(retries):
            success, error = _send_one(bridge, normalized_target, card)
            if success:
                break
            last_error = error
            if attempt + 1 >= retries or not _retryable(error):
                return 1, {
                    "status": "failed",
                    "target_type": target_type,
                    "card": card_index,
                    "error": last_error,
                }
            time.sleep(2**attempt)
        sent_card_hashes.add(card_hash)
        atomic_write_json(
            receipt_path,
            {
                "status": "partial",
                "target_type": target_type,
                "target_hash": target_hash,
                "cards_sha256": cards_hash,
                "sent_card_hashes": sorted(sent_card_hashes),
                "cards": len(cards),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    atomic_write_json(
        receipt_path,
        {
            "status": "sent",
            "target_type": target_type,
            "target_hash": target_hash,
            "cards_sha256": cards_hash,
            "sent_card_hashes": card_hashes,
            "cards": len(cards),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return 0, {"status": "sent", "target_type": target_type, "cards": len(cards)}


def send_personal_cards(
    cards: list[dict],
    target: str,
    receipt_path: Path,
    bridge: Path,
    *,
    dry_run: bool = False,
) -> tuple[int, dict[str, object]]:
    return send_cards(
        cards,
        target,
        receipt_path,
        bridge,
        target_type="personal",
        dry_run=dry_run,
    )


def send_group_cards(
    cards: list[dict],
    target: str,
    receipt_path: Path,
    bridge: Path,
    *,
    dry_run: bool = False,
) -> tuple[int, dict[str, object]]:
    return send_cards(
        cards,
        receipt_path=receipt_path,
        target=target,
        bridge=bridge,
        target_type="group",
        dry_run=dry_run,
    )
