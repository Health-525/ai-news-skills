# Card contract

Frozen Markdown is the immutable boundary between model judgment and deterministic delivery. The
validator requires exactly the same URL set as the dated source JSON and rejects missing, duplicated,
or invented records.

The maintained Feishu card displays:

- per-card total, current-window, recovered, official news, YouTube, AIHOT, GitHub radar,
  security advisory, model Hub, industry digest, Builders X, and model-selected highlight counts;
- source sections ordered as official news, YouTube, AIHOT, GitHub radar, security
  advisories, model Hub, industry digest, then Builders X;
- source section headers use the Feishu JSON 2.0 `section_heading` token configured to render at
  `heading` size on desktop and mobile, while item bodies retain the normal Markdown size;
- cards use the same uncluttered title without global-overview or classification-appendix labels;
- records are packed by stable source order, preserving each source section instead of allowing a
  global editorial score to move a later section ahead of an earlier one;
- model-selected highlights expanded first inside each source section;
- every current source section with available evidence shows at least one highlight; recovered-only
  sections display a catch-up count instead of the misleading label `AI 判断 0`;
- current-window records are rendered in the first card and recovered records in a separate catch-up
  card; each source keeps full summaries only for current highlights, while folded records use a
  compact title-link and source list;
- `来源摘要` and an optional source-provided recommendation;
- item bodies show only title, source, source-bounded summary, and an optional recommendation;
  deterministic ranking and evidence metadata remain in the private source artifact rather than
  being exposed in the daily reading surface;
- the orange AI 前哨 header.

The renderer normally produces one current card followed by one recovered card. It splits a time
window only when its compact representation still exceeds the safe card limit, and never truncates
records. Delivery hashes the rendered cards and target, then writes a private receipt. A retry with
the same target and card hash returns `skipped`; a mismatched receipt fails closed.

Historical artifacts containing the retired `bilibili` source type remain renderable for audit,
but new collection runs do not create that source type or display a Bilibili count.

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
