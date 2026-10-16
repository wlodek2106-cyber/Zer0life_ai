"""Owner-scoped Telegram confirmations for treasury withdrawals; never stores OTPs."""
import hashlib
import hmac
import os
import re
import secrets
import time
from decimal import Decimal, InvalidOperation

import requests


class ApprovalError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class WithdrawalApprovals:
    def __init__(self, path, connect, challenge, balance, zrl_scale, daily_cap, pool):
        self.path, self.connect, self.challenge = path, connect, challenge
        self.balance, self.scale, self.cap, self.pool = balance, zrl_scale, daily_cap, pool
        with connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS wallet_withdrawal_approvals (
                nonce TEXT PRIMARY KEY REFERENCES wallet_challenges(nonce),
                request_id TEXT UNIQUE NOT NULL, telegram_id TEXT NOT NULL,
                code_hash TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                delivery TEXT NOT NULL DEFAULT 'sending', created_at INTEGER NOT NULL
            )""")

    def _digest(self, nonce, code):
        token = os.getenv("BOT_TOKEN", "")
        if not token:
            raise ApprovalError("Telegram verification is temporarily unavailable.", 503)
        return hmac.new(token.encode(), f"withdraw:{nonce}:{code}".encode(), hashlib.sha256).hexdigest()

    def pending(self, user):
        with self.connect(self.path) as db:
            rows = db.execute("""SELECT c.* FROM wallet_withdrawal_approvals a
                JOIN wallet_challenges c ON c.nonce=a.nonce
                WHERE a.telegram_id=? AND a.delivery='sent' AND a.attempts<5
                AND c.used=0 AND c.expires_at>? ORDER BY a.created_at DESC""",
                (user, int(time.time()))).fetchall()
        return [self._public(row) for row in rows]

    def _public(self, row):
        asset = "SOL" if row["purpose"] == "withdraw_sol" else "ZRL"
        return {"nonce": row["nonce"], "address": row["address"], "asset": asset,
                "amount": row["amount_units"] / (10**9 if asset == "SOL" else self.scale),
                "expiresAt": row["expires_at"]}

    def request(self, user, data):
        from solders.pubkey import Pubkey
        asset, address = data.get("asset"), str(data.get("address", "")).strip()
        request_id = str(data.get("requestId", ""))
        if asset not in ("SOL", "ZRL") or not re.fullmatch(r"[A-Za-z0-9_-]{12,100}", request_id):
            raise ApprovalError("Invalid withdrawal request.")
        try:
            if address == self.pool or not Pubkey.from_string(address).is_on_curve():
                raise ValueError()
        except ValueError:
            raise ApprovalError("Enter your personal Solana wallet address, not a pool or token account address.")
        try:
            amount = Decimal(str(data.get("amount", "")))
            units = amount * (10**9 if asset == "SOL" else self.scale)
            if not amount.is_finite() or not 0 < units <= 9_000_000_000_000_000 or units != units.to_integral_value():
                raise ValueError()
            units = int(units)
        except (InvalidOperation, ValueError, OverflowError):
            raise ApprovalError("Enter a valid positive withdrawal amount.")
        now = int(time.time())
        with self.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("""SELECT a.*, c.address,c.purpose,c.amount_units,c.expires_at,c.used
                FROM wallet_withdrawal_approvals a JOIN wallet_challenges c ON c.nonce=a.nonce
                WHERE a.request_id=?""", (request_id,)).fetchone()
            if prior:
                if prior["telegram_id"] != user:
                    raise ApprovalError("This request belongs to another player.", 403)
                if (prior["address"] != address or prior["amount_units"] != units
                        or prior["purpose"] != f"withdraw_{asset.lower()}"):
                    raise ApprovalError("The existing request parameters were changed.", 409)
                if prior["used"] or prior["expires_at"] <= now or prior["attempts"] >= 5:
                    raise ApprovalError("This request is complete or expired. Create a new request.", 409)
                if prior["delivery"] == "auto":
                    account = db.execute(
                        "SELECT trusted_withdrawal_address FROM wallet_accounts WHERE telegram_id=?",
                        (user,),
                    ).fetchone()
                    if account and account["trusted_withdrawal_address"] == address:
                        return {**self._public(prior), "autoApproved": True}
                    raise ApprovalError("This address is no longer verified. Create a new request.", 409)
                if prior["delivery"] != "sent":
                    raise ApprovalError("Telegram code delivery is not confirmed. Wait, then refresh Wallet.", 503)
                return self._public(prior)
            account = db.execute(
                "SELECT trusted_withdrawal_address FROM wallet_accounts WHERE telegram_id=?",
                (user,),
            ).fetchone()
            auto_approved = bool(account and account["trusted_withdrawal_address"] == address)
            if not auto_approved and not os.getenv("BOT_TOKEN"):
                raise ApprovalError("Telegram verification is temporarily unavailable.", 503)
            if db.execute("""SELECT COUNT(*) FROM wallet_withdrawal_approvals
                    WHERE telegram_id=? AND created_at>?""", (user, now - 600)).fetchone()[0] >= 3:
                raise ApprovalError("Too many requests. Try again in 10 minutes.", 429)
            if db.execute("""SELECT 1 FROM wallet_withdrawal_approvals a
                    JOIN wallet_challenges c ON c.nonce=a.nonce
                    WHERE a.telegram_id=? AND a.delivery IN ('sending','sent','auto') AND a.attempts<5
                    AND c.used=0 AND c.expires_at>?""", (user, now)).fetchone():
                raise ApprovalError("Approve or cancel your previous request in Wallet first.", 409)
            if asset == "ZRL":
                available = self.balance(db, user)
                withdrawn = db.execute("""SELECT COALESCE(SUM(amount_units),0) FROM wallet_withdrawals
                    WHERE telegram_id=? AND asset='ZRL' AND created_at>=?
                    AND status IN ('processing','submitted','confirmed')""",
                    (user, now - now % 86400)).fetchone()[0]
                if units + withdrawn > self.cap:
                    raise ApprovalError("The daily ZRL withdrawal limit has been exceeded.", 429)
            else:
                account = db.execute("SELECT sol_lamports FROM wallet_accounts WHERE telegram_id=?", (user,)).fetchone()
                available = account["sol_lamports"] if account else 0
            if units > available:
                raise ApprovalError(f"Insufficient available {asset} balance.", 402)
            item = self.challenge(db, user, address, f"withdraw_{asset.lower()}", units)
            code = "" if auto_approved else f"{secrets.randbelow(1_000_000):06d}"
            db.execute("""INSERT INTO wallet_withdrawal_approvals
                (nonce,request_id,telegram_id,code_hash,delivery,created_at) VALUES(?,?,?,?,?,?)""",
                (item["nonce"], request_id, user,
                 "" if auto_approved else self._digest(item["nonce"], code),
                 "auto" if auto_approved else "sending", now))
            db.commit()
            if auto_approved:
                row = db.execute(
                    "SELECT * FROM wallet_challenges WHERE nonce=?",
                    (item["nonce"],),
                ).fetchone()
                return {**self._public(row), "autoApproved": True}
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
                json={"chat_id": user, "text": (
                    f"Zer0Life Run — withdrawal verification\n{amount} {asset}\n"
                    f"Recipient:\n{address}\n\nCode: {code}\n"
                    "Valid for 5 minutes. Enter it only in your Mini App. "
                    "If you did not request this withdrawal, do not enter or share the code."
                )}, timeout=12)
            response.raise_for_status()
            if not response.json().get("ok"):
                raise ValueError("delivery failed")
        except Exception:
            with self.connect(self.path) as db:
                db.execute("UPDATE wallet_withdrawal_approvals SET delivery='failed',code_hash='' WHERE nonce=?",
                           (item["nonce"],))
            raise ApprovalError("The code could not be delivered. Open a private chat with @Zer0lifelabs_ai_bot and tap Start. No funds were deducted.", 503)
        with self.connect(self.path) as db:
            db.execute("UPDATE wallet_withdrawal_approvals SET delivery='sent' WHERE nonce=?", (item["nonce"],))
            row = db.execute("SELECT * FROM wallet_challenges WHERE nonce=?", (item["nonce"],)).fetchone()
        return self._public(row)

    def verify(self, db, user, nonce, code):
        row = db.execute("SELECT * FROM wallet_withdrawal_approvals WHERE nonce=? AND telegram_id=?",
                         (nonce, user)).fetchone()
        if not row or row["delivery"] != "sent":
            raise ApprovalError("Telegram verification was not found.", 403)
        if row["attempts"] >= 5:
            raise ApprovalError("The attempt limit has been reached. Create a new request.", 429)
        if not re.fullmatch(r"\d{6}", str(code)) or not hmac.compare_digest(
                row["code_hash"], self._digest(nonce, str(code))):
            db.execute("UPDATE wallet_withdrawal_approvals SET attempts=attempts+1 WHERE nonce=?", (nonce,))
            db.commit()  # Bad attempts must survive the error response.
            raise ApprovalError("Incorrect verification code.", 403)

    def cancel(self, user, nonce):
        with self.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT 1 FROM wallet_withdrawal_approvals WHERE nonce=? AND telegram_id=?",
                             (nonce, user)).fetchone()
            if not row:
                raise ApprovalError("Request not found.", 404)
            if db.execute("SELECT 1 FROM wallet_withdrawals WHERE nonce=?", (nonce,)).fetchone():
                raise ApprovalError("The transfer is already processing and cannot be cancelled.", 409)
            db.execute("UPDATE wallet_challenges SET used=1 WHERE nonce=?", (nonce,))
            db.execute("UPDATE wallet_withdrawal_approvals SET code_hash='' WHERE nonce=?", (nonce,))
            db.commit()
