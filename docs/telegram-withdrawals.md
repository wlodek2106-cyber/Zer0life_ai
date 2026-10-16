# Treasury withdrawals confirmed in Telegram

The Wallet shows game SOL/ZRL balances and referrals, not a second personal
wallet. Its page no longer mounts or loads Privy. Existing balances, Farming,
treasury keys, deposit ownership verification and Merchant payment settlement
are not replaced or migrated.

## User flow

1. Choose Withdraw SOL or Withdraw ZRL, enter amount and a Solana wallet address.
2. Confirm the amount and complete destination shown in the Mini App.
3. The authenticated user's existing bot sends a six-digit code in a private
   Telegram message, bound server-side to that user's exact amount and address.
4. Enter the code within five minutes. Only then does the existing treasury
   payout engine reserve funds and sign a transaction.

No wallet address is generated, no recipient key is requested, and no wallet
browser opens. The receiving address is not implicitly made the deposit source
or saved over an old linked account. Do not change deposit attribution merely
because a recipient was used for a withdrawal.

## Security and operations

- Every request, confirm, cancel and status call validates Telegram initData.
- Codes are delivered only to its verified user ID, never a caller's chat ID.
- Only an HMAC of each OTP is stored. Codes, request bodies and bot API URLs
  must not be logged. Bot credentials remain server-side.
- Five wrong attempts lock an approval; at most three requests per ten minutes
  per user. Only one unexpired approval can be active. Request IDs are
  idempotent and owner-scoped.
- A code does not authorize changes to amount, asset, recipient or account.
  Balance, expiry, daily ZRL limit and nonce use are checked inside the same
  SQLite write transaction that reserves funds.
- Cancellation before submission moves no money. An unknown broadcast remains
  reserved, never blindly refunded or resent. Status failure refunds only after
  finalized chain failure, once. Memo includes the nonce to distinguish separate
  identical-amount withdrawals sharing one blockhash.
- Existing game and fund records remain in place. The only new table holds
  approval metadata. Existing wallet-signature routes remain protected for
  compatibility, but the updated UI uses Telegram confirmation instead.
- Requires the correct existing BOT_TOKEN and a private chat started with
  @Zer0lifelabs_ai_bot. Delivery failure leaves game funds untouched.
- Actual payouts still require the correctly configured project treasury,
  SOL fees/liquidity and the correct ZRL mint/token liquidity. Automated tests
  use synthetic keys, mocked delivery and a fake RPC; they do not prove real
  bot delivery or a successful mainnet payout.
