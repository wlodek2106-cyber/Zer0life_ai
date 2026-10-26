# Withdrawal reliability release

This release preserves the existing treasury signer, destination verification,
token mint, six-decimal ZRL accounting, and daily limits. It does not issue
refunds or send live payouts during deployment.

## Status and receipt contract

- `processing`: broadcast is uncertain; funds stay reserved. A locally signed
  transaction signature alone is not evidence of on-chain delivery.
- `submitted`: the RPC returned the expected signature; funds stay reserved
  until finalized confirmation.
- `confirmed`: the chain reports finalized success.
- `failed`: a pre-broadcast preparation failure or finalized chain failure;
  the existing refund rules return the amount exactly once.

Wallet shows the latest 20 owner-scoped requests, including finalized and failed
requests after reload. A receipt includes asset, amount, destination, time and
transaction link when a signature exists. This is not a complete ledger export.

HTTP 429 gets at most three attempts with short bounded delays. Read calls may
retry; broadcast retries reuse the exact same signed bytes. Ambiguous broadcast
timeouts are not blindly retried/refunded. Persistent RPC quotas still require
the owner to check the provider's limits; retries cannot guarantee availability.

## Publish both services

1. Merge the reviewed PR after checks pass.
2. Wait for the GitHub Pages workflow to succeed.
3. Deploy the same main revision to the existing Render service (auto-deploy
   or Manual Deploy → Deploy latest commit). Do not change keys, mint,
   recipient, or RPC credentials as part of this code release.
4. Verify Render using an **empty POST**, not GET/HEAD. Flask blueprint release
   headers may not be attached to a GET/HEAD 405 response:

   ```sh
   curl -sS -D - -o /dev/null -X POST \
     -H 'Content-Type: application/json' -d '{}' \
     https://zer0life-ai-iz5n.onrender.com/wallet/state
   ```

   An unauthenticated 401 with
   `X-Zer0Life-Withdrawals: receipts-rate-limit-retry-1` verifies this API code
   is serving without obtaining any player's Telegram session or balances.
5. Close and reopen the Telegram mini app. Wallet must show Your withdrawals.
   The launcher and wallet script version were changed to avoid an old cached UI.

Automated tests use only synthetic signers, sessions, RPC responses and funds.
Any actual small-amount payout check requires the owner's explicit approval.
Never refund a successful payout merely because the receiving wallet's history
does not display it; verify mint, recipient owner and token balance delta on-chain.
