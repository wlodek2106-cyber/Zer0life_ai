# Offline balance and Farming recovery checks

Run from the repository root, with Python 3.11+ and Node.js 22+:

```sh
python -m pip install -r tests/requirements-recovery.txt
python tests/run_recovery_checks.py
```

Dependency installation needs a package registry; the **test run itself is
offline**. The runner does not install anything, start a server, call Telegram,
connect Phantom, send transactions or accept database paths or URLs.
It exits nonzero on any failed assertion or timeout. GitHub runs the same
command on relevant pull requests via `recovery-accounting-check.yml`.
No deployment or production operation is part of that workflow.

## Sources and independent expectations

The schema fixture is based on `wallet_service.py` from
[PR 4](https://github.com/wlodek2106-cyber/Zer0life_ai/pull/4)
(merge `1827e975609f974a5410bc55f931d724a42698b9`).
It is handwritten from schema definitions, **not copied from any player DB**.
It deliberately lacks newer tables/columns. Each check creates a synthetic
old SQLite file and a native SQLite backup before starting the current routes.
The first-generation paid-farm registry is also reconstructed with its original
columns so the migration must distinguish nominal principal from paid principal.

`tests/helpers/recovery-storage.cjs` generates two fictional numeric profiles
and executes the real `legacy-recovery.js` against in-memory localStorage.
Python consumes those preserved snapshots through a local subprocess and
passes them into the real authenticated Flask recovery routes. Current
`wallet.js`, `legacy-farms.js`, `recovery-actions.js` and dashboard wiring are
covered by the Node checks; no remote fetch implementation is used.

Expected amounts are independent integer accounting assertions, not values
calculated by the recovery implementation under test:

| Profile | Original available ZRL | Archived ZRL | Farm principal | Unpaid farm income (ZRL) | Final ZRL |
| --- | ---: | ---: | --- | ---: | ---: |
| Synthetic 200 | 25.000003 | 100.000001 | 1,000 ZRL + 0.002 SOL | 75 + 1.5 | 1,201.500004 |
| Synthetic 300 | 7.000004 | 60.123456 | 250 ZRL | 110 | 427.123460 |

Final SOL is respectively 0.012500001 and 0.020700003. New rewards injected
after review add **exactly** 3.000007 ZRL and 9 lamports per profile.
Income uses the saved currency, rate, dates and claimed rewards, with a fixed
clock. The SOL farm's income is ZRL, not additional SOL principal.

## Covered invariants

- Upgrading a populated old SQLite copy preserves every original account
  column and ledger row. An empty newly created `wallet_farms` table does not
  invalidate the historical device farms.
- Before owner approval, uploads only stage evidence/pending amounts. They
  never increase available funds; untrusted totals cannot replace an existing
  server archive. Unsigned requests, ordinary players and spoofed IDs cannot
  approve or access another profile's recovery.
- Original currency, principal, dates and claimed yield survive review.
  Previously paid principal and income are not paid again; unknown yield
  rates remain unresolved rather than being silently treated as zero.
- Existing verified farms remain byte-for-value unchanged by recovery.
  Later claims match an unrecovered control database, including exact reward
  events and returned principal.
- Repeated submissions/approvals, fresh application instances and simultaneous
  owner retries do not duplicate money or farm settlements.
- A SQLite trigger fails the second player's update after earlier batch
  writes; every account, ledger, request and settlement row must roll back.
- A subprocess is killed with `os._exit` after a real recovery ledger INSERT,
  before COMMIT. Reopening the database must show no partial credits. A retry
  after a committed-but-discarded success response is also idempotent.
- Interrupting each of the four archive localStorage writes preserves original
  strings and the other profile on restart. An empty/new live farm list cannot
  overwrite already archived farms. Real wallet refresh keeps available money
  separate from unreviewed history.
- Malformed/duplicate farms, verified-farm collisions, invalid dates, expired
  reviews, changed pending sums and tampered digests fail without crediting.

## Safety and limits

Only automatically generated files under temporary directories are opened
for recovery. They and approval backups are deleted after the test. The runner
removes credentials from child environments and blocks Python socket
connections; RPC/HTTP entry points fail explicitly. JavaScript uses only
in-memory storage and explicit fetch doubles. No fixtures are actual player
exports and no existing player database is consulted.

Passing these checks proves the stated accounting invariants for synthetic
cases, **not** that every historical record has been discovered. Farms may
still exist on another device. This does not approve any real amount,
authorize a real recovery, prove a full-site release, test damaged archives
per player, or validate Phantom, withdrawals or production storage/backups.
