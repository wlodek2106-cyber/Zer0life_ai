# Telegram ingress and startup reliability

The supported entrypoint remains `bot.py`. No new environment settings, balances,
ledger migrations, reward caps, financial signatures or frontend changes are needed.

## Fixed failure modes

- The former HTTP handler acknowledged updates before dispatch finished and swallowed
  enqueue errors. A 200 could therefore prevent Telegram from retrying a lost command.
- Registration ran once and stopped waiting after five seconds. Temporary Telegram
  failures could leave the bot without a usable webhook until a manual restart.
- The webhook URL contained the privileged bot API token.
- Strict content-type comparison rejected otherwise valid JSON with a charset.
- `/start` performed synchronous recipient-file reads/writes on the bot loop.
  It now offloads registration and bounds the wait for ancillary statistics to
  one second. File failure is reported without hiding the launch button.
- Concurrent recipient writes could lose subscribers, and a corrupt file could
  be silently replaced by an almost-empty one. The existing `users.json` format
  is preserved with file locking and atomic replacement; invalid data is not
  overwritten. This is not a financial ledger migration.

## Contract

`POST /telegram/webhook` requires Telegram's
`X-Telegram-Bot-Api-Secret-Token` header. Its value is a domain-separated HMAC-SHA256
derived from the existing bot token. Disclosure of that header alone cannot recover
the bot API token or call privileged Telegram methods. Do not print either value.
Rotating the bot token also rotates the header secret.

The old token-bearing route is not registered. Werkzeug access logs stay disabled
because Telegram may still retry old URLs briefly during migration.

Unauthenticated requests return 403 before parsing a body. Valid requests are bounded
to 1 MiB, validated with aiogram, and acknowledged only after the handler completes.
Processing failures, an unavailable queue or an eight-second acknowledgement timeout
return 503 with Retry-After so Telegram can retry.

Retries of the same update share their running future. Successfully completed update
IDs are remembered for ten minutes, up to 4096 receipts, with at most 64 in-flight
updates. These are in-process safeguards, **not durable exactly-once guarantees**.
A restart or cache eviction may allow redelivery. Financial operation deduplication
continues to depend on the existing ledger and event identifiers.

Registration runs on the existing bot loop without blocking HTTP startup. It retries
transient failures with capped backoff until successful and explicitly preserves
queued Telegram updates (`drop_pending_updates=False`). Chat menu setup is separate;
its failure cannot invalidate successful message registration.

## Release verification

This is a coordinated route change: deploy `bot.py` and `telegram_webhook.py`
together. Startup automatically registers the new URL and header with Telegram.
Do not manually register the new route without its secret header.

After approved publishing:

1. Send an empty unauthenticated request to the new route. Expect 403 and
   `X-Zer0Life-Telegram-Webhook: authenticated-retry-safe-1`. This sends no messages.
2. Confirm the safe `Telegram webhook registered successfully` startup log.
3. The owner can send `/start` to confirm delivery from real Telegram. Do not use
   real broadcasts, withdrawals or deposits as a deployment smoke test.

Tests cover real aiogram `/start` dispatch using a synthetic bot with a no-send
handler, plus rejected signatures, malformed/chunked bodies, retry coalescing,
bounded caches and registration recovery. Tests do not contact Telegram or Solana.
