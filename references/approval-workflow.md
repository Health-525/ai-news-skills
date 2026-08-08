# Digest approval workflow

This workflow applies only to manual group publication. The scheduled digest uses
`scheduled-group DATE` when external runtime configuration explicitly enables direct delivery; it
does not create or consume an approval draft.

`preview DATE` stores the exact rendered digest cards in SQLite before private delivery. The owner
receives those cards and a separate approval control card. The control card is not part of the group
payload.

## Approval guarantees

- Bind every draft to the configured owner and configured group using hashes.
- Accept the requester only from authenticated Feishu metadata.
- Require a pending, unexpired draft; proposals expire after 24 hours.
- Send the exact stored card JSON. Never rebuild or edit content after approval.
- Claim a draft transactionally before delivery and use a per-draft group receipt for idempotency.
- Return a failed claim to pending after delivery failure; mark it sent only after acknowledgement.
- Treat a repeated approval of an already-sent draft as `skipped`.

## Commands

```bash
python {baseDir}/scripts/daily_pipeline.py preview YYYY-MM-DD
python {baseDir}/scripts/daily_pipeline.py approve --requester-id AUTHENTICATED_ID --draft-id DRAFT_ID
python {baseDir}/scripts/daily_pipeline.py reject --requester-id AUTHENTICATED_ID --draft-id DRAFT_ID
```

The group target never appears as a command argument. Manual preview runs must stop after `preview`;
only a later authenticated owner action may call `approve`.
