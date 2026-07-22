# Subscription workflow

## Interaction

1. Send the native subscription guide card when the owner asks to add channels.
2. Accept up to 50 channel-home links or channel IDs in one private reply, separated by lines or
   spaces. The card is guidance; current OpenClaw Feishu event parsing does not preserve CardKit
   free-text form values reliably.
3. Resolve only allowlisted YouTube hosts. Reject video, playlist, redirect, and non-YouTube URLs.
4. Verify every resolved channel against its public Atom feed. Mark each input as `valid`,
   `duplicate`, `invalid`, or `unavailable`.
5. Send the batch result card with exact confirmation and cancellation commands. Do not mutate
   subscriptions yet. Current Feishu schema 2.0 delivery rejects the legacy `action` container, so
   do not depend on an interactive button callback.
6. Add only `valid` items after the same authenticated owner confirms that exact proposal.

Proposals expire after 24 hours. A different requester cannot inspect, confirm, or cancel them.
Unavailable items are never silently accepted; the owner can submit them again later.

## Commands

```bash
python {baseDir}/scripts/daily_pipeline.py subscription-form --send
python {baseDir}/scripts/daily_pipeline.py subscription-propose --requester-id AUTHENTICATED_ID --input-file PRIVATE_FILE --send
python {baseDir}/scripts/daily_pipeline.py subscription-confirm --requester-id AUTHENTICATED_ID --proposal-id PROPOSAL_ID
python {baseDir}/scripts/daily_pipeline.py subscription-cancel --requester-id AUTHENTICATED_ID --proposal-id PROPOSAL_ID
python {baseDir}/scripts/daily_pipeline.py subscriptions
```

Store temporary input under the external state directory and remove it after processing when
practical. Never place submitted messages in the Skill folder.
