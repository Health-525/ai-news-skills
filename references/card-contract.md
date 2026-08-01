# Card contract

Frozen Markdown is the immutable boundary between model judgment and deterministic delivery. The
validator requires exactly the same URL set as the dated source JSON and rejects missing, duplicated,
or invented records.

The maintained Feishu card displays:

- total, official news, YouTube, AIHOT, industry digest, Builders X, and model-selected highlight counts;
- source sections ordered as official news, YouTube, AIHOT, industry digest, then Builders X;
- source section headers use the Feishu JSON 2.0 `heading` text size while item bodies retain the
  normal Markdown size;
- model-selected highlights expanded first inside each source section;
- remaining records collapsed immediately after their source highlights;
- `来源摘要` and an optional source-provided recommendation;
- the orange AI 前哨 header.

The renderer splits oversized payloads instead of truncating records. Delivery hashes the rendered
cards and target, then writes a private receipt. A retry with the same target and card hash returns
`skipped`; a mismatched receipt fails closed.

The owner preview appends a separate approval control card containing exact approve/reject commands.
Approval stores and later sends only the exact digest cards, so the group never receives approval
controls. Subscription result cards similarly show exact confirm/cancel commands and use
authenticated requester metadata; they do not trust identities supplied in message text.

Native-card delivery uses the bundled OpenClaw Feishu bridge because the public message CLI does not
preserve this exact collapsible-card shape. Run dry validation after OpenClaw upgrades and use the
documented module overrides if discovery changes.
