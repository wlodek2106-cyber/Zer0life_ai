"""Durable, exactly-once Boots settlement from the pool to the merchant.

An RPC acknowledgement is not a completed purchase. The signed transaction is
saved before broadcast; Boots activate only after finalized on-chain success.
"""

import base64
import os
import re
from decimal import Decimal, InvalidOperation

MERCHANT_SOL_WALLET = "57S7TryAMhxRq5sMSyZxTkyCmzT8tPFKfXjzZwvmv5db"
PACKAGES = {
    "0.2": (1500, 7 * 86400),
    "0.5": (3000, 0),
    "1": (6000, 0),
    "5": (13000, 0),
    "10": (30000, 0),
}


class MerchantError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class MerchantPayments:
    def __init__(self, db_path, connect, ensure_account, rpc_client, now, pool, logger):
        self.path, self.connect, self.ensure_account = db_path, connect, ensure_account
        self.rpc_client, self.now, self.pool, self.logger = rpc_client, now, pool, logger
        with self.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS wallet_merchant_purchases (
                    event_id TEXT PRIMARY KEY,
                    telegram_id TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    lamports INTEGER NOT NULL,
                    booster_rate INTEGER NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    tx_signature TEXT UNIQUE,
                    signed_transaction TEXT,
                    last_valid_block_height INTEGER,
                    last_broadcast_at INTEGER NOT NULL DEFAULT 0,
                    failure TEXT,
                    created_at INTEGER NOT NULL,
                    settled_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS wallet_merchant_owner_idx
                    ON wallet_merchant_purchases(telegram_id, status);
            """)

    def pending(self, telegram_id):
        with self.connect(self.path) as db:
            return [dict(row) for row in db.execute(
                """SELECT event_id AS purchaseId, tx_signature AS txHash,
                          recipient, status
                   FROM wallet_merchant_purchases
                   WHERE telegram_id=? AND status='pending' ORDER BY created_at""",
                (str(telegram_id),),
            )]

    def _get(self, event_id, telegram_id):
        with self.connect(self.path) as db:
            row = db.execute(
                "SELECT * FROM wallet_merchant_purchases WHERE event_id=? AND telegram_id=?",
                (event_id, str(telegram_id)),
            ).fetchone()
        if not row:
            raise MerchantError("Purchase not found", 404)
        return row

    def receipt(self, telegram_id):
        with self.connect(self.path) as db:
            row = db.execute(
                """SELECT event_id AS purchaseId,status,tx_signature AS txHash,
                          recipient,lamports,created_at AS createdAt
                   FROM wallet_merchant_purchases WHERE telegram_id=?
                   ORDER BY created_at DESC,rowid DESC LIMIT 1""", (str(telegram_id),)
            ).fetchone()
        return dict(row) if row else None

    def _result(self, event_id, telegram_id):
        row = self._get(event_id, telegram_id)
        with self.connect(self.path) as db:
            account = db.execute(
                "SELECT sol_lamports,booster_rate,booster_expires FROM wallet_accounts WHERE telegram_id=?",
                (str(telegram_id),),
            ).fetchone()
        expires = account["booster_expires"]
        return {
            "success": True, "purchaseId": event_id, "status": row["status"],
            "recipient": row["recipient"], "txHash": row["tx_signature"],
            "message": row["failure"] or (
                "Merchant payment finalized; Boots activated"
                if row["status"] == "confirmed" else "Waiting for finalized merchant payment"
            ),
            "gameSolBalance": account["sol_lamports"] / 1_000_000_000,
            "boosterRate": account["booster_rate"] if not expires or expires > self.now() else 300,
            "boosterExpires": expires * 1000,
            "pendingPurchases": self.pending(telegram_id),
            "lastMerchantPurchase": self.receipt(telegram_id),
        }

    def purchase(self, telegram_id, event_id, cost):
        telegram_id = str(telegram_id)
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,120}", event_id):
            raise MerchantError("Invalid purchase ID")
        try:
            if len(str(cost)) > 32:
                raise ValueError("Invalid package")
            amount = Decimal(str(cost))
            package = PACKAGES.get(format(amount.normalize(), "f")) if amount.is_finite() and 0 < amount <= 10 else None
        except (InvalidOperation, ValueError):
            package = None
        if not package:
            raise MerchantError("Unknown booster package")
        lamports = int(amount * 1_000_000_000)
        rate, duration = package
        with self.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM wallet_merchant_purchases WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing:
                if existing["telegram_id"] != telegram_id:
                    raise MerchantError("Purchase belongs to another account", 403)
                if existing["lamports"] != lamports:
                    raise MerchantError("Purchase ID cannot be reused for another package", 409)
            else:
                if db.execute("SELECT 1 FROM wallet_spend_events WHERE event_id=?", (event_id,)).fetchone():
                    raise MerchantError("This spend request was already processed", 409)
                if db.execute(
                    "SELECT 1 FROM wallet_merchant_purchases WHERE telegram_id=? AND status='pending'",
                    (telegram_id,),
                ).fetchone():
                    raise MerchantError("A Boots payment is pending; check it before buying again", 409)
                self.ensure_account(db, telegram_id)
                changed = db.execute(
                    "UPDATE wallet_accounts SET sol_lamports=sol_lamports-? WHERE telegram_id=? AND sol_lamports>=?",
                    (lamports, telegram_id, lamports),
                ).rowcount
                if changed != 1:
                    raise MerchantError("Insufficient verified SOL game balance", 402)
                db.execute(
                    "INSERT INTO wallet_spend_events(event_id,telegram_id,purpose,created_at) VALUES(?,?,?,?)",
                    (event_id, telegram_id, "booster", self.now()),
                )
                db.execute(
                    """INSERT INTO wallet_merchant_purchases
                       (event_id,telegram_id,recipient,lamports,booster_rate,duration_seconds,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (event_id, telegram_id, MERCHANT_SOL_WALLET, lamports, rate, duration, self.now()),
                )
            db.commit()
        return self.status(telegram_id, event_id)

    def _prepare(self, row):
        # Read the existing treasury key only inside the server process.
        from base58 import b58decode
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.instruction import Instruction
        from solders.system_program import transfer, TransferParams
        from solders.transaction import Transaction

        private_key = os.getenv("PRIVATE_KEY")
        if not private_key:
            raise MerchantError("Merchant payment signing is not configured; SOL restored", 503)
        treasury = Keypair.from_bytes(b58decode(private_key))
        if str(treasury.pubkey()) != self.pool:
            raise MerchantError("Treasury signer does not match the pool; SOL restored", 503)
        if row["recipient"] != MERCHANT_SOL_WALLET:
            raise MerchantError("Merchant destination is not valid; SOL restored", 503)
        client = self.rpc_client()
        latest = client.get_latest_blockhash(commitment="finalized").value
        tx = Transaction.new_signed_with_payer(
            instructions=[
                transfer(TransferParams(
                    from_pubkey=treasury.pubkey(),
                    to_pubkey=Pubkey.from_string(MERCHANT_SOL_WALLET),
                    lamports=row["lamports"],
                )),
                Instruction(
                    Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"),
                    ("Zer0Life Boots:" + row["event_id"]).encode(), [],
                ),
            ],
            payer=treasury.pubkey(), signing_keypairs=[treasury],
            recent_blockhash=latest.blockhash,
        )
        fee = client.get_fee_for_message(tx.message, commitment="finalized").value
        if fee is None:
            raise MerchantError("Network fee is unavailable; SOL restored", 503)
        # Sale proceeds must not consume the remaining SOL owed to players.
        # Pending transactions are counted conservatively until reconciled.
        with self.connect(self.path) as db:
            available = db.execute("SELECT COALESCE(SUM(sol_lamports),0) FROM wallet_accounts").fetchone()[0]
            farms = db.execute(
                "SELECT COALESCE(SUM(principal_lamports),0) FROM wallet_farms WHERE asset='sol' AND status='active'"
            ).fetchone()[0]
            withdrawals = db.execute(
                """SELECT COALESCE(SUM(amount_units),0) FROM wallet_withdrawals
                   WHERE asset='SOL' AND status IN ('processing','submitted')"""
            ).fetchone()[0]
            purchases = db.execute(
                "SELECT COALESCE(SUM(lamports),0) FROM wallet_merchant_purchases WHERE status='pending'"
            ).fetchone()[0]
        balance = int(client.get_balance(treasury.pubkey(), commitment="finalized").value)
        if balance < available + farms + withdrawals + purchases + int(fee):
            raise MerchantError("Pool SOL reserves or network fee are insufficient; SOL restored", 503)
        # A racing status request may also prepare a transaction. Only the
        # winner of this compare-and-set may broadcast, and only saved bytes.
        with self.connect(self.path) as db:
            db.execute(
                """UPDATE wallet_merchant_purchases SET tx_signature=?,signed_transaction=?,
                          last_valid_block_height=?
                   WHERE event_id=? AND status='pending' AND tx_signature IS NULL""",
                (str(tx.signatures[0]), base64.b64encode(bytes(tx)).decode(),
                 latest.last_valid_block_height, row["event_id"]),
            )

    def _settle(self, row, failure=None, unsigned_only=False):
        with self.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM wallet_merchant_purchases WHERE event_id=?", (row["event_id"],)
            ).fetchone()
            if current["status"] != "pending" or (unsigned_only and current["tx_signature"]):
                db.rollback()
                return
            if failure:
                db.execute(
                    "UPDATE wallet_accounts SET sol_lamports=sol_lamports+? WHERE telegram_id=?",
                    (current["lamports"], current["telegram_id"]),
                )
            else:
                expires = self.now() + current["duration_seconds"] if current["duration_seconds"] else 0
                db.execute(
                    "UPDATE wallet_accounts SET booster_rate=?,booster_expires=? WHERE telegram_id=?",
                    (current["booster_rate"], expires, current["telegram_id"]),
                )
            db.execute(
                """UPDATE wallet_merchant_purchases SET status=?,failure=?,settled_at=?,
                          signed_transaction=NULL WHERE event_id=?""",
                ("failed" if failure else "confirmed", failure, self.now(), row["event_id"]),
            )
            db.commit()

    def status(self, telegram_id, event_id):
        row = self._get(event_id, telegram_id)
        if row["status"] != "pending":
            return self._result(event_id, telegram_id)
        if not row["tx_signature"]:
            try:
                self._prepare(row)
            except Exception as exc:
                message = str(exc) if isinstance(exc, MerchantError) else "Payment preparation failed; SOL restored"
                self.logger.warning("Merchant preparation failed (%s)", type(exc).__name__)
                self._settle(row, message, unsigned_only=True)
            row = self._get(event_id, telegram_id)
        if row["status"] == "pending" and row["tx_signature"]:
            try:
                self._check_chain(row)
            except Exception as exc:
                # Uncertain broadcast/confirmation is NEVER a reason to refund
                # or build a new transaction. The saved bytes remain retryable.
                self.logger.warning("Merchant confirmation pending (%s)", type(exc).__name__)
        return self._result(event_id, telegram_id)

    def _check_chain(self, row):
        from solders.signature import Signature
        from solders.transaction_status import TransactionConfirmationStatus
        client = self.rpc_client()
        signature = Signature.from_string(row["tx_signature"])

        def chain_status():
            return client.get_signature_statuses([signature], search_transaction_history=True).value[0]

        status = chain_status()
        if status and status.confirmation_status == TransactionConfirmationStatus.Finalized:
            self._settle(row, "Merchant transaction failed; SOL restored" if status.err is not None else None)
            return
        height = int(client.get_block_height(commitment="finalized").value)
        if not status and height > row["last_valid_block_height"]:
            # Recheck history after observing expiry at finalized commitment.
            if chain_status() is None:
                self._settle(row, "Merchant transaction expired without confirmation; SOL restored")
            return
        if height <= row["last_valid_block_height"] and row["signed_transaction"]:
            with self.connect(self.path) as db:
                claimed = db.execute(
                    """UPDATE wallet_merchant_purchases SET last_broadcast_at=?
                       WHERE event_id=? AND status='pending' AND last_broadcast_at<=?""",
                    (self.now(), row["event_id"], self.now() - 5),
                ).rowcount
            if claimed:
                submitted = str(client.send_raw_transaction(base64.b64decode(row["signed_transaction"])).value)
                if submitted != row["tx_signature"]:
                    raise RuntimeError("Unexpected merchant transaction signature")
