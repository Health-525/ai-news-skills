# Newsroom intelligence contract

Use this contract when reading a prepared source payload, choosing highlights, generating a
high-priority brief, or explaining why one signal outranks another.

## Product principles

The implementation translates established newsroom and recommendation patterns into bounded,
deterministic metadata:

- Google News documents relevance, prominence, authoritativeness, freshness, location, and
  language as ranking factors. This Skill implements the applicable source-only subset:
  authority, freshness, impact, and audience relevance.
- IPTC NewsML-G2 models updates, corrections, relationships, and version history. This Skill keeps
  an event ID, chronological version, role, and `supersedes` link without claiming standards
  conformance.
- Event-centric news research groups temporally related reports before summarization. This Skill
  uses a bounded 72-hour online cluster based on entities, signal type, product/version tokens,
  topics, and title similarity.
- Microsoft Research's MIND work emphasizes content understanding and user-interest modeling.
  This Skill applies only authenticated `useful` / `not_useful` feedback after three samples and
  caps its effect at +/-10 points.
- C2PA separates provenance from a truth verdict. This Skill similarly treats hashes and source
  diversity as traceability evidence, never proof that a claim is true.

Primary references:

- https://support.google.com/news/publisher-center/answer/9606702
- https://iptc.org/std/NewsML-G2/guidelines/
- https://aclanthology.org/2023.findings-emnlp.274/
- https://aclanthology.org/D18-1483/
- https://www.microsoft.com/en-us/research/publication/mind-a-large-scale-dataset-for-news-recommendation/
- https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html

## Event graph

Each record contains:

- `event_id`: stable identifier for the clustered story in the prepared artifact.
- `story_role`: `primary`, `corroborating`, or `update`.
- `story_version`: chronological position inside the event.
- `story_items`: number of collected signals in the event.
- `source_diversity`: independent publisher identities, not URL count.
- `change_type`: `correction`, `deprecation`, `advisory`, `policy`, `release`, `update`, or
  `report`, derived only from the bounded record.
- `event_first_seen` / `event_last_updated`: observed event bounds.
- `supersedes`: prior record ID from the same publisher when the record is an update.
- `language`: deterministic original-title language (`zh`, `en`, `ja`, `ko`, or `ru`) used for
  coverage reporting; it is not machine translation or a locale inference about the publisher.

Do not merge records manually. Do not describe two channels owned by the same publisher as
independent corroboration.

## Verification labels

- `cross_verified`: at least two publisher identities and at least one first-party/reviewed source.
- `multi_source`: at least two publisher identities without a high-authority source.
- `first_party`: one first-party or reviewed-advisory publisher.
- `single_source`: one medium-evidence publisher.
- `low_evidence`: only aggregation or social-level evidence.

These labels describe evidence topology. They are not truth, safety, quality, or endorsement
verdicts. Preserve this distinction in every card and report.

## Editorial score

`rank_score` is a bounded 0-100 sum with an inspectable `rank_components` object:

- authority: 0-30;
- freshness with a 72-hour half-life: 0-25;
- impact by signal type and affected audiences: 0-20;
- verification/source diversity: 0-15;
- novelty by event role: 0-10;
- authenticated owner feedback: -10 to +10;
- specificity adjustment: downranks opaque version-only titles, generic dated updates, and
  implementation case-study headlines relative to concrete releases.

Use `rank_reason` to explain ordering. Do not invent a reason or replace the stored score with an
unsupported subjective estimate.

## Alert levels

- `critical`: a security record whose bounded source text explicitly marks critical/high-risk
  severity.
- `breaking`: score >= 88, observed within 24 hours, not single/low-evidence, and a `release`,
  `advisory`, `policy`, `correction`, or `deprecation` rather than a general report/case study.
- `high`: score >= 74.
- `watch`: score >= 60.
- `normal`: remaining signals or opaque version-only notices.

Generate `breaking-report` after `prepare`. The report selects one leader per event and never sends
messages on its own. Its deterministic marginal-diversity pass normally caps a coverage bucket at
two items, a signal type at four, and a publisher at three, while allowing critical alerts to bypass caps. If
too few candidates remain, deferred items fill the requested limit. Scores are preserved; only Top
N occupancy changes. Publishing still follows the scheduled-delivery or owner-approval boundary.

## Highlight policy

The deterministic `recommended_highlight` quality gate requires current, available, primary-event
evidence. It admits critical/breaking records, corrections or deprecations above their threshold,
and company-relevant signal types scoring at least 80. Low-evidence, opaque, generic, recovered, and
corroborating records do not pass.

Choose zero through six highlights globally from eligible records. Use `rank_position` for relevance
and marginal-value diversity for event, publisher, entity, and topic overlap. Keep at most one leader
per event, do not guarantee representation for any source section, and do not pad a quiet day.

Prioritize `correction` and `deprecation` records because they can invalidate earlier operational
assumptions. Never call a later record a correction unless its own bounded text uses correction or
revision language.

When a high-ranked record has unavailable source text, preserve its unavailable marker; ranking
does not authorize inference from the headline.

## Trend momentum

`trend-report` compares its requested 2-31 day window with the immediately preceding equal-length
window. It reports current/previous counts, absolute delta, bounded growth ratio, and direction for
signal types, topics, and entities. Treat momentum as collection-observation change; source outages,
onboarding, and publisher cadence can affect it, so it is not a market-share or adoption forecast.
