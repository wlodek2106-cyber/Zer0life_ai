# Archived balances and farms

This recovery is an owner-approved accounting adjustment, not a blockchain
verification of historical browser data. Deploying this code does not credit
accounts automatically and does not restore a Render disk snapshot.

## Owner workflow in Telegram

1. Open the updated Mini App from `@Zer0lifelabs_ai_bot` on the configured
   administrator's Telegram account.
2. Open **Wallet → Восстановление · только владелец**.
3. Select **Загрузить архив игроков**. Existing server-held pending balances
   appear even if those players have not reopened the app.
4. Review each frozen request, currencies and totals. Device-only historical
   farms appear after the player opens the updated app on the original device.
5. Select **Вернуть архив на основные балансы** and confirm the exact totals.
   This authorizes spending and withdrawing these amounts from real project
   treasury funds. Requests are processed in batches of up to 50 players.
6. Reload the list to process any remaining or newly submitted records.

The administrator is the existing `ADMIN_TELEGRAM_ID` from the bot configuration,
not a client-supplied nickname. Other players cannot list or approve recoveries.

## What is credited

- The account's server-held pending ZRL and pending SOL, exactly once.
- Approved SOL principal from old device farms that has not already been
  returned or migrated into verified farming.
- Existing spendable balances remain additive and are not overwritten.

In the inspected legacy client, SOL farm principal was removed through pool
turnover, but creating a ZRL farm did **not** subtract its nominal amount from
the ZRL account balance. Therefore ZRL farm nominals are retained in the audit
history but are not minted a second time. A different historical implementation
requires debit evidence and separate reconciliation before an additional credit.

Already claimed rewards are not paid twice. Historical farm records closed by
recovery do not continue accruing yield. Existing verified farms are unchanged.
Original device archives remain intact and are labelled as resolved.

## Safeguards and backup limits

Uploads only stage requests; they never credit money. Approval compares the
frozen digest and current server pending amounts, rejects verified-farm
collisions, and applies the entire selected batch atomically. Repeated or
concurrent approvals cannot credit the same balance or farm twice. Recovery
credits do not generate referral commissions.

A native SQLite backup is created and integrity-checked before approval.
It is on the same persistent disk: it does **not** protect against losing or
replacing that disk. Keep off-disk backups before any Render disk restore.

## Phantom and withdrawals

Inside Telegram, connect opens Phantom's official encrypted mobile approval
flow and returns to the correct bot. The app stays in Telegram; opening the
whole app in Phantom's browser is not required. An injected desktop Phantom
provider remains supported.

Callbacks only obtain signatures. Linking and transfers still require the
authenticated Telegram account and the existing single-use server challenge.
Both ZRL and SOL withdrawals require a linked wallet and a valid signature.
The existing daily ZRL withdrawal limit remains unchanged.

Actual transfers require a correctly configured and funded treasury, mainnet
RPC, and the correct ZRL mint/decimals. Never enter private keys in chat or
commit them to the repository.

The signed transaction hash is saved before broadcast. If the RPC response is
uncertain, funds remain reserved instead of being refunded while a transaction
may still execute. Use the pending-withdrawal check in Wallet: confirmed chain
failures refund exactly once; confirmed transfers remain debited. A transaction
not found by RPC is not, by itself, proof that it is safe to refund.

Local automated tests use generated keys, isolated SQLite databases and an
offline RPC. They do not prove real iPhone/Phantom deep-link behavior or actual
mainnet payouts; those require an explicitly authorized device/treasury test.
