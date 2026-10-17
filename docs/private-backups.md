# Private off-disk accounting backups

This change **does not deploy, create a bucket, migrate the database, restore a
disk, or change balances**. Backups remain disabled until an operator configures
storage and explicitly enables them on Render after reviewing/merging the PR.
SQLite remains the source of truth; Replit PostgreSQL is not involved.

## What is captured

- The exact `DATA_DIR/wallet_ledger.sqlite3` used by `register_wallet_routes` in
  `wallet_service.py`. `bot.py` passes the same `DATA_DIR` to the backup worker.
  This includes accounts, ledger entries, active farms, historical recovery
  submissions and farm-return receipts, purchases, and withdrawal approvals.
- Committed WAL pages are captured using SQLite's **online backup API**, opened
  read-only. No raw database/WAL copying, checkpoint, INSERT, UPDATE, or DELETE
  is performed against the live database.
- `users.json` and `leaderboard_stats.json` from that directory, if present,
  as **separate encrypted objects**. Their absence is recorded in the encrypted
  manifest, not silently replaced with empty data.
- Additional historical JSON files only when explicitly named by an operator
  in `BACKUP_HISTORICAL_JSON_PATHS`. A missing configured historical file is an
  error. No disk crawling or automatic import of old balances occurs.

Historical files are independent point-in-time captures, **not an atomic
transaction with SQLite**. Browser localStorage cannot be backed up by this
server. No inference is made that missing server JSON represents lost funds.

## Configure a private destination (before enabling)

Use a **dedicated private S3-compatible bucket outside Render**, e.g. Amazon S3
or Cloudflare R2. Do not use GitHub, Pages, a public bucket, a Replit artifact,
a Vite directory, or another folder on the same Render disk as the destination.
No external bucket or credentials are provisioned by this PR.

1. Disable public ACLs and public policies. On AWS, enable all Block Public
   Access settings. Do not enable public website hosting or an R2 public domain.
   Use HTTPS only. Enable bucket default encryption; optional versioning and
   provider-side lifecycle rules are additional safeguards.
2. Create a credential restricted to this bucket and the dedicated prefix.
   Required operations: list the prefix, put/get/delete objects, and abort
   multipart uploads. No account-wide administrator permission is needed.
   Allow listing multipart uploads only if your provider requires it.
3. Set up a bucket lifecycle rule to abort incomplete multipart uploads after
   one day. If bucket versioning is on, define a **separate noncurrent-version
   expiration rule**; application rotation deletes current objects, not old
   versions. Confirm costs and retention with the operator.
4. Generate an independent random 32-byte encryption key, encoded as base64,
   using a trusted local secret manager. Keep a second copy of the key outside
   Render and outside the bucket (e.g. in the owner's password manager).
   Losing it makes backups unreadable. Never reuse treasury, Telegram, or
   recovery-transfer secrets. Never paste it into chat or commit it.
5. Enter credentials and encryption key through the **Render service's secret
   environment settings**, not source files or chat. Replit project secrets
   do not automatically configure this external Render service.

Required environment settings:

| Name | Purpose |
|---|---|
| `BACKUP_ENABLED` | `true` to enable; default `false` |
| `BACKUP_S3_BUCKET` | Dedicated private bucket name |
| `BACKUP_S3_PREFIX` | Dedicated namespace; default `zerolife-ledger`; letters, digits, `_`, `-` |
| `AWS_ACCESS_KEY_ID` | Scoped storage credential (secret) |
| `AWS_SECRET_ACCESS_KEY` | Scoped storage credential (secret) |
| `AWS_DEFAULT_REGION` | Region required by your provider; commonly `auto` for R2 |
| `BACKUP_S3_ENDPOINT_URL` | HTTPS S3 API endpoint for non-AWS providers; omit for AWS |
| `BACKUP_ENCRYPTION_KEY` | Dedicated base64-encoded 32-byte key (secret) |
| `BOT_TOKEN` | Existing bot credential used to notify the administrator |

Optional: `AWS_SESSION_TOKEN` for temporary provider credentials, refreshed
outside this worker. Native AWS role credentials also work through boto3's
normal credential chain. Never put credential values into endpoint URLs.

Policy settings:

| Name | Default | Allowed |
|---|---:|---|
| `BACKUP_INTERVAL_SECONDS` | 3600 | 60–86400 |
| `BACKUP_SNAPSHOT_TIMEOUT_SECONDS` | 120 | 1–600 |
| `BACKUP_KEEP_COUNT` | 48 | 2–10000 |
| `BACKUP_KEEP_DAYS` | 30 | 1–3650 |
| `BACKUP_HISTORICAL_JSON_PATHS` | `[]` | JSON array of absolute `.json` file paths, max 100 |

For historical files first **inspect actual file locations on Render**. A disk
at `/var/data` alone does not prove that a particular old file survived.
Do not configure guesses. Extra configured JSON paths are required to exist.

Example AWS policy (replace both placeholders, never insert secrets):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::YOUR_PRIVATE_BUCKET",
      "Condition": {"StringLike": {"s3:prefix": ["zerolife-ledger/*"]}}
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::YOUR_PRIVATE_BUCKET/zerolife-ledger/*"
    }
  ]
}
```

IAM policy syntax is AWS-specific; use equivalent bucket-scoped API permissions
for other providers. A bucket using customer-managed KMS additionally needs
appropriate KMS key permissions. Separate workloads should use separate prefixes
and credentials; do not share this worker's namespace.

## Automatic operation and evidence

The worker starts at bot import/startup, also under a WSGI server. Its first run
is immediate; later runs use the configured interval after the preceding run.
Only one worker per host holds a filesystem lock. Standby workers retry the lock.
This is not a distributed scheduler: **do not run multiple Render instances
writing to the same prefix**.

Every run:

1. Creates temporary files only under a mode-0700 directory in `/tmp`, outside
   the application/preview root and outside `/var/data`. Files are mode 0600.
2. Takes an online snapshot, checks `PRAGMA integrity_check`, required wallet,
   farming, recovery, payment and mobile tables, and `PRAGMA foreign_key_check`.
3. Captures and parses historical JSON; malformed, changing or symlinked files
   fail the run rather than producing a misleading successful backup.
4. Encrypts every file using libsodium secretstream XChaCha20-Poly1305 with a
   fresh random header, authenticated chunks and an authenticated final marker.
   Plaintext is never uploaded. The format is versioned `ZRL-BACKUP-1`.
5. Decrypts locally and compares SHA-256; checks the decrypted SQLite copy again.
6. Uploads ciphertext, downloads it, and compares SHA-256. A successful upload
   response alone is not accepted as verification.
7. Uploads the **encrypted manifest last**, also with readback verification.
   Sets without `manifest.json.enc` are incomplete, never restore candidates.
8. Rotates only objects with this worker's strict generated set/object names.
   Keeps at least the newest N complete sets **and** all sets younger than D
   days. Removes old incomplete sets after D days. It never prunes after a
   failed upload/verification. During deletion, the manifest is removed first.
9. Cleans temporary files on success or error. After a process crash, the next
   lock-holding worker removes leftover `run-*` staging directories. Ephemeral
   `/tmp` is not a backup destination.

The encrypted manifest includes plaintext and ciphertext checksums, sizes,
table inventory, timestamps, optional missing JSON and source paths of
historical files. It contains no encryption key.

## Failure signals and monitoring

- Failures log only their exception **class**, not SDK exception text,
  tracebacks, private paths or credential-bearing URLs.
- With backups enabled, a working Telegram administrator notification channel
  is required. Failure messages go to the existing `ADMIN_TELEGRAM_ID` at most
  once per hour. Alert delivery failures are also logged, without exception text.
- Public `GET /health/backups` returns only `{"status":"ok"}` with HTTP 200
  after verification and retention succeed. Other states return HTTP 503:
  `disabled`, `pending`, `error`, `stale`. No account, storage or file details
  are exposed. Responses have `Cache-Control: no-store`.
- A successful backup older than twice the interval plus ten minutes is stale.
  A failed new run reports error even when a previous copy exists.
- **Configure an independent external monitor** to alert the owner on non-200
  or service downtime. Telegram alone cannot detect a dead bot, revoked bot
  credential, host outage or a hung backup worker.

Review log access and turn off verbose HTTP SDK logging in production. This
worker does not enable SDK debugging. Existing unrelated bot logging behavior
is not changed by this PR.

## Activation acceptance checklist (operator, after PR approval)

- Confirm live `DATA_DIR`, the database and historical paths without printing
  customer records. Do not perform a Render disk Restore.
- Configure the private bucket, scoped credentials, external key copy and
  external monitor. Merge/deploy only with the owner's approval.
- Enable backups and wait for `/health/backups` to return 200.
- Confirm a complete encrypted set exists in the private bucket and that an
  unauthenticated object request is denied. No data is shared in chat or GitHub.
- On a separate trusted host, download the encrypted manifest and its listed
  objects to a restricted directory outside web roots, decrypt offline with
  `backup_archive.decrypt_file`, compare the manifest SHA-256 values, and run
  `validate_sqlite` on the decrypted copy. Never use the live DB as destination.
- Exercise a credential failure **in staging** and confirm the admin alert,
  HTTP 503 and retention of older complete sets, then restore staging settings.

The code checks run only on synthetic data with a fake object store. They do
not prove a real bucket is private or that Render credentials/notifications
work. Production protection is **not active** until the above checklist passes.
Do not announce that accounts are protected based only on a merged PR.

## Offline inspection, not restoration

`decrypt_file(source, destination, key)` refuses to overwrite an existing file,
authenticates every frame and rejects truncation, mutation, wrong keys and
trailing bytes. Obtain `key` from secure environment injection on a trusted
host; do not pass keys as CLI arguments or print them.

Run integrity, required-table and checksum checks against **copies** before
considering a separately approved recovery plan. This PR intentionally adds
no restore endpoint, balance import, disk Restore action or automatic rollback.
Key rotation also requires a separate plan: keep old keys for existing sets
until their retention expires and verified new-key sets exist.

## Tests

```sh
python -m unittest discover -s tests -p 'test_wallet_backup.py' -v
```

Tests use synthetic SQLite WAL data and JSON in private `/tmp` directories,
not production exports. CI uploads no databases or backup artifacts.
