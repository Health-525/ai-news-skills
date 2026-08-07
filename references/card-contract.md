# Card contract

Frozen Markdown is the immutable boundary between model judgment and deterministic delivery. The
validator requires exactly the same URL set as the dated source JSON and rejects missing, duplicated,
or invented records.

The maintained Feishu card displays:

- per-card total, current-window, recovered, official news, YouTube, Bilibili, AIHOT, GitHub radar,
  security advisory, model Hub, industry digest, Builders X, and model-selected highlight counts;
- source sections ordered as official news, YouTube, Bilibili, AIHOT, GitHub radar, security
  advisories, model Hub, industry digest, then Builders X;
- source section headers use the Feishu JSON 2.0 `heading` text size while item bodies retain the
  normal Markdown size;
- a compact `今日必看` overview appears only on the first card and lists up to five ranked primary
  event leaders; subsequent cards are labeled as classification appendices;
- records are packed into cards by global editorial score before they are grouped into the
  familiar source sections;
- model-selected highlights expanded first inside each source section;
- remaining current-window records collapsed immediately after their source highlights, while
  recovered records use a separate folded catch-up panel;
- `来源摘要` and an optional source-provided recommendation;
- item bodies show only title, source, source-bounded summary, and an optional recommendation;
  deterministic ranking and evidence metadata remain in the private source artifact rather than
  being exposed in the daily reading surface;
- the orange AI 前哨 header.

The renderer splits oversized payloads instead of truncating records. Delivery hashes the rendered
cards and target, then writes a private receipt. A retry with the same target and card hash returns
`skipped`; a mismatched receipt fails closed.

Before rendering schema-version-2 sources, verify the source-text, record, and source-set hashes.
The private card artifact records source-set, frozen-Markdown, and rendered-card hashes for audit and
reproducibility.

The owner preview appends a separate approval control card containing exact approve/reject commands.
Approval stores and later sends only the exact digest cards, so the group never receives approval
controls. Subscription result cards similarly show exact confirm/cancel commands and use
authenticated requester metadata; they do not trust identities supplied in message text.

Native-card delivery uses the bundled OpenClaw Feishu bridge because the public message CLI does not
preserve this exact collapsible-card shape. Run dry validation after OpenClaw upgrades and use the
documented module overrides if discovery changes.

Release announcements use a separate blue native card after a verified production switch. The card
shows the short commit version, deployment time, bounded change list, and folded verification
results. Its private receipt is keyed by the full deployed commit under `receipts/releases/`, so a
retry of the same version is idempotent. The manifest cannot specify a delivery target, and a real
send fails closed unless its full version matches `.deployment-commit`.
