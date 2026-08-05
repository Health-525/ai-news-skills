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
- Bilibili: public submission-list metadata for every account in `bilibili-accounts.json`. The
  configured accounts are collected in full without an AI topic gate. Evidence is limited to the
  publisher-supplied submission description; the collector never opens video pages, reads captions,
  downloads media, or transcribes content. Empty or materially short descriptions remain
  unavailable. Requests use Bilibili's public WBI signature, run serially with bounded retries, and
  retain only successful responses in the 72-hour HTTP fallback cache. Account-list failures are
  isolated per account and reported in `source_health`; a rejected response never masquerades as a
  quiet account or overwrites a previously successful cache entry.
- AIHOT: the official public selected-items API; evidence comes from its supplied summary.
- GitHub open-source radar: official GitHub repository Search API metadata selected by the topics in
  `github-radar.json`. It excludes forks, archived repositories, missing descriptions, and projects
  outside the bounded creation window. On the first run, only recently created repositories meeting
  both absolute-Star and Star-per-day thresholds are emitted. Later runs compare private SQLite
  snapshots and emit only repositories meeting the configured Star-gain threshold. Total Stars,
  Forks, creation date, latest push date, language, license, topics, and repository description are
  attributed to the GitHub API snapshot; popularity is not treated as an endorsement or a security
  review. The collector does not fetch README files, source code, issues, or arbitrary repository
  links. Search requests run serially, stop after rate-limit exhaustion, reuse a short-lived local
  cache for same-run retries, and expose stale fallback or query failures in `source_health`.
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
  GitHub radar entries are observation events timestamped when a repository first crosses the
  bootstrap threshold or later crosses the snapshot-growth threshold; they are not presented as
  repository publication dates.

The collector handles gzip responses, uses conditional requests when validators are available, and
can use a recent private cache during transient outages. Every fallback is visible in
`source_health`; stale official RSS,
  industry digest, YouTube, Bilibili, and AIHOT records outside the report window are excluded, and stale X snapshots fail
closed. Canonical URLs are deduplicated across sources and across digest dates, with direct official
records preferred over matching aggregator records discovered in the same run. Same-host official
or editorial records with the same normalized title and publication date are also treated as one
event, preventing duplicate documentation routes from producing duplicate cards. Cross-host
records on the same date are merged only when at least one is editorial and their normalized
titles are identical or extremely similar; separate official announcements and materially
different editorial analysis remain independent records. Dated changelog
entries use the canonical changelog URL plus entry date, so separate release days remain distinct.

## Output schema

The dated source JSON contains `schema_version`, `date`, `generated_at`, `summary_basis`, the
24-hour `window`, the overlapping `collection_window`, `source_health`, and `items`. Aggregated
official and editorial health includes per-source checks with status, accepted item count, cache
usage, and a sanitized failure reason so one broken route cannot hide behind an otherwise successful
collection. Each item includes identity, source, publication time, `recency_status`, canonical URL,
quality status, cleaned source text, unavailable reason, and optional recommendation. A `current`
item falls inside the primary 24-hour window; a `recovered` item is an unseen entry recovered from
the overlap after source onboarding, delayed publication, or a transient collection failure.

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
- `AI_NEWS_BILIBILI_ACCOUNTS_FILE`: external Bilibili account-list override.
- `AI_NEWS_OFFICIAL_SOURCES_FILE`: external official-source-list override.
- `AI_NEWS_INDUSTRY_DIGEST_SOURCES_FILE`: external editorial-feed-list override.
- `AI_NEWS_GITHUB_RADAR_FILE`: external GitHub topic and threshold configuration override.
- `AI_NEWS_GITHUB_TOKEN`: optional read-only GitHub API token. Public collection works without it;
  when configured, keep it only in the private runtime environment and grant no write permission.
- `AI_NEWS_FEISHU_PERSONAL_TARGET`: private owner preview target.
- `AI_NEWS_FEISHU_GROUP_TARGET`: configured group target.
- `AI_NEWS_AUTO_GROUP_DELIVERY`: explicit opt-in for approval-free scheduled group delivery;
  accepted true values are `1`, `true`, `yes`, and `on`.
- `AI_NEWS_RELEASE_ANNOUNCEMENTS`: explicit opt-in for post-deployment group update cards. A real
  announcement additionally requires an exact deployed-commit match and an idempotent version
  receipt.
- `AI_NEWS_OWNER_ID`: authenticated owner identity used for proposal and draft authorization.
- `OPENCLAW_FEISHU_ACCOUNT_ID`: optional configured Feishu account name.
- `OPENCLAW_FEISHU_SEND_MODULE` and `OPENCLAW_CONFIG_MODULE`: native-card compatibility overrides.
- `AI_NEWS_CARD_RETRIES`: transient delivery attempts, clamped to one through five.

Deployment may keep the target, owner, and scheduled-delivery values in `runtime.env` under the external state
directory with mode `600`. No `.env`, credential, target, database, cache, report, or receipt
belongs inside the Skill folder, and runtime values must never be printed.
