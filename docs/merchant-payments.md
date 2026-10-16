# Boots merchant payments

Purchases debit the player's verified SOL game balance and settle the exact
package price from the existing pool signer to the fixed Merchant address
declared in `merchant_payments.py`. The client cannot select a recipient, rate,
duration or fee. The project pays the network fee separately.

This change does not settle historical purchases, move players' deposits to a
new address, change Farming, send casino wagers to Merchant, or change the
wallet connection provider. Casino wagers are not equivalent to sales revenue:
SOL still owed to players must remain available for payouts.

## Safety and accounting

- A persistent purchase ID and atomic SOL reservation prevent repeat charges.
- A purchase-specific Memo makes distinct purchases distinct transactions even
  when amount and recent blockhash are identical.
- Signed bytes, signature and last valid block height are committed before
  broadcast. Retries rebroadcast only those same signed bytes.
- Boots activate only after **finalized** successful payment.
- A preparation failure, finalized chain failure, or finalized block-height
  expiry plus missing historical signature returns principal exactly once.
- An uncertain RPC response keeps the reservation, never refunds blindly, and
  never creates a replacement transaction.
- Remaining SOL game balances, active SOL farm principal, pending SOL withdrawals
  and pending merchant payments must remain covered by pool funds plus the fee.
  Underfunding fails the sale and returns the reservation, without touching
  another player's ledger or issuing free Boots.
- Game balances and active boost state are server-authoritative. A merchant
  receipt and pending purchases survive reloads and process restarts.
- Already applied boosters are not changed if a new purchase fails.

## Rollout

Apply this independently from the embedded-wallet pull request. No Privy,
GitHub Pages build-mode switch or wallet migration is required for this fix.
The new root-level Python module must deploy alongside `wallet_service.py`.
The existing server-only `PRIVATE_KEY` must control the configured pool; it is
not the Merchant key. Do not change the pool address or paste any keys into chat.
No new credentials or dependencies are required.

Deploy backend and frontend together where possible. Updated frontend fails
closed while `/wallet/state` lacks `merchantPaymentsEnabled: true`.
An older cached frontend can reserve a purchase but must not be used to judge
its completion; reload the updated page to reconcile it.

New status route: `POST /wallet/purchases/status`, signed Telegram `initData`
and `purchaseId`. Only the owning player can query/process the payment.
The UI polls while open; after closing Telegram, reconciliation resumes on
the next open. A background reconciliation worker and an operator alerting
dashboard are not included.

No historical sales are billed or transferred automatically.

## Verification

Python tests construct and verify actual Solana signatures with generated,
synthetic keys and a fake RPC. Node tests exercise the real wallet controller.
They cover recipient integrity, amounts, finality, refunds, lost responses,
identical-byte rebroadcast, identity isolation, concurrent replays, restart,
release gating and missing/underfunded treasury configuration.

No real mainnet transaction has been sent or verified during development.
A controlled real purchase requires an adequately funded pool, correct existing
server signer and explicit owner approval. Confirm its explorer transaction,
Merchant receipt, exact game balance debit and Boots activation before rollout
to all players.
