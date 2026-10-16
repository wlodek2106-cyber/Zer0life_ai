# Embedded Solana wallet inside Telegram

The existing game ledger, deposits, farming, recovery archives and withdrawal limits are unchanged.
Privy performs Telegram Mini App seamless authentication. Wallet creation requires a deliberate click.
The SDK creates at most one embedded Solana wallet for the authenticated Privy user.
No private key is generated, stored or exported by the application server.

## Account and signature checks

- `/wallet/state` returns `telegramId` from verified Telegram initData, never from a caller-supplied ID.
- The SDK Telegram user must match that server identity before creation, signing or recovery export.
- Only a Privy-owned Solana wallet present in that authenticated user's linked accounts is selected.
- Link and withdrawal still sign the exact existing server nonce/challenge. The backend verifies Ed25519 proof.
- Changing an existing bound address is deliberately blocked. A different embedded address is NOT a transfer
  of funds from an existing external wallet. Users with an existing different binding need a separately
  designed, authenticated address-change process; do not bypass it with a database update.
- Game SOL/ZRL and on-chain SOL/ZRL remain distinct. Creating a wallet credits neither balance.
- Export uses Privy's separate-domain recovery UI, after an explicit warning; the app never receives the key.

## Public provider configuration

The public App ID is in `wallet-embedded/src/main.jsx`. No Privy App Secret is needed by this client.
For the existing Telegram bot, configure Telegram OAuth and seamless authentication in Privy.
Allow the production origin `https://wlodek2106-cyber.github.io`; if Telegram web is supported also allow
the Telegram web origins documented by Privy. Do not disable domain checks as a workaround.
The current provider app is a development environment. Review Privy's production activation, allowed
domains, MFA and recovery configuration before a public production launch; do not claim production readiness
based on fixture tests.

## Build and release

This repo previously published its root directly with legacy GitHub Pages. The SDK requires bundling;
publishing the raw repo will not produce `wallet-embedded/embedded-wallet.js`.

1. Review and explicitly approve the frontend deployment change before applying it.
2. Configure GitHub Pages **Build and deployment → Source → GitHub Actions**, rather than a source branch.
   Keep the existing Pages URL. The workflow deploys the generated public-only site.
3. Merge the approved change. Render must deploy the updated `wallet_service.py` as well. Until the
   server returns its authenticated `telegramId`, the frontend fails closed for wallet creation/withdrawal.
4. Confirm the `Build Telegram wallet and Pages` workflow succeeds and the built entry/chunks are served.
5. In the real Telegram Mini App on iPhone and Android, validate two separate accounts, persistence across
   relaunch, cancellation and account mismatch. Only the owner may authorize real financial test transfers.

Local checks:

```
pnpm --dir wallet-embedded install --frozen-lockfile
node --test tests/*.test.cjs
pnpm --dir wallet-embedded test
pnpm --dir wallet-embedded build:site
```

The workflow uses a lockfile and keeps the package-age safety gate. Generated SDK/site files are ignored
and not committed. Its public allow-list excludes Python, databases, secrets, tests and node_modules.
The embedded-wallet rollout also retains the live merchant-payment implementation:
Boots still reserve game SOL and settle to the configured Merchant before activating.
Do not overwrite merchant routes, receipts, UI finality handling or its tests when
rebasing this integration. The public `X-Zer0Life-Wallet-Schema` response header
identifies the combined backend without authenticating as a player.

Preserved Phantom mobile API routes and their tests remain for outstanding historical requests, but the
dashboard no longer loads the external handoff script or offers its connect links.
