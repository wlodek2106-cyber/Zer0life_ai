# Archived balances and farms

This recovery is an owner-approved accounting adjustment, not a blockchain
verification of historical browser data. Deploying this code does not credit
accounts automatically and does not restore a Render disk snapshot.

## Owner workflow in Telegram

1. Open the updated Mini App from `@Zer0lifelabs_ai_bot` on the configured
   administrator's Telegram account.
2. Open **Wallet → Archive recovery · owner only**.
3. Select **Check update and load archives**. Existing server-held pending balances
   appear even if those players have not reopened the app.
4. Review each frozen request, currencies and totals. Device-only historical
   farms appear after the player opens the updated app on the original device.
5. Select **Restore archives to main balances** and confirm the exact totals.
   This authorizes spending and withdrawing these amounts from real project
   treasury funds. Requests are processed in batches of up to 50 players.
6. Reload the list to process any remaining or newly submitted records.

### Archives requiring reconciliation

The owner list returns independently prepared accounts in `requests` and
account-specific failures in `quoteErrors`. Each error identifies `telegramId`,
`username`, `requestId` (when a pending request exists), `stage` (`staging` or
`quote`), the error text and `requiresReconciliation: true`.

Invalid dates, yield overflow, unreadable saved data or missing original farm
terms do not hide the other accounts. The panel marks these archives
**Reconciliation required**, displays the cause and excludes them from approval
and confirmation totals. Error-only lists keep approval disabled; successful
approval of valid accounts leaves the unresolved errors visible.

Failed preparation rolls back that account's list-preparation writes, preserving
the original request, digest and records. It does not replace unknown yield with
zero, discard missing farm terms, approve principal from an invalid archive, or
alter balances. Reconcile against the original archive through a separately
reviewed process, then reload; this interface does not edit damaged records.

Approval revalidates the frozen farm calculation before settlement. The entire
selected batch still uses one transaction: if any selected request is invalid,
no account in that batch receives a credit. Per-account savepoints are used only
for preparing the list, never for partial approval.

The administrator is the existing `ADMIN_TELEGRAM_ID` from the bot configuration,
not a client-supplied nickname. Other players cannot list or approve recoveries.

## Independent website and API rollouts

Recovery contract release: `recovery-isolation-2026-10-17`, protocol `1`.
The website and API must advertise this exact recovery release plus
`owner-approved-principal-yield-v2`, `release-gated-approval-v1` and
`per-account-quote-errors-v1`.
This identifier describes the recovery contract, not every unrelated website
change. When changing recovery semantics, update the Python contract, the
JavaScript expectation, the dashboard data attribute and script cache keys
together. The regression test checks these stay aligned.

The owner panel starts with approval disabled, probes the public API on opening
and before loading requests, and checks again both before and after the native
confirmation. A missing endpoint, timeout, incompatible protocol, missing
capability, or cached HTML/wallet script fails closed with “Recovery update in
progress.” A failed reload also discards any previously reviewed list. Use
**Check update and load archives** after the update finishes; requests must be
reviewed again. No automatic approval or accounting migration occurs.

New API code rejects owner list/apply requests without the matching
`recoveryClient: {releaseId, protocol}` with HTTP 409 and
`code: recovery_release_mismatch`, before request generation, backups or balance
writes. This public client declaration is a compatibility check, not an
authentication credential: valid signed Telegram data and owner authorization
remain required. Staging player archives remains available independently.

| Website | API | Result |
| --- | --- | --- |
| New | Old / missing `/release` | Update message; no approval request sent |
| Old | New | Owner list/apply rejected before writes |
| New | New, matching | Load, explicit review and confirmation are available |
| New | New, mismatched release/capability | Update message; review discarded |
| Old | Old | Neither side contains this guard; update API first |

Cached old dashboard HTML may lack the panel entirely. If an old recovery script
is still accessible, the new API guard still rejects its unversioned approvals.
No new code can retroactively protect an entirely old website/API pair. Deploy
the API first and avoid rolling back to an API predating this guard while
approval is in use.

### Confirm Render rollout without a Telegram session

After the source change is merged and the normal deployment runs, make a plain
GET request to the configured API's `/release` endpoint. It requires no
`initData`, account identity, cookie or authorization header and performs no
database access. The response is not cached:

```json
{
  "success": true,
  "releaseId": "recovery-isolation-2026-10-17",
  "sourceCommit": "<40-character Render source commit, or null outside Render>",
  "recovery": {
    "protocol": 1,
    "capabilities": ["owner-approved-principal-yield-v2", "release-gated-approval-v1", "per-account-quote-errors-v1"],
    "ready": true
  }
}
```

Compare `sourceCommit` with the source revision selected in Render and compare
the recovery contract with the deployed Pages source. The commit is allowlisted
to a hexadecimal SHA only; no other environment values, paths, wallet addresses,
Telegram IDs or account data are returned. `X-Zer0Life-Release` on bot API
responses is a secondary contract marker. An expected GitHub commit or a 401
from `/wallet/state` alone is not proof this API release is running. If
`sourceCommit` is null, the contract is visible but exact revision verification
is unavailable; do not infer a deployed commit.

Offline checks:

```sh
python -m unittest discover -s tests -p 'test_recovery_mobile.py'
node --test tests/recovery-actions.test.cjs
```

## What is credited

- The account's server-held pending ZRL and pending SOL, exactly once.
- Approved ZRL and SOL principal from old device farms that has not already been
  returned or migrated into verified farming.
- Accrued unclaimed ZRL yield, using the saved daily rate and original dates,
  minus already claimed rewards and any previously restored yield.
- Existing spendable balances remain additive and are not overwritten.

Historical deployed clients did debit ZRL farming principal. The disabled
compatibility wrapper inspected during the initial recovery was not reliable
evidence of historical debit behavior. The original recovery consequently
omitted ZRL principal and unclaimed yield; this reconciliation corrects those
omissions additively, including positions it already marked closed.

The registry distinguishes nominal principal from principal actually credited.
For old settlements only SOL principal was paid. Original closure timestamps
remain the yield cutoff, so waiting for the correction does not create extra
post-closure interest. Missing daily rates stay explicitly unresolved; they
are never silently interpreted as zero. Original approved dates, principal,
asset and claimed rewards cannot be changed by a new device submission.

Already claimed rewards are not paid twice. Historical farm records closed by
recovery do not continue accruing yield. Existing verified farms are unchanged.
Original device archives remain intact. A position is fully resolved only
after both its principal and yield have been reconciled. Restoring principal
first does not block a later, separately approved missing-yield adjustment.
Owner review freezes the calculation and exact totals for fifteen minutes;
an old cached review from before the correction is rejected before any credit.

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
