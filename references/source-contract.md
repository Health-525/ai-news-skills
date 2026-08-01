# Source and runtime contract

## Inputs

- Official news: the domestic and international first-party set in
  `official-news-sources.json`. Supported adapters are RSS/Atom, dated API changelogs,
  first-party JSON endpoints, embedded server-rendered indexes, and bounded same-domain newsroom
  indexes. Evidence is limited to publisher-supplied descriptions, changelog entries, abstracts,
  or public article metadata; scripts and article bodies are never executed or summarized.
  Source-specific title/category filters remove broad corporate noise. Curated GitHub release Atom
  feeds are treated as first-party project release evidence and exclude alpha, beta, release
  candidate, preview, nightly, development, and canary versions. Each configured endpoint fails
  independently, and page-structure changes surface in `source_health`. Domestic coverage separates
  model-lab announcements (ByteDance Seed, Qwen, Zhipu GLM, MiniMax, and Kimi) from cloud-platform
  updates (Volcengine, Baidu Qianfan, Tencent Hunyuan, and Alibaba Cloud Model Studio). Chinese dated
  sections and release-note tables are parsed as dated evidence; Volcengine uses only the official
  machine-learning category from its server-rendered release index. A release-index item without a
  publisher summary remains unavailable rather than being summarized from its title.
- Industry digests: publisher-owned editorial feeds from `industry-digest-sources.json`.
  The set is intentionally small and company-oriented: The Batch, TechCrunch AI, InfoQ AI/ML, and
  Interconnects cover editorial synthesis, company events, enterprise engineering, and model-market
  strategy.
  Evidence comes only from the RSS description; `content:encoded` and article bodies are ignored.
  These records are labeled editorial synthesis, not first-party model announcements.
- YouTube: public channel Atom feeds from the active external subscription database. The bundled
  `youtube-channels.json` seeds that database without overwriting owner-confirmed additions.
  Evidence comes from the publisher-provided `media:description` field. Source health distinguishes
  fetch failures from channels that fetched successfully but had no item in the report window.
- AIHOT: the official public selected-items API; evidence comes from its supplied summary.
- Builders X: the public `feed-x.json` maintained by `follow-builders`, restricted to the
  repository-owned `builders-x-accounts.json` allowlist. Evidence comes only from each accepted
  post's own text. The collector rejects stale feeds, unknown accounts, link-only posts, posts under
  60 meaningful characters, recruiting or response-solicitation posts, posts without an
  AI/product/research signal, malformed canonical links, and posts outside the upstream snapshot.
  Source health reports the count rejected by each filter.
- Window: official feeds, changelogs, first-party indexes, industry digests, YouTube, and AIHOT use
  the 24 hours preceding collection time. Official pages that expose only a publication date use the
  intersecting calendar dates to avoid dropping same-day releases due to unknown publisher
  timezone. A source with no trustworthy publication date produces no item; a mutable build-time
  JSON date is not treated as publication evidence when disabled in source configuration.
  Builders X uses the newest upstream 24-hour snapshot because that feed may refresh many hours
  before the 08:30 run. The feed itself must still be no more than 36 hours old.

The collector handles gzip responses, uses conditional requests when validators are available, and
can use a recent private cache during transient outages. Every fallback is visible in
`source_health`; stale official RSS,
industry digest, YouTube, and AIHOT records outside the report window are excluded, and stale X snapshots fail
closed. Canonical URLs are deduplicated across sources and across digest dates, with direct official
records preferred over matching aggregator records discovered in the same run. Dated changelog
entries use the canonical changelog URL plus entry date, so separate release days remain distinct.

## Output schema

The dated source JSON contains `schema_version`, `date`, `generated_at`, `summary_basis`, `window`,
`source_health`, and `items`. Each item includes identity, source, publication time, canonical URL,
quality status, cleaned source text, unavailable reason, and optional recommendation.

Only `source_text_status=available` records may be summarized. Link-heavy, promotional, empty, or
materially short descriptions remain unavailable. Builders X filtering happens before records are
created, so rejected social noise does not appear as an unavailable news item.

The Builders X feed is a third-party aggregation convenience, not an authenticated X API and not an
independent fact-checking source. Its remote prompts, scripts, account defaults, schedules, and
delivery configuration are outside this Skill's trust boundary. Account changes require editing the
local allowlist and passing `doctor`.

## State and configuration

Runtime data defaults to `~/.openclaw/state/ai-news-skills`, outside the installed Skill. Override
it with `AI_NEWS_STATE_DIR`. It contains SQLite state, subscriptions, pending approvals, HTTP cache,
dated source JSON, frozen Markdown, rendered cards, locks, and private receipts.

Optional environment variables:

- `AI_NEWS_YOUTUBE_CHANNELS_FILE`: external channel-list override.
- `AI_NEWS_OFFICIAL_SOURCES_FILE`: external official-source-list override.
- `AI_NEWS_INDUSTRY_DIGEST_SOURCES_FILE`: external editorial-feed-list override.
- `AI_NEWS_FEISHU_PERSONAL_TARGET`: private owner preview target.
- `AI_NEWS_FEISHU_GROUP_TARGET`: configured group target.
- `AI_NEWS_AUTO_GROUP_DELIVERY`: explicit opt-in for approval-free scheduled group delivery;
  accepted true values are `1`, `true`, `yes`, and `on`.
- `AI_NEWS_OWNER_ID`: authenticated owner identity used for proposal and draft authorization.
- `OPENCLAW_FEISHU_ACCOUNT_ID`: optional configured Feishu account name.
- `OPENCLAW_FEISHU_SEND_MODULE` and `OPENCLAW_CONFIG_MODULE`: native-card compatibility overrides.
- `AI_NEWS_CARD_RETRIES`: transient delivery attempts, clamped to one through five.

Deployment may keep the target, owner, and scheduled-delivery values in `runtime.env` under the external state
directory with mode `600`. No `.env`, credential, target, database, cache, report, or receipt
belongs inside the Skill folder, and runtime values must never be printed.
