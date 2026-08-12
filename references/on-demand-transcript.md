# On-demand YouTube transcript

This capability is separate from collection and scheduled delivery. It handles only an explicit request
from an authenticated member of the configured Feishu group for one public YouTube video.

## Contract

1. Read the sender identity only from trusted Feishu event metadata and require group context.
2. Accept only a public HTTPS YouTube video URL. The deterministic command canonicalizes the video ID
   and rejects channels, playlists, arbitrary hosts, credentials, fragments, and malformed URLs.
3. Call Supadata's universal transcript endpoint with `text=true` and `mode=native`. Never use `auto`,
   `generate`, translation, media download, or another transcript provider.
4. Ordinary members may consume one request per `Asia/Shanghai` date. A pending or consumed request
   blocks another request that day. Reservations abandoned for ten minutes are released. The configured
   owner bypasses the quota but still creates a private audit row.
5. A successful transcript, an unavailable-caption response, or an empty billed transcript consumes the
   ordinary member's daily request. Authentication, rate-limit, network, and service failures release it.
6. Store transcript text only under the private external state directory with restrictive permissions.
   Return only its path and bounded metadata to the agent; never print the API key or requester identity.
7. Use the transcript only to answer the current request. Prefer a concise Chinese summary or direct
   answer with the original YouTube link. Do not paste the full transcript into a group unless the owner
   explicitly requests it and the message-size limit is respected.

The API key belongs only in `runtime.env` as `AI_NEWS_SUPADATA_API_KEY`. Do not place it in the Skill,
OpenClaw cron prompt, Feishu card, Bitable, Miaoda, logs, receipts, or generated artifacts.
