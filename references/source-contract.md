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
  independently, and page-structure changes surface in `source_health`. Empty RSS/Atom documents,
  changelog pages without parseable dated entries, and newsroom indexes without matching article
  links fail closed instead of being reported as quiet but healthy. Domestic coverage separates
  model and infrastructure vendors (ByteDance Seed, Qwen, Zhipu GLM, MiniMax, Kimi, DeepSeek,
  Huawei AI, SiliconFlow, and SenseNova) from cloud-platform updates (Volcengine, Baidu Qianfan,
  Tencent Hunyuan, and Alibaba Cloud Model Studio). Chinese dated
  sections and release-note tables are parsed as dated evidence; Volcengine uses only the official
  machine-learning category from its server-rendered release index. A release-index item without a
  publisher summary remains unavailable rather than being summarized from its title. AWS What's New
  uses the official all-products feed with an AI-product title allowlist so database, compute, and
  other unrelated service announcements do not enter the digest. GitHub Changelog uses its official
  feed with an AI, model, agent, MCP, and Copilot title allowlist. NVIDIA uses its official newsroom
  feed with an AI product and infrastructure allowlist and explicit gaming exclusions. AWS coverage
  combines the Machine Learning Blog, filtered What's New feed, and official Bedrock, AgentCore, and
  SageMaker AI release-note feeds. Google Agent Platform uses the official Gemini Enterprise Agent
  Platform release feed after the Vertex AI Agent Builder migration. Cloudflare's unified changelog
  and the Databricks release feed use strict AI-product title allowlists. Release-note GUIDs remain
  distinct even when several updates share one documentation URL; routine SageMaker managed-policy
  churn is excluded. StepFun, Baichuan, 01.AI, and iFlytek remain explicit coverage gaps until they
  expose a stable, day-dated first-party feed or a same-domain index with usable metadata; adding a
  source name without collectible evidence is not treated as coverage.
- Industry digests: publisher-owned editorial feeds from `industry-digest-sources.json`.
  The set is intentionally small and company-oriented: The Batch, TechCrunch AI, InfoQ AI/ML,
  Interconnects, MIT Technology Review AI, The Register AI+ML, and the AI-filtered InfoQ China feed
  cover editorial synthesis, company events, enterprise engineering, regulation, infrastructure,
  and model-market strategy. Each feed has a bounded `max_items` value; broad feeds also use AI
  title allowlists, while sponsored, partner, recruiting, course, and advertising titles are
  excluded where applicable. This prevents one high-volume publisher from dominating the folded
  media section.
  Evidence comes only from the RSS description; `content:encoded` and article bodies are ignored.
  These records are labeled editorial synthesis, not first-party model announcements.
- YouTube: public channel Atom feeds from the active external subscription database. The bundled
  `youtube-channels.json` seeds that database without overwriting owner-confirmed additions.
  Evidence comes from the publisher-provided `media:description` field. Source health distinguishes
  fetch failures from channels that fetched successfully but had no relevant item in the report
  window. A conservative title-plus-description topic gate removes clearly unrelated uploads while
  retaining AI models, agents, inference, coding tools, autonomous systems, robotics, and AI
  infrastructure. The health detail reports how many in-window uploads were filtered off-topic.
- AIHOT: the official public selected-items API; evidence comes from its supplied summary.
- GitHub open-source radar: the official daily GitHub Trending page supplies discovery order and a
  bounded candidate set. The official repository metadata API enriches each candidate with its
  owner-provided description, topics, archive/fork status, and current total Stars. A deterministic
  topic-and-keyword gate retains AI projects and excludes unrelated Trending repositories. Reader
  evidence contains the project description and current total Star count; daily Star growth, Forks,
  repository dates, language, license, and topics are not summarized. Popularity is not treated as
  an endorsement or security review. The collector does not fall back to repository Search, fetch
  README files, source code, issues, or arbitrary repository links. It preserves Trending order,
  uses bounded caches, and exposes stale fallback or metadata failures in `source_health`.
- Security radar: GitHub-reviewed global advisories returned by the official GitHub Advisory API,
  restricted to the package and ecosystem allowlist in `security-advisories.json`. Evidence includes
  the reviewed description, affected range, patched version, CVE/GHSA identifiers, severity, and
  update time. An advisory does not prove that a deployment uses an affected version.
- Model Hub radar: public Hugging Face Hub API metadata for organizations in
  `huggingface-radar.json`. Only repositories created inside the overlap window are emitted.
  Evidence is limited to repository identity, creation time, pipeline tag, library, license tag,
  and observed activity counts. This is uploader/platform metadata, not a quality, safety,
  benchmark, adoption, or production-readiness review. Never download model files or execute code.
- Builders X: the public `feed-x.json` maintained by `follow-builders`, restricted to the
  repository-owned `builders-x-accounts.json` allowlist. Evidence comes only from each accepted
  post's own text. The collector rejects stale feeds, unknown accounts, link-only posts, posts under
  60 meaningful characters, recruiting or response-solicitation posts, posts without an
  AI/product/research signal, malformed canonical links, and posts outside the upstream snapshot.
  Source health reports the count rejected by each filter.
- Window: the primary reporting window is the 24 hours preceding collection time. Pull-based
  official feeds, changelogs, first-party indexes, industry digests, YouTube, and AIHOT are fetched
  with a 96-hour overlap, while persistent global identities prevent already-seen records from being
  emitted again. This overlap recovers delayed feed entries, newly added sources, and transient
  failures without routine duplicate delivery. Official pages that expose only a publication date use the
  intersecting `Asia/Shanghai` calendar dates to avoid dropping same-day releases due to unknown
  publisher timezone. A newsroom date serialized as midnight is treated as date-only only when the
  bounded index independently exposes the same calendar date. A source with no trustworthy
  publication date produces no item; a mutable build-time
  JSON date is not treated as publication evidence when disabled in source configuration.
  Builders X uses the newest upstream 24-hour snapshot because that feed may refresh many hours
  before the 08:30 run. The feed itself must still be no more than 36 hours old.
  GitHub radar entries are daily observation events timestamped when they are collected from the
  Trending page; they are not presented as repository publication dates.

Regulatory and safety coverage includes NIST AI, the European Commission AI policy newsroom, and
the UK AI Security Institute's official GOV.UK Atom feed. These sources remain attributed public
sector publications; their inclusion does not make a policy proposal, evaluation, or risk claim a
universal fact.

The collector handles gzip responses, uses conditional requests when validators are available, and
can use a recent private cache during transient outages. Every fallback is visible in
`source_health`; stale official RSS,
  industry digest, YouTube, and AIHOT records outside the report window are excluded, and stale X snapshots fail
closed. Canonical URLs are deduplicated across sources and across digest dates, with direct official
records preferred over matching aggregator records discovered in the same run. Same-host official
or editorial records with the same normalized title and publication date are also treated as one
event, preventing duplicate documentation routes from producing duplicate cards. Cross-host
records on the same date are merged only when at least one is editorial and their normalized
titles are identical or extremely similar; separate official announcements and materially
different editorial analysis remain independent records. Dated changelog
entries use the canonical changelog URL plus entry date, so separate release days remain distinct.

After identity-level deduplication, the newsroom layer may link retained records under one
`event_id`; linking does not delete evidence records or transfer source text between them. It uses a
72-hour window and requires matching signal type plus entity/product/version or sufficiently
specific title evidence. Brand names, generic AI terms, series labels, and coincidental numeric
metrics are not sufficient by themselves.

## Output schema

The schema-version-2 dated source JSON contains `schema_version`, `date`, `generated_at`, `summary_basis`, the
24-hour `window`, the overlapping `collection_window`, `source_health`, `newsroom`, and `items`. Aggregated
official and editorial health includes per-source checks with status, accepted item count, cache
usage, and a sanitized failure reason so one broken route cannot hide behind an otherwise successful
collection. Each item includes identity, source, publication time, `recency_status`, canonical URL,
quality status, cleaned source text, unavailable reason, and optional recommendation. A `current`
item falls inside the primary 24-hour window; a `recovered` item is an unseen entry recovered from
the overlap after source onboarding, delayed publication, or a transient collection failure.

Every record also contains deterministic `event_id`, `signal_type`, `topics`, `entities`, `audiences`, `language`,
`evidence_level`, event role/version/update fields, evidence-topology labels, a confidence score,
an explainable editorial score and component breakdown, `rank_position`, `alert_level`,
`recommended_highlight`, `source_text_sha256`, and `record_sha256`. Records are serialized in
editorial rank order. The top-level `newsroom` summary reports signal-to-event compression,
verification distribution, alert distribution, ranking model, feedback sample count, and a
deterministic fingerprint. See `newsroom-intelligence.md` for semantics and non-truth boundaries.

When `AI_NEWS_OWNER_ID` is configured, ranking can use only that authenticated owner's latest
feedback. Personalization remains neutral until three samples exist and is capped at +/-10 points;
it never changes source text or evidence labels. Top-level provenance records the full source-set
hash, newsroom-summary hash, and deployed code version. Card rendering verifies these hashes and
records the source-set, newsroom, frozen-Markdown, and rendered-card hashes. Hashes provide tamper evidence; they do not
upgrade the truth value of a publisher claim.

Collection applies a publication gate. By default at least 65% of configured official sources must
fetch successfully. A deployment can configure a stricter ratio and required official source names.
A failed gate writes no new dated source artifact and prevents delivery.

Only `source_text_status=available` records may be summarized. Link-heavy, promotional, empty, or
materially short descriptions remain unavailable. Builders X filtering happens before records are
created, so rejected social noise does not appear as an unavailable news item.

Frozen-digest validation also rejects numeric claims that cannot be resolved from the corresponding
`source_text`, including translated unit equivalents. Publication dates in the required `补录`
prefix are metadata and are validated separately. This catches financing, benchmark counts, model
versions, and other title-only numeric claims before card delivery.

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
- `AI_NEWS_GITHUB_RADAR_FILE`: external GitHub Trending AI-filter configuration override.
- `AI_NEWS_GITHUB_TOKEN`: optional read-only GitHub API token. Public collection works without it;
  when configured, keep it only in the private runtime environment and grant no write permission.
- `AI_NEWS_FEISHU_APP_ID`: optional Feishu custom-app ID override used by the Bitable publisher.
- `AI_NEWS_FEISHU_APP_SECRET`: optional custom-app secret override; configure it together with the app ID.
- `AI_NEWS_BITABLE_APP_TOKEN`: target Bitable app token.
- `AI_NEWS_BITABLE_TABLE_ID`: target AI news table ID.
- `AI_NEWS_OPENCLAW_CONFIG`: optional OpenClaw config path override. When the explicit Feishu credential
  pair is absent, the publisher reuses the single Feishu account found in this local config. Multiple
  accounts require `OPENCLAW_FEISHU_ACCOUNT_ID`.
- `AI_NEWS_SECURITY_ADVISORIES_FILE`: external security package allowlist override.
- `AI_NEWS_HUGGINGFACE_RADAR_FILE`: external model-organization allowlist override.
- `AI_NEWS_HUGGINGFACE_TOKEN`: optional read-only Hugging Face token for higher rate limits.
- `AI_NEWS_SUPADATA_API_KEY`: optional Supadata credential for authenticated, group-only, native-mode
  YouTube transcript requests. It is forbidden in scheduled collection and paid generation modes.
- `AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO`: official-source publication ratio from `0` through `1`;
  default `0.65`.
- `AI_NEWS_REQUIRED_OFFICIAL_SOURCES`: optional comma-separated official source names that must be
  healthy before publication.
- `AI_NEWS_FEISHU_PERSONAL_TARGET`: private owner preview target.
- `AI_NEWS_FEISHU_GROUP_TARGET`: configured group target.
- `AI_NEWS_AUTO_GROUP_DELIVERY`: explicit opt-in for approval-free scheduled group delivery;
  accepted true values are `1`, `true`, `yes`, and `on`.
- `AI_NEWS_RELEASE_ANNOUNCEMENTS`: manual release-notice opt-in. Keep it disabled in production;
  enabling and sending a notice requires a separate explicit owner request for the exact version.
- `AI_NEWS_OWNER_ID`: authenticated owner identity used for proposal and draft authorization.
- `OPENCLAW_FEISHU_ACCOUNT_ID`: optional configured Feishu account name.
- `OPENCLAW_FEISHU_SEND_MODULE` and `OPENCLAW_CONFIG_MODULE`: native-card compatibility overrides.
- `AI_NEWS_CARD_RETRIES`: transient delivery attempts, clamped to one through five.

Deployment may keep the target, owner, and scheduled-delivery values in `runtime.env` under the external state
directory with mode `600`. No `.env`, credential, target, database, cache, report, or receipt
belongs inside the Skill folder, and runtime values must never be printed.
