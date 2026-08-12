# On-demand YouTube transcript

This capability is separate from collection and scheduled delivery. It handles only an explicit request
from an authenticated member of the configured Feishu group for one public YouTube video.

## Contract

1. Read the sender identity only from trusted Feishu event metadata and require group context.
2. Accept only a public HTTPS YouTube video URL. The deterministic command canonicalizes the video ID
   and rejects channels, playlists, arbitrary hosts, credentials, fragments, and malformed URLs.
3. Call Supadata's universal transcript endpoint with `text=true` and `mode=native`. Never use `auto`,
   `generate`, translation, media download, or another transcript provider.
4. Authenticated group members may make unlimited explicit requests. Pending, completed, and failed
   requests never block another request. Reservations abandoned for ten minutes are marked failed.
5. Create a private audit row for every attempt, including successful, unavailable, and upstream-failed
   requests. The owner marker is retained only for auditing and does not change access or limits.
6. Store transcript text only under the private external state directory with restrictive permissions.
   Return only its path and bounded metadata to the agent; never print the API key or requester identity.
7. Use the transcript only to answer the current request. Prefer a concise Chinese summary or direct
   answer with the original YouTube link. Do not paste the full transcript into a group unless the owner
   explicitly requests it and the message-size limit is respected.

The API key belongs only in `runtime.env` as `AI_NEWS_SUPADATA_API_KEY`. Do not place it in the Skill,
OpenClaw cron prompt, Feishu card, Bitable, Miaoda, logs, receipts, or generated artifacts.
