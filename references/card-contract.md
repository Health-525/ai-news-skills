# Card contract

Frozen Markdown is the immutable boundary between model judgment and deterministic delivery. The
validator requires exactly the same URL set as the dated source JSON and rejects missing, duplicated,
or invented records.

The maintained Feishu card displays:

- an optional full-report link on the first card only, taken from `AI_NEWS_DAILY_REPORT_URL`. The
  link is deployment configuration, never a repository literal. When it is unset the element is
  omitted entirely; when it is set to anything other than a plain HTTPS URL, card rendering fails
  closed rather than emitting an unusable link;
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
- highlights are selected globally with a zero-to-six daily limit; source sections without a
  qualifying highlight remain complete and folded instead of receiving a filler highlight;
- recovered-only sections display a catch-up count instead of the misleading label `AI 判断 0`;
- recovered records stay in their original source section after current-window records and carry a
  compact `补录` marker instead of being moved to separate catch-up cards; highlights stay expanded
  while every folded record keeps its title-link, source, and full source-bounded summary;
- `来源摘要` and an optional source-provided recommendation;
- item bodies show only title, source, source-bounded summary, and an optional recommendation;
  deterministic ranking and evidence metadata remain in the private source artifact rather than
  being exposed in the daily reading surface;
- the orange AI 前哨 header.

The renderer keeps current and recovered records together by source section and splits the ordered
digest only when its complete summary representation exceeds the safe card limit; it never truncates records. Delivery
hashes the rendered cards and target, then writes a private receipt. A retry with
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
