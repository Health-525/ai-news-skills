# Intelligence operations

## Source health and publication gate

Run local configuration checks before every collection:

```bash
python {baseDir}/scripts/daily_pipeline.py doctor
```

Run the bounded live audit manually or on a separate low-frequency schedule:

```bash
python {baseDir}/scripts/daily_pipeline.py doctor --live
```

The live audit performs read-only endpoint probes and reports latency and partial failures. It does
not validate complete parsing and does not write HTTP cache. A success ratio below 80% is an error;
isolated endpoint failures produce a warning.

`prepare` applies a deterministic publication gate after collection. By default at least 65% of
configured official sources must fetch successfully. Override the threshold with
`AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO` in the range `0` through `1`. Use
`AI_NEWS_REQUIRED_OFFICIAL_SOURCES` for a comma-separated deployment-specific list of source names
that must be healthy. Keep strict required-source lists in private runtime configuration because
they express organization priorities.

## Provenance

Schema-version-2 source artifacts include deterministic signal labels, evidence levels, topics,
entities, a SHA-256 hash for every cleaned `source_text`, a full-record hash, a source-set hash, a
newsroom-summary hash, and the deployed code version when `.deployment-commit` is available. Card
rendering verifies these hashes before accepting frozen Markdown. The rendered-card artifact then
records source-set, newsroom, Markdown, and card hashes.

Hashes provide tamper evidence and reproducibility; they do not prove that a publisher claim is
true. Continue to apply the evidence rules in `editorial-policy.md`.

## Ranked newsroom, breaking brief, trend report, and feedback

Every successful `prepare` clusters the bounded signals into update-aware events and attaches an
explainable editorial rank. Inspect `newsroom` in the returned result for event count, compression,
verification coverage, and alert distribution. Ranking never expands the evidence boundary.

Generate a high-priority, one-leader-per-event brief from the verified source artifact:

```bash
python {baseDir}/scripts/daily_pipeline.py breaking-report YYYY-MM-DD \
  --limit 10 --minimum-score 74
```

The command writes JSON and Markdown under external `reports/`. It selects one leader per event and
applies deterministic marginal-diversity caps so one package, publisher, or signal class
does not consume the whole brief; critical alerts bypass the caps. It does not collect, invoke a
model, or send a message. Publication still requires the normal scheduled-delivery or owner-approval path.

Generate a deterministic report from already collected private state:

```bash
python {baseDir}/scripts/daily_pipeline.py trend-report YYYY-MM-DD --days 7
```

The command writes JSON and Markdown under the external `reports/` directory. It aggregates signal
types, topics, entities, source types, publishers, evidence availability, event compression,
top-ranked event leaders, alert distribution, owner feedback, and topic/entity/signal momentum
against the immediately preceding equal-length window. `new`, `rising`, `steady`, and `falling`
describe observed collection counts, not market forecasts. It does not invoke a model or send a
message.

Record feedback only from authenticated owner metadata:

```bash
python {baseDir}/scripts/daily_pipeline.py feedback \
  --requester-id AUTHENTICATED_ID --item-id ITEM_ID --value useful
```

Accepted values are `useful` and `not_useful`. Repeated feedback from the same owner and item updates
the existing value instead of creating duplicate votes.

## State maintenance

Preview eligible cleanup without deleting data:

```bash
python {baseDir}/scripts/daily_pipeline.py maintenance --retention-days 30
```

Apply cleanup only after reviewing the returned counts:

```bash
python {baseDir}/scripts/daily_pipeline.py maintenance --retention-days 30 --apply
```

Maintenance removes only expired HTTP cache, completed subscription proposals, completed digest
drafts, and old GitHub snapshots. It never removes the global item ledger, active subscriptions,
pending approvals, reports, cards, receipts, or credentials. SQLite uses an explicit schema version
and rejects a database created by a newer runtime.

On Windows, a lock file younger than six hours remains authoritative. A lock older than six hours
is treated as a crashed-run remnant and is recovered atomically.

## Runtime packaging

Build a deterministic, runtime-only ZIP from the verified Git commit:

```bash
python {baseDir}/scripts/package_skill.py --output PATH/ai-news-skills.zip
```

The archive contains `SKILL.md`, `agents/`, `references/`, runtime scripts, offline tests, and an
exact `.deployment-commit`. It excludes repository-only README, CI, editor state, caches, and test
development files. Packaging fails when any runtime file is uncommitted, preventing content from
being mislabeled with an unrelated Git commit. Validate the extracted archive with `self_test.py` and `doctor` before switching
the production directory.
