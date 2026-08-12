# Daily schedule

The deployed OpenClaw cron runs at `08:30 Asia/Shanghai` in an isolated agent session. The model is
needed only for evidence-bounded Chinese summaries and highlight selection.

## Run contract

1. Run `doctor`; stop when any check is `error`.
2. Run `prepare DATE`; stop on `failed` and report sanitized source health.
3. Read only the dated source JSON. Do not browse or use prior knowledge.
4. Write every record to frozen Markdown in `rank_position` order. Keep unavailable records exact
   and choose highlights without a fixed maximum or source quota. Give every source section with at
   least one available current record at least one highlight; zero is allowed only when that section
   has no available current record. Start from `recommended_highlight`, keep one primary leader per event,
   and apply the company relevance and marginal-value diversity rules in `editorial-policy.md`.
   Do not select weak evidence merely to fill a section. Corroborating records normally remain
   folded unless they add distinct decision-relevant information. Label `recovered` records as
   catch-up coverage and do not present them as current-window updates.
5. Run `card DATE` and correct validation failures.
6. Run `scheduled-group DATE`. This validates the frozen cards and sends them only to the externally
   configured group when automatic group delivery is explicitly enabled.
7. Stop. Never call `preview`, `approve`, or `release-announcement` from the cron run.

Never call `youtube-transcript` from this schedule. On-demand Supadata access is reserved for explicit
authenticated group messages and is not a fallback source for the daily digest.

Success requires structured `sent` or a matching `skipped` receipt. Report total, official news,
security advisories, model Hub, YouTube, AIHOT, GitHub radar, industry digest, Builders X, available,
unavailable, highlights, publication-gate result, source failures, card count, and group-delivery
status. Never expose credentials or identifiers.

Run `doctor --live`, `breaking-report DATE`, and `trend-report DATE --days 7` in separate
intelligence or maintenance sessions. Do not add
their latency or generated artifacts to the isolated daily delivery transaction.

## Isolated-run prompt

```text
Use $ai-news-skills. Run the complete source-only daily workflow for today's Asia/Shanghai date.
Stop on doctor errors. Summarize only available source_text, keep unavailable records exact,
choose company-relevant highlights independently within every populated source section using
marginal-value diversity without a fixed maximum, require at least one highlight in every section
that has an available current record, preserve rank_position order, use recommended
primary event leaders as a strong starting point without letting one source suppress all other
sections, and never promote weak evidence merely for balance,
label recovered overlap records as catch-up coverage rather than current-window news,
validate the native cards, and call scheduled-group for the configured group. Treat domestic and
international official releases, stable GitHub releases, and API changelogs as
attributed vendor claims, not independent verification. Treat industry digests as attributed
editorial synthesis. Treat GitHub radar descriptions as repository-owner claims and total Stars as
a popularity count, not a quality or security review. For GitHub radar summaries, directly explain
what the project is, what problem it addresses, and its primary use, and include its current total
Star count; omit daily growth, Forks, repository dates, language, license, and topics. Never fetch
transcripts, use S3, create a
preview draft, request approval, or send a release announcement. Treat only structured sent or a
matching skipped receipt as successful group delivery.
```

When `doctor` reports a configured platform publisher, call `platform-publish` after card
validation and before `scheduled-group`. Platform failure must be reported but must not suppress the
group digest. The command publishes only the reader-facing fields in `platform-contract.md`.
