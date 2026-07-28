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
6. Run `scheduled-group DATE`. This validates the frozen cards again and sends them directly to the
   configured group only when `AI_NEWS_AUTO_GROUP_DELIVERY` is explicitly enabled.
7. Stop. Do not call `preview` or `approve` from the cron run.

Success requires structured `sent` or a matching `skipped` receipt. Report total, official news,
YouTube, AIHOT, industry digest, Builders X, available, unavailable, highlights, source failures,
card count, and group delivery status. Never expose credentials or identifiers.

## Isolated-run prompt

```text
Use $ai-news-skills. Run the complete source-only daily workflow for today's Asia/Shanghai date.
Stop on doctor errors. Summarize only available source_text, keep unavailable records exact,
choose highlights without a fixed quota, validate the native cards, and call scheduled-group for
the configured group. Treat domestic and international official releases and API changelogs as
attributed vendor claims, not independent verification. Treat industry digests as attributed
editorial synthesis. Never fetch transcripts, use S3, send a personal preview, or create an
approval draft. Treat only structured sent or a matching skipped receipt as successful delivery.
```
