# Daily schedule

The deployed OpenClaw cron runs at `08:30 Asia/Shanghai` in an isolated agent session. The model is
needed only for evidence-bounded Chinese summaries and highlight selection.

## Run contract

1. Run `doctor`; stop when any check is `error`.
2. Run `prepare DATE`; stop on `failed` and report sanitized source health.
3. Read only the dated source JSON. Do not browse or use prior knowledge.
4. Write every record to frozen Markdown. Keep unavailable records exact and choose highlights
   without a fixed count or source quota.
5. Run `card DATE` and correct validation failures.
6. Run `preview DATE`. This creates a requester-bound frozen draft and sends the digest plus an
   approval control card privately to the owner.
7. Stop. Never call `approve` from the cron run.

Success requires structured `sent` or a matching `skipped` receipt. Report total, YouTube, AIHOT,
Builders X, available, unavailable, highlights, source failures, card count, and personal delivery
status. Never expose credentials or identifiers.

## Isolated-run prompt

```text
Use $ai-news-skills. Run the complete source-only daily workflow for today's Asia/Shanghai date.
Stop on doctor errors. Summarize only available source_text, keep unavailable records exact,
choose highlights without a fixed quota, validate the native cards, and call preview for the
configured private owner. Never fetch transcripts or send to a group. Group delivery may happen
only later when the authenticated owner explicitly approves the frozen draft.
```
