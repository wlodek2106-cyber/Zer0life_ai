# Manual broadcasts and retired legacy API

## Scope

This change closes unauthenticated manual broadcasts and retires the insecure
alternative payout/chat API. It does not change the current wallet ledger, signing
keys, token mint, balances, OTP flow, Boots checkout, or Zer0Net social APIs.
No historical JSON/SQLite files are deleted, migrated, loaded, or rewritten by
the retired entrypoint.

## Manual broadcast contract

`POST /broadcast` now requires JSON with `initData` and `text`.
`initData` must be a valid, unexpired Telegram session signed for this bot, and
the verified identity must equal the existing administrator ID. A body-provided
telegramId, admin flag or permissive CORS policy does not grant access.

- Guest/forged/expired sessions: 401; a verified non-administrator: 403.
- Text must be nonempty and at most 4096 UTF-16 units.
- The body read is capped at 64 KiB, including chunked requests.
- One manual broadcast can be queued per 60 seconds per application process.
  Overlapping/too-frequent requests get 429 with Retry-After.
- A successful request returns 202, success=true, status=queued and recipients.
  Queued does not mean all recipients have received the message.
- Authentication/queue failures return a generic error, never raw exceptions
  or Telegram sessions. Failed queue attempts do not consume the cooldown.
- This local cooldown is not a distributed or durable queue; multiple workers
  or restarts do not share it. Authorization still applies independently.

There is no in-repository UI or script calling the old HTTP broadcast contract.
Any external operator client must now send the administrator's genuine Telegram
initData. Do not put that session in source code, URLs, logs, or shared scripts.
Existing scheduled notifications and administrator bot commands are unchanged.

## Legacy entrypoint behavior

The supported application entrypoint remains `python bot.py`.
If a service still starts `python payout_server.py` or loads
`payout_server:app`, it now serves an explicit retirement response instead of
mutating balances or exposing private messages:

- All old business paths, both plain and /api-prefixed, return HTTP 410 with
  success=false. This includes withdrawal, deposit verification, casino,
  registration, statistics writes, friends, chats, and unknown paths.
- No fake transaction signature is returned.
- No farming thread, disk migration, RPC client, or Telegram sender starts.
- GET /health returns 200 with status=retired and success=false, indicating only
  that this compatibility service is reachable, not that the game API is healthy.
- No static directory is exposed.

If a deployment still uses the retired entrypoint, deliberately update it to
the existing bot.py application after checking its persistent disk and normal
configuration. Do not revive the old balance/chat code or delete its historical
database to make a health probe green. Old farm records remain historical data;
this change does not restart their accrual or settle them.

## Review and deployment verification

The PR alone does not update production. Merge/deploy only after approval.
GitHub Pages and Render are separate publications; these fixes require Render.
Do not trigger a real broadcast as a smoke test.

An empty JSON POST to /broadcast must return 401 without any side effect and
X-Zer0Life-Admin-API: authenticated-broadcast-1.
The retired entrypoint returns X-Zer0Life-Legacy-API:
retired-no-data-access-1. Use a local temporary service to test retirement; do
not change the live service's entrypoint merely for testing.

All regression checks use synthetic signed sessions, recipients, queues and
temporary database fixtures. No real Telegram delivery or Solana payout is
required to verify these guards.
