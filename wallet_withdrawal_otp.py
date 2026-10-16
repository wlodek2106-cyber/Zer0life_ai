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
            raise ApprovalError("Telegram-подтверждение временно недоступно.", 503)
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
            raise ApprovalError("Некорректная заявка на вывод.")
        try:
            if address == self.pool or not Pubkey.from_string(address).is_on_curve():
                raise ValueError()
        except ValueError:
            raise ApprovalError("Укажите адрес личного Solana-кошелька, не адрес пула или токен-счёта.")
        try:
            amount = Decimal(str(data.get("amount", "")))
            units = amount * (10**9 if asset == "SOL" else self.scale)
            if not amount.is_finite() or not 0 < units <= 9_000_000_000_000_000 or units != units.to_integral_value():
                raise ValueError()
            units = int(units)
        except (InvalidOperation, ValueError, OverflowError):
            raise ApprovalError("Укажите корректную положительную сумму вывода.")
        if not os.getenv("BOT_TOKEN"):
            raise ApprovalError("Telegram-подтверждение временно недоступно.", 503)
        now = int(time.time())
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("""SELECT a.*, c.address,c.purpose,c.amount_units,c.expires_at,c.used
                FROM wallet_withdrawal_approvals a JOIN wallet_challenges c ON c.nonce=a.nonce
                WHERE a.request_id=?""", (request_id,)).fetchone()
            if prior:
                if prior["telegram_id"] != user:
                    raise ApprovalError("Заявка принадлежит другому игроку.", 403)
                if (prior["address"] != address or prior["amount_units"] != units
                        or prior["purpose"] != f"withdraw_{asset.lower()}"):
                    raise ApprovalError("Параметры существующей заявки изменены.", 409)
                if prior["used"] or prior["expires_at"] <= now or prior["attempts"] >= 5:
                    raise ApprovalError("Заявка завершена или истекла. Создайте новую.", 409)
                if prior["delivery"] != "sent":
                    raise ApprovalError("Отправка кода не подтверждена. Подождите и обновите Wallet.", 503)
                return self._public(prior)
            if db.execute("""SELECT COUNT(*) FROM wallet_withdrawal_approvals
                    WHERE telegram_id=? AND created_at>?""", (user, now - 600)).fetchone()[0] >= 3:
                raise ApprovalError("Слишком много запросов. Повторите через 10 минут.", 429)
            if db.execute("""SELECT 1 FROM wallet_withdrawal_approvals a
                    JOIN wallet_challenges c ON c.nonce=a.nonce
                    WHERE a.telegram_id=? AND a.delivery IN ('sending','sent') AND a.attempts<5
                    AND c.used=0 AND c.expires_at>?""", (user, now)).fetchone():
                raise ApprovalError("Подтвердите или отмените предыдущую заявку в Wallet.", 409)
            if asset == "ZRL":
                available = self.balance(db, user)
                withdrawn = db.execute("""SELECT COALESCE(SUM(amount_units),0) FROM wallet_withdrawals
                    WHERE telegram_id=? AND asset='ZRL' AND created_at>=?
                    AND status IN ('processing','submitted','confirmed')""",
                    (user, now - now % 86400)).fetchone()[0]
                if units + withdrawn > self.cap:
                    raise ApprovalError("Превышен дневной лимит вывода ZRL.", 429)
            else:
                account = db.execute("SELECT sol_lamports FROM wallet_accounts WHERE telegram_id=?", (user,)).fetchone()
                available = account["sol_lamports"] if account else 0
            if units > available:
                raise ApprovalError(f"Недостаточно доступного баланса {asset}.", 402)
            item = self.challenge(db, user, address, f"withdraw_{asset.lower()}", units)
            db.execute("""INSERT INTO wallet_withdrawal_approvals
                (nonce,request_id,telegram_id,code_hash,created_at) VALUES(?,?,?,?,?)""",
                (item["nonce"], request_id, user, self._digest(item["nonce"], code), now))
            db.commit()
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
                json={"chat_id": user, "text": (
                    f"Zer0Life Run — подтверждение вывода\n{amount} {asset}\n"
                    f"Получатель:\n{address}\n\nКод: {code}\n"
                    "Действует 5 минут. Введите его только в своём Mini App. "
                    "Если вы не запрашивали вывод — не вводите код и никому его не сообщайте."
                )}, timeout=12)
            response.raise_for_status()
            if not response.json().get("ok"):
                raise ValueError("delivery failed")
        except Exception:
            with self.connect(self.path) as db:
                db.execute("UPDATE wallet_withdrawal_approvals SET delivery='failed',code_hash='' WHERE nonce=?",
                           (item["nonce"],))
            raise ApprovalError("Код не доставлен. Откройте личный чат с @Zer0lifelabs_ai_bot и нажмите Start. Деньги не списаны.", 503)
        with self.connect(self.path) as db:
            db.execute("UPDATE wallet_withdrawal_approvals SET delivery='sent' WHERE nonce=?", (item["nonce"],))
            row = db.execute("SELECT * FROM wallet_challenges WHERE nonce=?", (item["nonce"],)).fetchone()
        return self._public(row)

    def verify(self, db, user, nonce, code):
        row = db.execute("SELECT * FROM wallet_withdrawal_approvals WHERE nonce=? AND telegram_id=?",
                         (nonce, user)).fetchone()
        if not row or row["delivery"] != "sent":
            raise ApprovalError("Подтверждение Telegram не найдено.", 403)
        if row["attempts"] >= 5:
            raise ApprovalError("Лимит попыток исчерпан. Создайте новую заявку.", 429)
        if not re.fullmatch(r"\d{6}", str(code)) or not hmac.compare_digest(
                row["code_hash"], self._digest(nonce, str(code))):
            db.execute("UPDATE wallet_withdrawal_approvals SET attempts=attempts+1 WHERE nonce=?", (nonce,))
            db.commit()  # Bad attempts must survive the error response.
            raise ApprovalError("Неверный код подтверждения.", 403)

    def cancel(self, user, nonce):
        with self.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT 1 FROM wallet_withdrawal_approvals WHERE nonce=? AND telegram_id=?",
                             (nonce, user)).fetchone()
            if not row:
                raise ApprovalError("Заявка не найдена.", 404)
            if db.execute("SELECT 1 FROM wallet_withdrawals WHERE nonce=?", (nonce,)).fetchone():
                raise ApprovalError("Перевод уже обрабатывается. Его нельзя отменить повторно.", 409)
            db.execute("UPDATE wallet_challenges SET used=1 WHERE nonce=?", (nonce,))
            db.execute("UPDATE wallet_withdrawal_approvals SET code_hash='' WHERE nonce=?", (nonce,))
            db.commit()
