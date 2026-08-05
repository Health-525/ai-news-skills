"""Deterministic entry point for the source-only AI news Skill."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from radar.approval import build_approval_card
from radar.delivery import send_group_cards, send_personal_cards
from radar.release_announcement import build_release_card, load_release_manifest
from radar.storage import Storage
from radar.subscriptions import (
    build_subscription_form_card,
    build_subscription_result_card,
    validate_batch,
)
from radar.sources import load_channels
from radar.workflow import (
    artifact_paths,
    channels_file,
    doctor,
    load_runtime_env,
    prepare,
    release_announcements_enabled,
    render_cards,
    scheduled_group_delivery_enabled,
    skill_root,
    state_dir,
)


def _date(value: str | None) -> str:
    date_str = value or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from error
    return date_str


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="Run local read-only checks")

    for command, help_text in (
        ("prepare", "Collect and freeze dated RSS inputs"),
        ("card", "Validate frozen Markdown and render cards"),
        ("preview", "Send owner preview and create an approval draft"),
        ("scheduled-group", "Send validated cards to the configured group without approval"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("date", nargs="?", help="YYYY-MM-DD; defaults to Shanghai today")
        if command in {"preview", "scheduled-group"}:
            command_parser.add_argument("--dry-run", action="store_true")

    send_parser = subparsers.add_parser("send", help="Send the validated private owner card")
    send_parser.add_argument("date", nargs="?", help="YYYY-MM-DD; defaults to Shanghai today")
    send_parser.add_argument("--target-type", required=True, choices=("personal",))
    send_parser.add_argument("--dry-run", action="store_true")

    form_parser = subparsers.add_parser("subscription-form", help="Build the batch subscription form card")
    form_parser.add_argument("--send", action="store_true")

    propose_parser = subparsers.add_parser("subscription-propose", help="Validate a batch and create a proposal")
    propose_parser.add_argument("--requester-id", required=True)
    propose_parser.add_argument("--input-file", required=True, type=Path)
    propose_parser.add_argument("--send", action="store_true")

    confirm_parser = subparsers.add_parser("subscription-confirm", help="Confirm valid proposal items")
    confirm_parser.add_argument("--requester-id", required=True)
    confirm_parser.add_argument("--proposal-id")

    cancel_parser = subparsers.add_parser("subscription-cancel", help="Cancel a pending proposal")
    cancel_parser.add_argument("--requester-id", required=True)
    cancel_parser.add_argument("--proposal-id")

    subparsers.add_parser("subscriptions", help="List active subscriptions")

    approve_parser = subparsers.add_parser("approve", help="Approve and send the exact frozen draft to the group")
    approve_parser.add_argument("--requester-id", required=True)
    approve_parser.add_argument("--draft-id")
    approve_parser.add_argument("--dry-run", action="store_true")

    reject_parser = subparsers.add_parser("reject", help="Reject a pending digest draft")
    reject_parser.add_argument("--requester-id", required=True)
    reject_parser.add_argument("--draft-id")

    release_parser = subparsers.add_parser(
        "release-announcement",
        help="Send an idempotent production release announcement to the configured group",
    )
    release_parser.add_argument("--manifest", required=True, type=Path)
    release_parser.add_argument("--dry-run", action="store_true")
    return parser


def _storage() -> Storage:
    storage = Storage(state_dir())
    storage.initialize()
    storage.seed_subscriptions(load_channels(channels_file()))
    return storage


def _cards(date_str: str) -> tuple[int, list[dict] | dict[str, object]]:
    exit_code, validation = render_cards(date_str)
    if exit_code:
        return exit_code, validation
    try:
        payload = json.loads(artifact_paths(date_str)["cards"].read_text(encoding="utf-8"))
        cards = payload["cards"]
        if not isinstance(cards, list):
            raise ValueError("cards must be an array")
    except (OSError, KeyError, json.JSONDecodeError, ValueError) as error:
        return 1, {"status": "failed", "error": f"rendered card file is invalid: {error}"}
    return 0, cards


def _target(name: str) -> str:
    return os.environ.get(name, "").strip()


def _require_owner(requester_id: str) -> None:
    owner_id = _target("AI_NEWS_OWNER_ID")
    if not owner_id or requester_id.strip() != owner_id:
        raise ValueError("requester is not the configured owner")


def _send_personal(cards: list[dict], receipt: Path, dry_run: bool = False) -> tuple[int, dict[str, object]]:
    return send_personal_cards(
        cards,
        _target("AI_NEWS_FEISHU_PERSONAL_TARGET"),
        receipt,
        skill_root() / "scripts" / "send_feishu_card.mjs",
        dry_run=dry_run,
    )


def _handle_subscription(args: argparse.Namespace) -> int:
    storage = _storage()
    if args.command == "subscriptions":
        channels = storage.active_channels()
        _print({"status": "ok", "total": len(channels), "channels": channels})
        return 0
    if args.command == "subscription-form":
        card = build_subscription_form_card()
        if not args.send:
            _print({"status": "valid", "cards": [card]})
            return 0
        timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
        code, result = _send_personal(
            [card], state_dir() / "receipts" / "subscriptions" / f"form-{timestamp}.json"
        )
        _print(result)
        return code

    _require_owner(args.requester_id)
    try:
        if args.command == "subscription-propose":
            content = args.input_file.read_text(encoding="utf-8")
            if len(content.encode("utf-8")) > 64_000:
                raise ValueError("subscription input exceeds 64 KB")
            results = validate_batch(content, storage)
            proposal_id = storage.create_subscription_proposal(args.requester_id, results)
            card = build_subscription_result_card(proposal_id, results)
            if args.send:
                code, delivery = _send_personal(
                    [card], state_dir() / "receipts" / "subscriptions" / f"{proposal_id}.json"
                )
                if code:
                    _print(delivery)
                    return code
            _print(
                {
                    "status": "pending_confirmation",
                    "proposal_id": proposal_id,
                    "total": len(results),
                    "valid": sum(item["status"] == "valid" for item in results),
                    "duplicate": sum(item["status"] == "duplicate" for item in results),
                    "invalid": sum(item["status"] == "invalid" for item in results),
                    "unavailable": sum(item["status"] == "unavailable" for item in results),
                    "delivery": "sent" if args.send else "not_requested",
                }
            )
            return 0
        if args.command == "subscription-confirm":
            proposal_id, added = storage.confirm_subscription_proposal(
                args.requester_id, args.proposal_id
            )
            _print({"status": "confirmed", "proposal_id": proposal_id, "added": added})
            return 0
        proposal_id = storage.cancel_subscription_proposal(args.requester_id, args.proposal_id)
        _print({"status": "cancelled", "proposal_id": proposal_id})
        return 0
    except (OSError, ValueError) as error:
        _print({"status": "failed", "error": str(error)})
        return 1


def _handle_preview(date_str: str, dry_run: bool) -> int:
    code, cards_or_error = _cards(date_str)
    if code:
        _print(cards_or_error)
        return code
    cards = cards_or_error
    assert isinstance(cards, list)
    owner_id = _target("AI_NEWS_OWNER_ID")
    group_target = _target("AI_NEWS_FEISHU_GROUP_TARGET")
    if not owner_id or not group_target:
        _print({"status": "failed", "error": "approval owner or group target is not configured"})
        return 1
    storage = _storage()
    draft_id, created = storage.create_digest_draft(date_str, owner_id, group_target, cards)
    preview_cards = [*cards, build_approval_card(draft_id)]
    code, result = _send_personal(
        preview_cards,
        state_dir() / "receipts" / "previews" / f"{draft_id}.json",
        dry_run=dry_run,
    )
    result.update({"draft_id": draft_id, "draft_created": created})
    _print(result)
    return code


def _handle_scheduled_group(date_str: str, dry_run: bool) -> int:
    if not scheduled_group_delivery_enabled():
        _print(
            {
                "status": "failed",
                "error": "scheduled group delivery is not explicitly enabled",
            }
        )
        return 1
    group_target = _target("AI_NEWS_FEISHU_GROUP_TARGET")
    if not group_target:
        _print({"status": "failed", "error": "group target is not configured"})
        return 1
    code, cards_or_error = _cards(date_str)
    if code:
        _print(cards_or_error)
        return code
    assert isinstance(cards_or_error, list)
    code, result = send_group_cards(
        cards_or_error,
        group_target,
        state_dir() / "receipts" / "scheduled-groups" / f"{date_str}.json",
        skill_root() / "scripts" / "send_feishu_card.mjs",
        dry_run=dry_run,
    )
    result["delivery_mode"] = "scheduled_group"
    _print(result)
    return code


def _handle_release_announcement(args: argparse.Namespace) -> int:
    try:
        manifest = load_release_manifest(args.manifest)
        version = str(manifest["version"])
        card = build_release_card(manifest)
        if not args.dry_run:
            if not release_announcements_enabled():
                raise ValueError("release announcements are not explicitly enabled")
            deployed_marker = skill_root() / ".deployment-commit"
            deployed_version = deployed_marker.read_text(encoding="utf-8").strip().casefold()
            if deployed_version != version:
                raise ValueError("release manifest does not match the deployed commit")
            if not _target("AI_NEWS_FEISHU_GROUP_TARGET"):
                raise ValueError("group target is not configured")
        code, result = send_group_cards(
            [card],
            _target("AI_NEWS_FEISHU_GROUP_TARGET"),
            state_dir() / "receipts" / "releases" / f"{version}.json",
            skill_root() / "scripts" / "send_feishu_card.mjs",
            dry_run=args.dry_run,
        )
        result.update({"release": version[:7], "delivery_mode": "release_announcement"})
        _print(result)
        return code
    except (OSError, ValueError) as error:
        _print({"status": "failed", "error": str(error)})
        return 1


def _handle_approve(args: argparse.Namespace) -> int:
    try:
        _require_owner(args.requester_id)
        group_target = _target("AI_NEWS_FEISHU_GROUP_TARGET")
        if not group_target:
            raise ValueError("group target is not configured")
        storage = _storage()
        if args.draft_id and storage.digest_draft_status(
            args.requester_id, group_target, args.draft_id
        ) == "sent":
            _print({"status": "skipped", "reason": "draft was already sent", "draft_id": args.draft_id})
            return 0
        draft = storage.claim_digest_draft(args.requester_id, group_target, args.draft_id)
        draft_id = str(draft["draft_id"])
        code, result = send_group_cards(
            draft["cards"],
            group_target,
            state_dir() / "receipts" / "groups" / f"{draft_id}.json",
            skill_root() / "scripts" / "send_feishu_card.mjs",
            dry_run=args.dry_run,
        )
        if args.dry_run:
            storage.mark_digest_failed(draft_id, "dry-run released the claim")
        elif code:
            storage.mark_digest_failed(draft_id, str(result.get("error", "delivery failed")))
        else:
            storage.mark_digest_sent(draft_id)
        result["draft_id"] = draft_id
        _print(result)
        return code
    except ValueError as error:
        _print({"status": "failed", "error": str(error)})
        return 1


def main() -> int:
    load_runtime_env()
    args = _parser().parse_args()
    if args.command == "doctor":
        result = doctor()
        _print(result)
        return 1 if result["status"] == "error" else 0
    if args.command == "release-announcement":
        return _handle_release_announcement(args)
    if args.command in {
        "subscription-form",
        "subscription-propose",
        "subscription-confirm",
        "subscription-cancel",
        "subscriptions",
    }:
        return _handle_subscription(args)
    if args.command == "approve":
        return _handle_approve(args)
    if args.command == "reject":
        try:
            _require_owner(args.requester_id)
            draft_id = _storage().reject_digest_draft(args.requester_id, args.draft_id)
        except ValueError as error:
            _print({"status": "failed", "error": str(error)})
            return 1
        _print({"status": "rejected", "draft_id": draft_id})
        return 0

    date_str = _date(args.date)
    if args.command == "prepare":
        diagnostics = doctor()
        if diagnostics["status"] == "error":
            _print({"status": "failed", "stage": "doctor", "checks": diagnostics["checks"]})
            return 1
        exit_code, result = prepare(date_str)
        _print(result)
        return exit_code
    if args.command == "card":
        exit_code, result = render_cards(date_str)
        _print(result)
        return exit_code
    if args.command == "preview":
        return _handle_preview(date_str, args.dry_run)
    if args.command == "scheduled-group":
        return _handle_scheduled_group(date_str, args.dry_run)
    if args.command == "send":
        exit_code, cards_or_error = _cards(date_str)
        if exit_code:
            _print(cards_or_error)
            return exit_code
        assert isinstance(cards_or_error, list)
        exit_code, result = _send_personal(
            cards_or_error, artifact_paths(date_str)["receipt"], args.dry_run
        )
        _print(result)
        return exit_code
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
