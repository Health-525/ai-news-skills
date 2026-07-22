"""Requester-bound digest approval cards."""

from __future__ import annotations


def build_approval_card(draft_id: str) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "日报审核"},
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "上方日报已冻结。通过后只会把同一份冻结卡片发送到预配置群聊；"
                        "未通过时不会产生任何群消息。\n\n"
                        f"通过：`通过日报 {draft_id}`\n"
                        f"退回：`退回日报 {draft_id}`"
                    ),
                },
            ]
        },
    }
