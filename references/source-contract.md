# Source and runtime contract

## Inputs

- YouTube: public channel Atom feeds from the active external subscription database. The bundled
  `youtube-channels.json` seeds that database without overwriting owner-confirmed additions.
  Evidence comes from the publisher-provided `media:description` field.
- AIHOT: the official public selected-items API; evidence comes from its supplied summary.
- Builders X: the public `feed-x.json` maintained by `follow-builders`, restricted to the
  repository-owned `builders-x-accounts.json` allowlist. Evidence comes only from each accepted
  post's own text. The collector rejects stale feeds, unknown accounts, link-only posts, posts under
  60 meaningful characters, recruiting or response-solicitation posts, posts without an
  AI/product/research signal, malformed canonical links, and posts outside the upstream snapshot.
  Source health reports the count rejected by each filter.
- Window: YouTube and AIHOT use the 24 hours preceding collection time. Builders X uses the newest
  upstream 24-hour snapshot because that feed may refresh many hours before the 08:30 run. The feed
  itself must still be no more than 36 hours old, and global item identity prevents an unchanged
  snapshot from entering a later digest twice.

The collector uses conditional requests when validators are available and can use a recent private
cache during transient outages. Every fallback is visible in `source_health`; stale YouTube and
AIHOT records outside the report window are excluded, and stale X snapshots fail closed.

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
- `AI_NEWS_FEISHU_PERSONAL_TARGET`: private owner preview target.
- `AI_NEWS_FEISHU_GROUP_TARGET`: approval-only group target.
- `AI_NEWS_OWNER_ID`: authenticated owner identity used for proposal and draft authorization.
- `OPENCLAW_FEISHU_ACCOUNT_ID`: optional configured Feishu account name.
- `OPENCLAW_FEISHU_SEND_MODULE` and `OPENCLAW_CONFIG_MODULE`: native-card compatibility overrides.
- `AI_NEWS_CARD_RETRIES`: transient delivery attempts, clamped to one through five.

Deployment may keep the three target/owner values in `runtime.env` under the external state
directory with mode `600`. No `.env`, credential, target, database, cache, report, or receipt
belongs inside the Skill folder, and runtime values must never be printed.
