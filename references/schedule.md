# Daily schedule

The deployed OpenClaw cron runs at `08:30 Asia/Shanghai` in an isolated agent session. The model is
needed only for evidence-bounded Chinese summaries and highlight selection.

## Run contract

1. Run `doctor`; stop when any check is `error`.
2. Run `prepare DATE`; stop on `failed` and report sanitized source health.
3. Read only the dated source JSON. Do not browse or use prior knowledge.
4. Write every record to frozen Markdown in `rank_position` order. Keep unavailable records exact
   and choose highlights without a fixed count or source quota. Start from
   `recommended_highlight`, keep one primary leader per event, and apply the company relevance and
   marginal-value diversity rules in `editorial-policy.md`. Corroborating records normally remain
   folded unless they add distinct decision-relevant information. Label `recovered` records as
   catch-up coverage and do not present them as current-window updates.
5. Run `card DATE` and correct validation failures.
6. Run `scheduled-group DATE`. This validates the frozen cards again and sends them directly to the
   configured group only when `AI_NEWS_AUTO_GROUP_DELIVERY` is explicitly enabled.
7. Stop. Do not call `preview` or `approve` from the cron run.

Success requires structured `sent` or a matching `skipped` receipt. Report total, official news,
security advisories, model Hub, YouTube, Bilibili, AIHOT, GitHub radar, industry digest, Builders X,
available, unavailable, highlights, publication-gate result, source failures, card count, and group
delivery status. Never expose credentials or identifiers.

Run `doctor --live`, `breaking-report DATE`, and `trend-report DATE --days 7` in separate
intelligence or maintenance sessions. Do not add
their latency or generated artifacts to the isolated daily delivery transaction.

## Isolated-run prompt

```text
Use $ai-news-skills. Run the complete source-only daily workflow for today's Asia/Shanghai date.
Stop on doctor errors. Summarize only available source_text, keep unavailable records exact,
choose company-relevant highlights using marginal-value source diversity without a fixed quota,
preserve rank_position order and normally select only the recommended primary event leader,
label recovered overlap records as catch-up coverage rather than current-window news,
validate the native cards, and call scheduled-group for the configured group. Treat domestic and
international official releases, stable GitHub releases, and API changelogs as
attributed vendor claims, not independent verification. Treat industry digests as attributed
editorial synthesis. Treat GitHub radar descriptions as repository-owner claims and Star growth as
a popularity signal, not a quality or security review. Never fetch transcripts, use S3, send a personal preview, or create an
approval draft. Treat only structured sent or a matching skipped receipt as successful delivery.
```
