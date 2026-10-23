"""Direct Solana Pay checkout for Boots packages.

Invoices are bound to the authenticated Telegram account and a unique Solana
reference. Boots activate only after a finalized, exact-value SOL transfer to
the fixed Merchant address has been verified on-chain.
"""

import os
import re
import secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import base58
import requests

from merchant_payments import MERCHANT_SOL_WALLET, PACKAGES

LAMPORTS_PER_SOL = 1_000_000_000
_EVENT_ID_RE = re.compile(r"[A-Za-z0-9:_-]{1,120}\Z")
_SIGNATURE_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{64,90}\Z")


class BootsPayError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _verified_boots_lamports(result, signature, reference, expected_lamports):
    if not isinstance(result, dict):
        raise ValueError("Solana has not finalized this transaction yet")

    meta = result.get("meta")
    if not isinstance(meta, dict) or "err" not in meta:
        raise ValueError("Solana has not finalized this transaction yet")
    if meta.get("err") is not None:
        raise ValueError("This Solana transaction failed; Boots were not activated")

    transaction = result.get("transaction") or {}
    if signature not in (transaction.get("signatures") or []):
        raise ValueError("Solana RPC returned a different transaction signature")

    message = transaction.get("message") or {}
    account_keys = set()
    signers = set()
    for key in message.get("accountKeys") or []:
        if isinstance(key, dict):
            public_key = key.get("pubkey")
            if isinstance(public_key, str):
                account_keys.add(public_key)
                if key.get("signer") is True:
                    signers.add(public_key)
        elif isinstance(key, str):
            account_keys.add(key)

    loaded = meta.get("loadedAddresses") or {}
    for group in (loaded.get("writable") or [], loaded.get("readonly") or []):
        for key in group:
            public_key = key.get("pubkey") if isinstance(key, dict) else key
            if isinstance(public_key, str):
                account_keys.add(public_key)

    if reference not in account_keys:
        raise ValueError("This transaction is missing the unique Boots payment reference")

    paid_lamports = 0
    for instruction in message.get("instructions") or []:
        parsed = instruction.get("parsed") or {}
        info = parsed.get("info") or {}
        if instruction.get("program") != "system" or parsed.get("type") != "transfer":
            continue
        if info.get("destination") != MERCHANT_SOL_WALLET:
            continue
        if info.get("source") not in signers:
            raise ValueError("The SOL payment was not signed by its sending wallet")
        try:
            lamports = int(info.get("lamports", 0))
        except (TypeError, ValueError):
            continue
        if lamports > 0:
            paid_lamports += lamports

    if paid_lamports != expected_lamports:
        raise ValueError("The finalized transaction does not match the exact Boots price")
    return paid_lamports


class BootsSolanaPay:
    def __init__(self, db_path, connect, ensure_account, now, logger):
        self.path = db_path
        self.connect = connect
        self.ensure_account = ensure_account
        self.now = now
        self.logger = logger
        with self.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS wallet_boots_solana_payments (
                    purchase_id TEXT PRIMARY KEY,
                    telegram_id TEXT NOT NULL,
                    reference TEXT NOT NULL UNIQUE,
                    recipient TEXT NOT NULL,
                    lamports INTEGER NOT NULL,
                    booster_rate INTEGER NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    tx_signature TEXT UNIQUE,
                    created_at INTEGER NOT NULL,
                    settled_at INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS wallet_boots_pending_owner_idx
                    ON wallet_boots_solana_payments(telegram_id)
                    WHERE status='pending';
                CREATE INDEX IF NOT EXISTS wallet_boots_owner_created_idx
                    ON wallet_boots_solana_payments(telegram_id, created_at);
            """)

    @staticmethod
    def _package(cost):
        if isinstance(cost, bool) or len(str(cost)) > 32:
            raise BootsPayError("Choose a valid Boots package")
        try:
            amount = Decimal(str(cost))
        except (InvalidOperation, TypeError, ValueError):
            raise BootsPayError("Choose a valid Boots package")
        if not amount.is_finite() or amount <= 0 or amount > 10:
            raise BootsPayError("Choose a valid Boots package")
        package = PACKAGES.get(format(amount.normalize(), "f"))
        if not package:
            raise BootsPayError("This Boots package is not available")
        return int(amount * LAMPORTS_PER_SOL), package

    def _new_reference(self, db):
        for _ in range(8):
            reference = base58.b58encode(secrets.token_bytes(32)).decode("ascii")
            collision = db.execute(
                """SELECT 1 FROM wallet_boots_solana_payments WHERE reference=?
                   UNION ALL
                   SELECT 1 FROM wallet_deposit_references WHERE reference=?
                   LIMIT 1""",
                (reference, reference),
            ).fetchone()
            if collision is None:
                return reference
        raise RuntimeError("Could not allocate a unique Solana Pay reference")

    def _serialize(self, row):
        amount = Decimal(int(row["lamports"])) / Decimal(LAMPORTS_PER_SOL)
        amount_text = format(amount, "f")
        pay_uri = None
        if row["status"] == "pending":
            pay_uri = f"solana:{row['recipient']}?" + urlencode({
                "amount": amount_text,
                "reference": row["reference"],
                "label": "Zer0Life Run",
                "message": f"Boots {row['booster_rate']} ZRL per km",
            })
        with self.connect(self.path) as db:
            account = db.execute(
                "SELECT booster_rate,booster_expires FROM wallet_accounts WHERE telegram_id=?",
                (str(row["telegram_id"]),),
            ).fetchone()
        expires = int(account["booster_expires"] or 0)
        return {
            "success": True,
            "purchaseId": row["purchase_id"],
            "status": row["status"],
            "recipient": row["recipient"],
            "reference": row["reference"],
            "amountLamports": int(row["lamports"]),
            "amountSol": float(amount),
            "payUri": pay_uri,
            "txHash": row["tx_signature"],
            "boosterRate": int(account["booster_rate"]),
            "boosterExpires": expires * 1000,
            "createdAt": int(row["created_at"]) * 1000,
            "message": (
                "Boots payment finalized and activated"
                if row["status"] == "confirmed"
                else "Pay the exact amount using the QR or Solana Pay link, then verify the finalized Tx"
            ),
        }

    def pending(self, telegram_id):
        with self.connect(self.path) as db:
            rows = db.execute(
                """SELECT * FROM wallet_boots_solana_payments
                   WHERE telegram_id=? AND status='pending'
                   ORDER BY created_at,purchase_id""",
                (str(telegram_id),),
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def prepare(self, telegram_id, purchase_id, cost):
        telegram_id = str(telegram_id)
        purchase_id = str(purchase_id or "").strip()
        if not _EVENT_ID_RE.fullmatch(purchase_id):
            raise BootsPayError("A valid Boots purchase ID is required")
        lamports, (booster_rate, duration_seconds) = self._package(cost)

        with self.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM wallet_boots_solana_payments WHERE purchase_id=?",
                (purchase_id,),
            ).fetchone()
            if existing:
                if existing["telegram_id"] != telegram_id:
                    raise BootsPayError("This purchase belongs to another Telegram account", 403)
                if (int(existing["lamports"]), int(existing["booster_rate"]),
                        int(existing["duration_seconds"])) != (
                        lamports, booster_rate, duration_seconds):
                    raise BootsPayError("A purchase ID cannot be reused for another Boots package", 409)
                db.commit()
                return self._serialize(existing)

            if db.execute(
                "SELECT 1 FROM wallet_spend_events WHERE event_id=?", (purchase_id,)
            ).fetchone():
                raise BootsPayError("This purchase ID has already been used", 409)

            pending = db.execute(
                """SELECT * FROM wallet_boots_solana_payments
                   WHERE telegram_id=? AND status='pending' LIMIT 1""",
                (telegram_id,),
            ).fetchone()
            if pending:
                raise BootsPayError(
                    "A Boots payment is already waiting. Reopen its QR and verify its Tx before starting another.",
                    409,
                )

            self.ensure_account(db, telegram_id)
            reference = self._new_reference(db)
            db.execute(
                """INSERT INTO wallet_boots_solana_payments
                   (purchase_id,telegram_id,reference,recipient,lamports,booster_rate,
                    duration_seconds,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (purchase_id, telegram_id, reference, MERCHANT_SOL_WALLET, lamports,
                 booster_rate, duration_seconds, self.now()),
            )
            db.commit()
            row = db.execute(
                "SELECT * FROM wallet_boots_solana_payments WHERE purchase_id=?",
                (purchase_id,),
            ).fetchone()
        return self._serialize(row)

    def _signature_used(self, db, signature, purchase_id):
        if db.execute(
            """SELECT 1 FROM wallet_boots_solana_payments
               WHERE tx_signature=? AND purchase_id<>?""",
            (signature, purchase_id),
        ).fetchone():
            return True
        for table, column in (
            ("wallet_deposits", "signature"),
            ("wallet_merchant_purchases", "tx_signature"),
        ):
            if db.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (signature,)).fetchone():
                return True
        return False

    def verify(self, telegram_id, purchase_id, signature):
        telegram_id = str(telegram_id)
        purchase_id = str(purchase_id or "").strip()
        signature = str(signature or "").strip()
        if not _EVENT_ID_RE.fullmatch(purchase_id):
            raise BootsPayError("A valid Boots purchase ID is required")
        if not _SIGNATURE_RE.fullmatch(signature):
            raise BootsPayError("Enter a valid Solana transaction signature")
        try:
            if len(base58.b58decode(signature)) != 64:
                raise BootsPayError("Enter a valid Solana transaction signature")
        except ValueError:
            raise BootsPayError("Enter a valid Solana transaction signature")

        with self.connect(self.path) as db:
            row = db.execute(
                """SELECT * FROM wallet_boots_solana_payments
                   WHERE purchase_id=? AND telegram_id=?""",
                (purchase_id, telegram_id),
            ).fetchone()
            if row is None:
                raise BootsPayError("Boots purchase not found", 404)
            if row["status"] == "confirmed":
                if row["tx_signature"] == signature:
                    return self._serialize(row)
                raise BootsPayError("This Boots invoice has already been paid", 409)
            if row["status"] != "pending":
                raise BootsPayError("This Boots invoice is no longer open", 409)
            if self._signature_used(db, signature, purchase_id):
                raise BootsPayError("This transaction signature has already been used", 409)

        rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        response = requests.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "commitment": "finalized",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            },
            timeout=20,
        )
        response.raise_for_status()
        rpc_result = response.json()
        if rpc_result.get("error"):
            raise BootsPayError("Solana RPC could not verify this transaction; try again later", 503)
        transaction = rpc_result.get("result")
        if transaction is None:
            return {
                **self._serialize(row),
                "status": "pending",
                "message": "Transaction not finalized yet. Wait, then verify the same Tx again; do not pay again.",
            }
        meta = transaction.get("meta") if isinstance(transaction, dict) else None
        if not isinstance(meta, dict) or "err" not in meta:
            return {
                **self._serialize(row),
                "status": "pending",
                "message": "Transaction is not finalized yet. Wait, then verify the same Tx again; do not pay again.",
            }
        try:
            _verified_boots_lamports(
                transaction, signature, row["reference"], int(row["lamports"])
            )
        except ValueError as exc:
            raise BootsPayError(str(exc), 400)

        with self.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                """SELECT * FROM wallet_boots_solana_payments
                   WHERE purchase_id=? AND telegram_id=?""",
                (purchase_id, telegram_id),
            ).fetchone()
            if current is None:
                db.rollback()
                raise BootsPayError("Boots purchase not found", 404)
            if current["status"] == "confirmed":
                if current["tx_signature"] == signature:
                    db.commit()
                    return self._serialize(current)
                db.rollback()
                raise BootsPayError("This Boots invoice has already been paid", 409)
            if current["status"] != "pending":
                db.rollback()
                raise BootsPayError("This Boots invoice is no longer open", 409)
            if self._signature_used(db, signature, purchase_id):
                db.rollback()
                raise BootsPayError("This transaction signature has already been used", 409)

            self.ensure_account(db, telegram_id)
            expires = self.now() + int(current["duration_seconds"]) if current["duration_seconds"] else 0
            db.execute(
                "UPDATE wallet_accounts SET booster_rate=?,booster_expires=? WHERE telegram_id=?",
                (int(current["booster_rate"]), expires, telegram_id),
            )
            updated = db.execute(
                """UPDATE wallet_boots_solana_payments
                   SET status='confirmed',tx_signature=?,settled_at=?
                   WHERE purchase_id=? AND telegram_id=? AND status='pending'""",
                (signature, self.now(), purchase_id, telegram_id),
            )
            if updated.rowcount != 1:
                db.rollback()
                raise BootsPayError("This Boots payment was already processed", 409)
            db.commit()
            confirmed = db.execute(
                "SELECT * FROM wallet_boots_solana_payments WHERE purchase_id=?",
                (purchase_id,),
            ).fetchone()
        return self._serialize(confirmed)
