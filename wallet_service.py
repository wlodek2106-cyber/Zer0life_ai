"""Authenticated Solana wallet linking, server-side reward ledger, and payouts."""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
import requests
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import parse_qsl

from flask import Blueprint, jsonify, request
from wallet_recovery import recovery_state, register_recovery_routes
from wallet_mobile import register_mobile_routes
from merchant_payments import MerchantPayments, MerchantError, MERCHANT_SOL_WALLET
from wallet_withdrawal_otp import WithdrawalApprovals, ApprovalError
from withdrawal_errors import rpc_call as withdrawal_rpc, describe_failure
from community_feed import register_community_routes
from social_chat import register_social_routes


ZRL_DECIMALS = 6
ZRL_SCALE = 10 ** ZRL_DECIMALS
DAILY_WITHDRAW_CAP = int(os.getenv("MAX_DAILY_ZRL_WITHDRAWAL", "10000")) * ZRL_SCALE
MAX_DEPOSIT_LAMPORTS = 10 * 1_000_000_000
MAX_INIT_DATA_AGE_SECONDS = 3600
MAX_RUN_SPEED_KMH = 14.0
MAX_RUN_SEGMENT_KM = 0.5
SHARED_POOL_WALLET = "HWkraaCqG3iY7hMbBZMsrYrChmctsvcmPdumGE8RVAix"
RED_ROULETTE_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
AFFILIATE_LEVELS = ((1, 1000), (2, 400), (3, 300), (4, 200), (5, 100))
AFFILIATE_ELIGIBLE_SOURCES = {
    "daily_checkin",
    "mystery_box",
    "run_reward",
    "farm_yield",
}
WHEEL_REWARDS = [
    {"text": "Boots x2", "type": "boots", "value": 2},
    {"text": "Mystery Box", "type": "box", "value": 0},
    {"text": "Free Spin", "type": "free_spin", "value": 1},
    {"text": "600 ZRL", "type": "zrl", "value": 600},
    {"text": "10k ZRL", "type": "zrl", "value": 10000},
    {"text": "100 ZRL", "type": "zrl", "value": 100},
    {"text": "150 ZRL", "type": "zrl", "value": 150},
    {"text": "1000 ZRL", "type": "zrl", "value": 1000},
    {"text": "200 ZRL", "type": "zrl", "value": 200},
    {"text": "250 ZRL", "type": "zrl", "value": 250},
    {"text": "2000 ZRL", "type": "zrl", "value": 2000},
    {"text": "300 ZRL", "type": "zrl", "value": 300},
    {"text": "350 ZRL", "type": "zrl", "value": 350},
    {"text": "400 ZRL", "type": "zrl", "value": 400},
]
SLOT_SYMBOLS = ["7️⃣", "🍇", "🍉", "🍊", "🍒"]


def _units(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid ZRL amount")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be greater than zero")
    return int((amount * ZRL_SCALE).quantize(Decimal("1"), rounding=ROUND_DOWN))


def _zrl(units):
    return round(int(units or 0) / ZRL_SCALE, ZRL_DECIMALS)


def _utc_now():
    return int(time.time())


def _today_utc():
    return datetime.now(timezone.utc).date().isoformat()


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def _connect(db_path):
    connection = sqlite3.connect(db_path, timeout=20, isolation_level=None, factory=_ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with _connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS wallet_accounts (
                telegram_id TEXT PRIMARY KEY,
                address TEXT UNIQUE,
                legacy_pending_units INTEGER NOT NULL DEFAULT 0,
                legacy_pending_sol_lamports INTEGER NOT NULL DEFAULT 0,
                legacy_recorded INTEGER NOT NULL DEFAULT 0,
                sol_lamports INTEGER NOT NULL DEFAULT 0,
                pending_run_units INTEGER NOT NULL DEFAULT 0,
                checkin_day INTEGER NOT NULL DEFAULT 1,
                last_checkin_date TEXT,
                registration_claimed INTEGER NOT NULL DEFAULT 0,
                free_spins INTEGER NOT NULL DEFAULT 0,
                pending_boxes INTEGER NOT NULL DEFAULT 0,
                booster_rate INTEGER NOT NULL DEFAULT 300,
                booster_expires INTEGER NOT NULL DEFAULT 0,
                active_run_id TEXT,
                run_last_lat REAL,
                run_last_lon REAL,
                run_last_at INTEGER,
                run_id TEXT,
                run_active INTEGER NOT NULL DEFAULT 0,
                referral_code TEXT,
                referred_by TEXT,
                trusted_withdrawal_address TEXT
            );
            CREATE TABLE IF NOT EXISTS wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL,
                source TEXT NOT NULL,
                delta_units INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS wallet_ledger_user_idx
                ON wallet_ledger(telegram_id, created_at);
            CREATE TABLE IF NOT EXISTS wallet_challenges (
                nonce TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                address TEXT NOT NULL,
                purpose TEXT NOT NULL,
                amount_units INTEGER,
                message TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS wallet_withdrawals (
                nonce TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                address TEXT NOT NULL,
                asset TEXT NOT NULL DEFAULT 'ZRL',
                amount_units INTEGER NOT NULL,
                status TEXT NOT NULL,
                tx_signature TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wallet_deposits (
                signature TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                lamports INTEGER NOT NULL,
                deposit_reference TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wallet_deposit_references (
                reference TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                credited_signature TEXT UNIQUE
            );
            CREATE INDEX IF NOT EXISTS wallet_deposit_references_user_idx
                ON wallet_deposit_references(telegram_id, created_at);
            CREATE TABLE IF NOT EXISTS wallet_game_cooldowns (
                telegram_id TEXT NOT NULL,
                game TEXT NOT NULL,
                last_played INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, game)
            );
            CREATE TABLE IF NOT EXISTS wallet_farms (
                farm_id TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                principal_units INTEGER NOT NULL,
                principal_lamports INTEGER NOT NULL DEFAULT 0,
                daily_rate REAL NOT NULL,
                start_time INTEGER NOT NULL,
                end_time INTEGER NOT NULL,
                claimed_units INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS wallet_blackjack_sessions (
                session_id TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                deck_json TEXT NOT NULL,
                player_json TEXT NOT NULL,
                dealer_json TEXT NOT NULL,
                bet_lamports INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wallet_spend_events (
                event_id TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                purpose TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )
        withdrawal_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(wallet_withdrawals)").fetchall()
        }
        if "asset" not in withdrawal_columns:
            db.execute("ALTER TABLE wallet_withdrawals ADD COLUMN asset TEXT NOT NULL DEFAULT 'ZRL'")
        account_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(wallet_accounts)").fetchall()
        }
        if "legacy_pending_sol_lamports" not in account_columns:
            db.execute("ALTER TABLE wallet_accounts ADD COLUMN legacy_pending_sol_lamports INTEGER NOT NULL DEFAULT 0")
        if "legacy_recorded" not in account_columns:
            db.execute("ALTER TABLE wallet_accounts ADD COLUMN legacy_recorded INTEGER NOT NULL DEFAULT 0")
        if "referral_code" not in account_columns:
            db.execute("ALTER TABLE wallet_accounts ADD COLUMN referral_code TEXT")
        if "referred_by" not in account_columns:
            db.execute("ALTER TABLE wallet_accounts ADD COLUMN referred_by TEXT")
        deposit_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(wallet_deposits)").fetchall()
        }
        if "deposit_reference" not in deposit_columns:
            db.execute("ALTER TABLE wallet_deposits ADD COLUMN deposit_reference TEXT")
        if "trusted_withdrawal_address" not in account_columns:
            db.execute("ALTER TABLE wallet_accounts ADD COLUMN trusted_withdrawal_address TEXT")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS wallet_referral_code_idx "
            "ON wallet_accounts(referral_code)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS wallet_referred_by_idx "
            "ON wallet_accounts(referred_by)"
        )


def _ensure_account(db, telegram_id):
    db.execute(
        "INSERT OR IGNORE INTO wallet_accounts (telegram_id) VALUES (?)",
        (str(telegram_id),),
    )
    row = db.execute(
        "SELECT referral_code FROM wallet_accounts WHERE telegram_id = ?",
        (str(telegram_id),),
    ).fetchone()
    if row and not row["referral_code"]:
        for _ in range(10):
            code = secrets.token_hex(5).upper()
            try:
                db.execute(
                    "UPDATE wallet_accounts SET referral_code = ? "
                    "WHERE telegram_id = ? AND referral_code IS NULL",
                    (code, str(telegram_id)),
                )
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise RuntimeError("Unable to allocate a unique referral code")


def _balance_units(db, telegram_id):
    row = db.execute(
        "SELECT COALESCE(SUM(delta_units), 0) AS balance FROM wallet_ledger WHERE telegram_id = ?",
        (str(telegram_id),),
    ).fetchone()
    return int(row["balance"] or 0)


def _apply_affiliate_commissions(db, telegram_id, reward_units, event_id):
    current_id = str(telegram_id)
    seen = {current_id}
    for level, rate_basis_points in AFFILIATE_LEVELS:
        row = db.execute(
            "SELECT referred_by FROM wallet_accounts WHERE telegram_id = ?",
            (current_id,),
        ).fetchone()
        if not row or not row["referred_by"]:
            break
        referrer_id = str(row["referred_by"])
        if referrer_id in seen:
            break
        seen.add(referrer_id)
        commission_units = int(reward_units) * rate_basis_points // 10000
        if commission_units <= 0:
            break
        _ledger_entry(
            db,
            referrer_id,
            f"affiliate_l{level}",
            commission_units,
            f"affiliate:{event_id}:l{level}",
        )
        current_id = referrer_id


def _ledger_entry(db, telegram_id, source, delta_units, event_id):
    cursor = db.execute(
        """INSERT OR IGNORE INTO wallet_ledger
           (telegram_id, source, delta_units, event_id, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (str(telegram_id), source, int(delta_units), event_id, _utc_now()),
    )
    inserted = cursor.rowcount == 1
    if inserted and int(delta_units) > 0 and source in AFFILIATE_ELIGIBLE_SOURCES:
        _apply_affiliate_commissions(db, telegram_id, int(delta_units), event_id)
    return inserted


def _verify_telegram_init_data(init_data):
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise PermissionError("Telegram authentication is not configured on the server")
    if not isinstance(init_data, str) or not init_data or len(init_data) > 8192:
        raise PermissionError("Open Zer0Life Run from Telegram to continue")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    values = {}
    for key, value in pairs:
        if key in values:
            raise PermissionError("Invalid Telegram session data")
        values[key] = value
    supplied_hash = values.pop("hash", None)
    if not supplied_hash:
        raise PermissionError("Telegram session signature is missing")

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, supplied_hash):
        raise PermissionError("Telegram session signature is invalid")

    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        telegram_id = str(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise PermissionError("Telegram session data is incomplete")

    age = _utc_now() - auth_date
    if age < -60 or age > MAX_INIT_DATA_AGE_SECONDS:
        raise PermissionError("Telegram session has expired; reopen the app")
    return {"id": telegram_id, "username": user.get("username", "")}


def _verify_phantom_signature(address, message, signature_b64):
    try:
        signature_bytes = base64.b64decode(signature_b64, validate=True)
        if len(signature_bytes) != 64:
            return False
        from solders.pubkey import Pubkey
        from solders.signature import Signature

        return Signature.from_bytes(signature_bytes).verify(
            Pubkey.from_string(address), message.encode("utf-8")
        )
    except Exception:
        return False


def _json_body():
    return request.get_json(silent=True) or {}


def _response_error(message, status):
    return jsonify({"success": False, "error": message}), status


def _new_deposit_reference():
    from base58 import b58encode

    return b58encode(secrets.token_bytes(32)).decode("ascii")


def _verified_deposit_lamports(result, signature, reference):
    if not isinstance(result, dict):
        raise ValueError("Transaction is missing or not finalized on Solana")

    meta = result.get("meta")
    if not isinstance(meta, dict) or "err" not in meta:
        raise ValueError("Solana did not return a finalized transaction status")
    if meta.get("err") is not None:
        raise ValueError("Transaction failed on Solana")

    transaction = result.get("transaction") or {}
    signatures = transaction.get("signatures") or []
    if signature not in signatures:
        raise ValueError("Solana RPC returned a different transaction signature")

    message = transaction.get("message") or {}
    account_keys = set()
    key_groups = [message.get("accountKeys") or []]
    loaded_addresses = meta.get("loadedAddresses") or {}
    key_groups.extend([
        loaded_addresses.get("writable") or [],
        loaded_addresses.get("readonly") or [],
    ])
    for group in key_groups:
        for key in group:
            public_key = key.get("pubkey") if isinstance(key, dict) else key
            if isinstance(public_key, str):
                account_keys.add(public_key)
    if reference not in account_keys:
        raise ValueError("Transaction does not include this account's unique deposit reference")

    lamports = 0
    for instruction in message.get("instructions") or []:
        parsed = instruction.get("parsed") or {}
        info = parsed.get("info") or {}
        if instruction.get("program") != "system" or parsed.get("type") != "transfer":
            continue
        if info.get("destination") != SHARED_POOL_WALLET:
            continue
        try:
            transfer_lamports = int(info.get("lamports", 0))
        except (TypeError, ValueError):
            continue
        if transfer_lamports > 0:
            lamports += transfer_lamports

    if lamports <= 0:
        raise ValueError("Transaction has no SOL transfer to the Zer0Life deposit pool")
    if lamports > MAX_DEPOSIT_LAMPORTS:
        raise ValueError("Deposit exceeds the 10 SOL per-transaction limit")
    return lamports


def _rpc_client():
    from solana.rpc.api import Client
    return Client(os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))


def _onchain_balances(address):
    balances = {"solBalance": None, "zrlBalance": None, "zrlConfigured": bool(os.getenv("ZRL_MINT"))}
    try:
        from solders.pubkey import Pubkey
        from spl.token.instructions import get_associated_token_address

        client = _rpc_client()
        owner = Pubkey.from_string(address)
        balances["solBalance"] = client.get_balance(owner).value / 1_000_000_000
        mint_text = os.getenv("ZRL_MINT")
        if mint_text:
            mint = Pubkey.from_string(mint_text)
            ata = get_associated_token_address(owner, mint)
            try:
                token_balance = client.get_token_account_balance(ata).value
                balances["zrlBalance"] = float(token_balance.ui_amount or 0)
            except Exception:
                balances["zrlBalance"] = 0.0
    except Exception:
        # Return nulls rather than a fabricated zero when the RPC is unavailable.
        pass
    return balances


def _create_wallet_challenge(db, telegram_id, address, purpose, amount_units=None):
    nonce = secrets.token_urlsafe(24)
    expiry = _utc_now() + 300
    asset = "SOL" if purpose == "withdraw_sol" else "ZRL"
    amount = (amount_units / (1_000_000_000 if asset == "SOL" else ZRL_SCALE)) if amount_units is not None else "—"
    message = (
        "Zer0Life Run wallet verification\n"
        f"Purpose: {purpose}\nTelegram user: {telegram_id}\nWallet: {address}\n"
        f"Amount: {amount} {asset}\nNonce: {nonce}\nExpires: {expiry}"
    )
    db.execute("""INSERT INTO wallet_challenges
               (nonce,telegram_id,address,purpose,amount_units,message,expires_at)
               VALUES(?,?,?,?,?,?,?)""", (nonce, telegram_id, address, purpose, amount_units, message, expiry))
    return {"nonce": nonce, "message": message, "expiresAt": expiry}


def register_wallet_routes(app, data_dir, admin_telegram_id=None):
    db_path = os.path.join(data_dir, "wallet_ledger.sqlite3")
    _init_db(db_path)
    merchant = MerchantPayments(
        db_path, _connect, _ensure_account, _rpc_client, _utc_now,
        SHARED_POOL_WALLET, app.logger,
    )
    approvals = WithdrawalApprovals(
        db_path, _connect, _create_wallet_challenge, _balance_units,
        ZRL_SCALE, DAILY_WITHDRAW_CAP, SHARED_POOL_WALLET,
    )
    bp = Blueprint("zerolife_wallet", __name__)

    @bp.after_request
    def wallet_release_header(response):
        # Public release marker, including on 401s, enables a deployment check
        # without obtaining a player's signed Telegram session or balance.
        response.headers["X-Zer0Life-Wallet-Schema"] = "telegram-deposits-reference-1"
        return response

    def authenticated_user(data):
        return _verify_telegram_init_data(data.get("initData"))

    def current_wallet(db, telegram_id):
        row = db.execute(
            "SELECT address FROM wallet_accounts WHERE telegram_id = ?",
            (str(telegram_id),),
        ).fetchone()
        return row["address"] if row else None

    @bp.post("/wallet/state")
    def wallet_state():
        try:
            data = _json_body()
            user = authenticated_user(data)
            with _connect(db_path) as db:
                _ensure_account(db, user["id"])
                account = db.execute(
                    "SELECT * FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                balance = _balance_units(db, user["id"])
                today = _today_utc()
                day_start = int(datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).timestamp())
                withdrawn = db.execute(
                    """SELECT COALESCE(SUM(amount_units), 0) AS total
                       FROM wallet_withdrawals
                       WHERE telegram_id = ? AND created_at >= ?
                         AND asset = 'ZRL'
                         AND status IN ('processing', 'submitted', 'confirmed')""",
                    (user["id"], day_start),
                ).fetchone()["total"]
                farms = db.execute(
                    "SELECT farm_id, asset, principal_units, principal_lamports, daily_rate, start_time, end_time, claimed_units FROM wallet_farms WHERE telegram_id = ? AND status = 'active'",
                    (user["id"],),
                ).fetchall()
                downline_rows = db.execute(
                    """WITH RECURSIVE downline(telegram_id, depth, visited) AS (
                           SELECT telegram_id, 1, ',' || telegram_id || ','
                           FROM wallet_accounts WHERE referred_by = ?
                           UNION ALL
                           SELECT account.telegram_id, downline.depth + 1,
                                  downline.visited || account.telegram_id || ','
                           FROM wallet_accounts AS account
                           JOIN downline ON account.referred_by = downline.telegram_id
                           WHERE downline.depth < 5
                             AND instr(downline.visited, ',' || account.telegram_id || ',') = 0
                       )
                       SELECT depth, COUNT(*) AS users
                       FROM downline GROUP BY depth""",
                    (user["id"],),
                ).fetchall()
                downline_counts = {int(row["depth"]): int(row["users"]) for row in downline_rows}
                affiliate_rows = db.execute(
                    """SELECT source, COALESCE(SUM(delta_units), 0) AS earned
                       FROM wallet_ledger
                       WHERE telegram_id = ? AND source LIKE 'affiliate_l%'
                       GROUP BY source""",
                    (user["id"],),
                ).fetchall()
                affiliate_earnings = {row["source"]: int(row["earned"] or 0) for row in affiliate_rows}
                affiliate_levels = [
                    {
                        "level": level,
                        "ratePercent": rate_basis_points / 100,
                        "users": downline_counts.get(level, 0),
                        "earnedZrl": _zrl(affiliate_earnings.get(f"affiliate_l{level}", 0)),
                    }
                    for level, rate_basis_points in AFFILIATE_LEVELS
                ]
                farm_items = []
                now = _utc_now()
                for farm in farms:
                    elapsed = max(0, min(now, farm["end_time"]) - farm["start_time"]) / 86400
                    principal = farm["principal_units"] / ZRL_SCALE if farm["asset"] == "zrl" else farm["principal_lamports"] / 1_000_000_000
                    accrued = int(max(0, principal * farm["daily_rate"] * elapsed - farm["claimed_units"] / ZRL_SCALE) * ZRL_SCALE)
                    farm_items.append({
                        "id": farm["farm_id"], "asset": farm["asset"],
                        "amount": principal, "dailyRate": farm["daily_rate"],
                        "startTime": farm["start_time"] * 1000,
                        "endTime": farm["end_time"] * 1000,
                        "claimableZrl": _zrl(accrued),
                        "claimedReward": _zrl(farm["claimed_units"]),
                        "months": 12 if farm["end_time"] - farm["start_time"] > 360 * 86400
                            else max(1, round((farm["end_time"] - farm["start_time"]) / (30 * 86400))),
                    })
                address = account["address"]
                # Personal-wallet cards were removed; do not stall game balances
                # on an unrelated external RPC balance lookup.
                chain = {
                    "solBalance": None, "zrlBalance": None, "zrlConfigured": bool(os.getenv("ZRL_MINT"))
                }
                return jsonify({
                    "success": True,
                    "telegramId": str(user["id"]),
                    "withdrawalMode": "trusted_destination_v2",
                    "trustedWithdrawalAddress": account["trusted_withdrawal_address"],
                    "pendingWithdrawalApprovals": approvals.pending(str(user["id"])),
                    "merchantPaymentsEnabled": True,
                    "merchantRecipient": MERCHANT_SOL_WALLET,
                    "pendingPurchases": merchant.pending(str(user["id"])),
                    "lastMerchantPurchase": merchant.receipt(str(user["id"])),
                    "walletAddress": address,
                    "referralCode": account["referral_code"],
                    "affiliateLevels": affiliate_levels,
                    "availableZrl": _zrl(balance),
                    "pendingRunZrl": _zrl(account["pending_run_units"]),
                    "legacyPendingZrl": _zrl(account["legacy_pending_units"]),
                    "legacyPendingSol": account["legacy_pending_sol_lamports"] / 1_000_000_000,
                    "gameSolBalance": account["sol_lamports"] / 1_000_000_000,
                    "dailyWithdrawalLimit": _zrl(DAILY_WITHDRAW_CAP),
                    "dailyWithdrawn": _zrl(withdrawn),
                    "dailyWithdrawalRemaining": _zrl(max(0, DAILY_WITHDRAW_CAP - int(withdrawn or 0))),
                    "freeSpins": account["free_spins"],
                    "checkinDay": account["checkin_day"] or 1,
                    "lastCheckinDate": account["last_checkin_date"],
                    "boosterRate": account["booster_rate"] if account["booster_expires"] == 0 or account["booster_expires"] > now else 300,
                    "boosterExpires": account["booster_expires"] * 1000,
                    "farms": farm_items,
                    "pendingWithdrawals": [
                        {"nonce": row["nonce"], "asset": row["asset"],
                         "amount": row["amount_units"] / (1_000_000_000 if row["asset"] == "SOL" else ZRL_SCALE),
                         "address": row["address"], "txHash": row["tx_signature"], "status": row["status"]}
                        for row in db.execute(
                            "SELECT nonce,asset,amount_units,address,tx_signature,status FROM wallet_withdrawals WHERE telegram_id=? AND status IN ('processing','submitted')",
                            (user["id"],),
                        )
                    ],
                    **recovery_state(db, user["id"], admin_telegram_id),
                    **chain,
                })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception as exc:
            app.logger.exception("wallet state failed")
            return _response_error("Wallet state is temporarily unavailable", 503)

    @bp.post("/wallet/legacy")
    def wallet_legacy():
        try:
            data = _json_body()
            user = authenticated_user(data)
            amount_units = max(0, int(Decimal(str(data.get("amount", 0))) * ZRL_SCALE))
            legacy_sol = Decimal(str(data.get("solAmount", 0)))
            if not legacy_sol.is_finite() or legacy_sol < 0:
                return _response_error("Invalid legacy SOL balance", 400)
            sol_lamports = int((legacy_sol * Decimal(1_000_000_000)).quantize(Decimal("1"), rounding=ROUND_DOWN))
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                if amount_units or sol_lamports:
                    db.execute(
                        """UPDATE wallet_accounts
                           SET legacy_pending_units = ?, legacy_pending_sol_lamports = ?, legacy_recorded = 1
                           WHERE telegram_id = ? AND legacy_recorded = 0""",
                        (amount_units, sol_lamports, user["id"]),
                    )
                db.commit()
            return jsonify({"success": True, "pendingReview": True})
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            return _response_error("Legacy balance could not be recorded", 400)

    @bp.post("/wallet/challenge")
    def wallet_challenge():
        try:
            data = _json_body()
            user = authenticated_user(data)
            address = str(data.get("address", "")).strip()
            purpose = data.get("purpose", "link")
            if purpose not in ("link", "withdraw_zrl", "withdraw_sol") or len(address) < 32 or len(address) > 50:
                return _response_error("Invalid wallet request", 400)
            if purpose == "withdraw_zrl":
                amount_units = _units(data["amount"])
            elif purpose == "withdraw_sol":
                amount = Decimal(str(data["amount"]))
                if not amount.is_finite() or amount <= 0:
                    raise ValueError("Amount must be greater than zero")
                amount_units = int((amount * Decimal(1_000_000_000)).quantize(Decimal("1"), rounding=ROUND_DOWN))
            else:
                amount_units = None
            with _connect(db_path) as db:
                challenge = _create_wallet_challenge(db, user["id"], address, purpose, amount_units)
            return jsonify({"success": True, **challenge})
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except (KeyError, ValueError):
            return _response_error("Invalid amount or wallet", 400)
        except Exception:
            return _response_error("Could not create wallet challenge", 503)

    @bp.post("/wallet/link")
    def wallet_link():
        try:
            data = _json_body()
            user = authenticated_user(data)
            nonce = str(data.get("nonce", ""))
            signature = data.get("signature", "")
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                challenge = db.execute(
                    "SELECT * FROM wallet_challenges WHERE nonce = ?",
                    (nonce,),
                ).fetchone()
                if (not challenge or challenge["telegram_id"] != user["id"]
                    or challenge["purpose"] != "link" or challenge["used"]
                    or challenge["expires_at"] < _utc_now()):
                    db.rollback()
                    return _response_error("Wallet verification expired; try again", 400)
                if not _verify_phantom_signature(challenge["address"], challenge["message"], signature):
                    db.rollback()
                    return _response_error("Solana wallet signature could not be verified", 401)
                _ensure_account(db, user["id"])
                existing = current_wallet(db, user["id"])
                if existing and existing != challenge["address"]:
                    db.rollback()
                    return _response_error("A different wallet is already linked; contact support to change it safely", 409)
                db.execute(
                    "UPDATE wallet_accounts SET address = ? WHERE telegram_id = ?",
                    (challenge["address"], user["id"]),
                )
                db.execute("UPDATE wallet_challenges SET used = 1 WHERE nonce = ?", (nonce,))
                db.commit()
            return jsonify({"success": True, "walletAddress": challenge["address"]})
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("wallet link failed")
            return _response_error("Wallet could not be linked", 400)

    @bp.post("/wallet/deposit/reference")
    def wallet_deposit_reference():
        try:
            data = _json_body()
            user = authenticated_user(data)
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                row = db.execute(
                    "SELECT reference FROM wallet_deposit_references "
                    "WHERE telegram_id = ? AND credited_signature IS NULL "
                    "ORDER BY created_at DESC LIMIT 1",
                    (user["id"],),
                ).fetchone()
                if row is None:
                    for _ in range(5):
                        reference = _new_deposit_reference()
                        try:
                            db.execute(
                                "INSERT INTO wallet_deposit_references "
                                "(reference, telegram_id, created_at) VALUES (?, ?, ?)",
                                (reference, user["id"], _utc_now()),
                            )
                            row = {"reference": reference}
                            break
                        except sqlite3.IntegrityError:
                            row = db.execute(
                                "SELECT reference FROM wallet_deposit_references "
                                "WHERE telegram_id = ? AND credited_signature IS NULL "
                                "ORDER BY created_at DESC LIMIT 1",
                                (user["id"],),
                            ).fetchone()
                            if row is not None:
                                break
                    if row is None:
                        raise RuntimeError("Could not allocate a unique deposit reference")
                db.commit()
            return jsonify({
                "success": True,
                "address": SHARED_POOL_WALLET,
                "reference": row["reference"],
            })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("deposit reference preparation failed")
            return _response_error("Could not prepare a deposit reference", 503)

    @bp.post("/wallet/deposit/verify")
    def wallet_deposit_verify():
        try:
            data = _json_body()
            user = authenticated_user(data)
            signature = str(data.get("txSignature", "")).strip()
            if not signature or len(signature) > 100:
                return _response_error("Transaction signature is required", 400)
            deposit_reference = str(data.get("depositReference", "")).strip()
            if not deposit_reference or len(deposit_reference) > 60:
                return _response_error("Unique deposit reference is required", 400)
            with _connect(db_path) as db:
                reference_row = db.execute(
                    "SELECT reference, credited_signature FROM wallet_deposit_references "
                    "WHERE reference = ? AND telegram_id = ?",
                    (deposit_reference, user["id"]),
                ).fetchone()
                if reference_row is None:
                    return _response_error("This deposit reference does not belong to your Telegram account", 409)
                if reference_row["credited_signature"]:
                    return _response_error("This deposit reference was already credited; request a new one", 409)
                reference = reference_row["reference"]
                if db.execute("SELECT 1 FROM wallet_deposits WHERE signature = ?", (signature,)).fetchone():
                    return _response_error("This deposit transaction was already credited", 409)

            rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
            rpc = requests.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                    "params": [signature, {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}],
                },
                timeout=20,
            )
            rpc.raise_for_status()
            result = rpc.json().get("result")
            try:
                lamports = _verified_deposit_lamports(result, signature, reference)
            except ValueError as exc:
                return _response_error(str(exc), 400)

            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                reference_row = db.execute(
                    "SELECT credited_signature FROM wallet_deposit_references "
                    "WHERE reference = ? AND telegram_id = ?",
                    (reference, user["id"]),
                ).fetchone()
                if reference_row is None or reference_row["credited_signature"]:
                    db.rollback()
                    return _response_error("This deposit reference was already used or is invalid", 409)
                try:
                    db.execute(
                        "INSERT INTO wallet_deposits "
                        "(signature, telegram_id, lamports, deposit_reference, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (signature, user["id"], lamports, reference, _utc_now()),
                    )
                except sqlite3.IntegrityError:
                    db.rollback()
                    return _response_error("This deposit transaction was already credited", 409)
                updated_reference = db.execute(
                    "UPDATE wallet_deposit_references SET credited_signature = ? "
                    "WHERE reference = ? AND telegram_id = ? AND credited_signature IS NULL",
                    (signature, reference, user["id"]),
                )
                if updated_reference.rowcount != 1:
                    db.rollback()
                    return _response_error("This deposit reference was already used or is invalid", 409)
                db.execute(
                    "UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?",
                    (lamports, user["id"]),
                )
                balance = db.execute(
                    "SELECT sol_lamports FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()["sol_lamports"]
                db.commit()
            return jsonify({
                "success": True,
                "depositedSOL": lamports / 1_000_000_000,
                "gameSolBalance": balance / 1_000_000_000,
                "depositReference": reference,
            })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except requests.RequestException:
            return _response_error("Could not verify the transaction with Solana RPC", 503)
        except Exception:
            app.logger.exception("deposit verification failed")
            return _response_error("Deposit could not be verified", 400)

    def send_withdrawal(asset, address, amount_units, nonce):
        private_key = os.getenv("PRIVATE_KEY")
        if not private_key:
            raise RuntimeError("Treasury signing is not configured")
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.transaction import Transaction
        from solders.system_program import transfer, TransferParams
        from base58 import b58decode
        treasury = Keypair.from_bytes(b58decode(private_key))
        if str(treasury.pubkey()) != SHARED_POOL_WALLET:
            raise RuntimeError("Treasury signer does not match the configured Zer0Life pool wallet")
        recipient = Pubkey.from_string(address)
        client = _rpc_client()
        instructions = []
        if asset == "SOL":
            lamports = int(amount_units)
            pool_balance = int(withdrawal_rpc("get_sol_balance", client.get_balance, treasury.pubkey()).value)
            if pool_balance < lamports + 5000:
                raise RuntimeError("Treasury SOL balance is insufficient for this withdrawal and network fee")
            instructions.append(transfer(TransferParams(
                from_pubkey=treasury.pubkey(), to_pubkey=recipient, lamports=lamports
            )))
        else:
            from spl.token.instructions import (
                transfer_checked, TransferCheckedParams,
                get_associated_token_address, create_associated_token_account,
            )
            mint_text = os.getenv("ZRL_MINT")
            if not mint_text:
                raise RuntimeError("ZRL token mint is not configured")
            mint = Pubkey.from_string(mint_text)
            source = get_associated_token_address(treasury.pubkey(), mint)
            destination = get_associated_token_address(recipient, mint)
            if int(withdrawal_rpc("get_zrl_balance", client.get_token_account_balance, source).value.amount) < int(amount_units):
                raise RuntimeError("Treasury ZRL liquidity is insufficient")
            rent = 0
            if withdrawal_rpc("get_recipient_token_account", client.get_account_info, destination).value is None:
                rent = int(withdrawal_rpc("get_token_account_rent", client.get_minimum_balance_for_rent_exemption, 165).value)
                instructions.append(create_associated_token_account(
                    payer=treasury.pubkey(), owner=recipient, mint=mint
                ))
            if int(withdrawal_rpc("get_fee_balance", client.get_balance, treasury.pubkey()).value) < rent + 5000:
                raise RuntimeError("Treasury SOL balance is insufficient for token-account rent and fee")
            instructions.append(transfer_checked(TransferCheckedParams(
                program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
                source=source, mint=mint, dest=destination, owner=treasury.pubkey(),
                amount=int(amount_units), decimals=ZRL_DECIMALS,
            )))
        from solders.instruction import Instruction
        instructions.append(Instruction(
            Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"),
            f"zerolife-withdraw:{nonce}".encode(), [],
        ))
        blockhash = withdrawal_rpc("get_latest_blockhash", client.get_latest_blockhash).value.blockhash
        tx = Transaction.new_signed_with_payer(
            instructions=instructions,
            payer=treasury.pubkey(),
            signing_keypairs=[treasury],
            recent_blockhash=blockhash,
        )
        signature = str(tx.signatures[0])
        # Persist the deterministic signature BEFORE broadcast. A network timeout
        # may occur after Solana accepted a transaction: never blindly refund it.
        with _connect(db_path) as db:
            changed = db.execute("""UPDATE wallet_withdrawals SET tx_signature=?
                                   WHERE nonce=? AND status='processing'""", (signature, nonce)).rowcount
            if changed != 1:
                raise RuntimeError("Withdrawal reservation is no longer active")
        submitted = str(withdrawal_rpc("broadcast_transaction", client.send_raw_transaction, bytes(tx)).value)
        if submitted != signature:
            raise RuntimeError("RPC returned an unexpected transaction signature")
        return signature

    @bp.post("/wallet/withdrawals/status")
    def withdrawal_status():
        try:
            data = _json_body()
            user = authenticated_user(data)
            nonce = str(data.get("nonce", ""))
            with _connect(db_path) as db:
                row = db.execute("SELECT * FROM wallet_withdrawals WHERE nonce=? AND telegram_id=?",
                                 (nonce, user["id"])).fetchone()
            if not row:
                return _response_error("Withdrawal not found", 404)
            if row["status"] in ("processing", "submitted") and row["tx_signature"]:
                response = requests.post(
                    os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"),
                    json={"jsonrpc": "2.0", "id": 1, "method": "getSignatureStatuses",
                          "params": [[row["tx_signature"]], {"searchTransactionHistory": True}]},
                    timeout=15,
                )
                response.raise_for_status()
                chain = response.json().get("result", {}).get("value", [None])[0]
                if chain and chain.get("confirmationStatus") == "finalized":
                    with _connect(db_path) as db:
                        db.execute("BEGIN IMMEDIATE")
                        current = db.execute("SELECT * FROM wallet_withdrawals WHERE nonce=?", (nonce,)).fetchone()
                        if current["status"] in ("processing", "submitted"):
                            failed = chain.get("err") is not None
                            if failed and current["asset"] == "ZRL":
                                _ledger_entry(db, user["id"], "withdrawal_refund", current["amount_units"], f"withdraw-refund:{nonce}")
                            elif failed:
                                db.execute("UPDATE wallet_accounts SET sol_lamports=sol_lamports+? WHERE telegram_id=?",
                                           (current["amount_units"], user["id"]))
                            db.execute("UPDATE wallet_withdrawals SET status=? WHERE nonce=?",
                                       ("failed" if failed else "confirmed", nonce))
                        db.commit()
                        row = db.execute("SELECT * FROM wallet_withdrawals WHERE nonce=?", (nonce,)).fetchone()
            return jsonify(
                success=True, status=row["status"], asset=row["asset"],
                amount=row["amount_units"] / (1_000_000_000 if row["asset"] == "SOL" else ZRL_SCALE),
                address=row["address"], txHash=row["tx_signature"],
            )
        except PermissionError as error:
            return _response_error(str(error), 401)
        except Exception:
            return _response_error("Chain confirmation is unavailable; funds remain reserved to prevent a duplicate payment", 503)

    @bp.post("/wallet/withdrawals/request")
    def request_telegram_withdrawal():
        try:
            data = _json_body()
            user = authenticated_user(data)
            item = approvals.request(str(user["id"]), data)
            if item.get("autoApproved"):
                request_data = dict(data)
                request_data["nonce"] = item["nonce"]
                return execute_withdrawal(trusted_auto=True, request_data=request_data)
            return jsonify(success=True, **item)
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except ApprovalError as exc:
            return _response_error(str(exc), exc.status)
        except Exception:
            # Never log request bodies, OTPs, bot API URLs or delivery responses.
            return _response_error("Withdrawal verification is temporarily unavailable. No funds were deducted.", 503)

    @bp.post("/wallet/withdrawals/cancel")
    def cancel_telegram_withdrawal():
        try:
            data = _json_body()
            user = authenticated_user(data)
            approvals.cancel(str(user["id"]), str(data.get("nonce", "")))
            return jsonify(success=True)
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except ApprovalError as exc:
            return _response_error(str(exc), exc.status)

    @bp.post("/wallet/withdraw")
    def wallet_withdraw():
        return execute_withdrawal()

    @bp.post("/wallet/withdrawals/confirm")
    def confirm_telegram_withdrawal():
        return execute_withdrawal(via_telegram=True)

    def execute_withdrawal(via_telegram=False, trusted_auto=False, request_data=None):
        nonce = ""
        try:
            data = request_data if request_data is not None else _json_body()
            user = authenticated_user(data)
            nonce = str(data.get("nonce", ""))
            signature = data.get("signature", "")
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                challenge = db.execute(
                    "SELECT * FROM wallet_challenges WHERE nonce = ?",
                    (nonce,),
                ).fetchone()
                if not challenge:
                    db.rollback()
                    return _response_error("Withdrawal confirmation was not found", 400)
                previous = db.execute(
                    "SELECT * FROM wallet_withdrawals WHERE nonce = ?",
                    (nonce,),
                ).fetchone()
                if challenge["telegram_id"] != user["id"]:
                    db.rollback()
                    return _response_error("Withdrawal belongs to another account", 403)
                if previous and previous["status"] in ("processing", "submitted", "confirmed"):
                    db.rollback()
                    return jsonify({
                        "success": True, "status": previous["status"],
                        "txHash": previous["tx_signature"], "replayed": True,
                    })
                if (challenge["telegram_id"] != user["id"] or challenge["used"]
                    or challenge["expires_at"] < _utc_now()
                    or challenge["purpose"] not in ("withdraw_zrl", "withdraw_sol")):
                    db.rollback()
                    return _response_error("Withdrawal confirmation expired; try again", 400)
                _ensure_account(db, user["id"])
                if via_telegram:
                    approvals.verify(db, str(user["id"]), nonce, data.get("code", ""))
                    db.execute(
                        "UPDATE wallet_accounts SET trusted_withdrawal_address = ? WHERE telegram_id = ?",
                        (challenge["address"], user["id"]),
                    )
                    linked = challenge["address"]
                elif trusted_auto:
                    trusted = db.execute(
                        "SELECT trusted_withdrawal_address FROM wallet_accounts WHERE telegram_id = ?",
                        (user["id"],),
                    ).fetchone()
                    approval = db.execute(
                        "SELECT delivery FROM wallet_withdrawal_approvals WHERE nonce = ? AND telegram_id = ?",
                        (nonce, user["id"]),
                    ).fetchone()
                    if (not trusted or trusted["trusted_withdrawal_address"] != challenge["address"]
                            or not approval or approval["delivery"] != "auto"):
                        db.rollback()
                        return _response_error("This address has not been verified for code-free withdrawals.", 403)
                    linked = challenge["address"]
                else:
                    linked = current_wallet(db, user["id"])
                    if not linked or linked != challenge["address"]:
                        db.rollback()
                        return _response_error("Withdrawal wallet is not linked to this Telegram account", 403)
                    if not _verify_phantom_signature(linked, challenge["message"], signature):
                        db.rollback()
                        return _response_error("Solana wallet signature could not be verified", 401)

                asset = "SOL" if challenge["purpose"] == "withdraw_sol" else "ZRL"
                amount_units = int(challenge["amount_units"] or 0)
                if amount_units <= 0:
                    db.rollback()
                    return _response_error("Invalid withdrawal amount", 400)
                if asset == "ZRL":
                    day_start = int(datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ).timestamp())
                    already = int(db.execute(
                        """SELECT COALESCE(SUM(amount_units), 0) AS total
                           FROM wallet_withdrawals
                           WHERE telegram_id = ? AND asset = 'ZRL' AND created_at >= ?
                             AND status IN ('processing', 'submitted', 'confirmed')""",
                        (user["id"], day_start),
                    ).fetchone()["total"] or 0)
                    if already + amount_units > DAILY_WITHDRAW_CAP:
                        db.rollback()
                        return _response_error(
                            f"Daily ZRL withdrawal limit is {_zrl(DAILY_WITHDRAW_CAP)}; remaining today: {_zrl(max(0, DAILY_WITHDRAW_CAP - already))}",
                            429,
                        )
                    balance = _balance_units(db, user["id"])
                    if amount_units > balance:
                        db.rollback()
                        return _response_error("Insufficient available ZRL balance", 402)
                    _ledger_entry(db, user["id"], "withdrawal", -amount_units, f"withdraw:{nonce}")
                else:
                    sol_balance = int(db.execute(
                        "SELECT sol_lamports FROM wallet_accounts WHERE telegram_id = ?",
                        (user["id"],),
                    ).fetchone()["sol_lamports"])
                    if amount_units > sol_balance:
                        db.rollback()
                        return _response_error("Insufficient verified SOL deposit balance", 402)
                    db.execute(
                        "UPDATE wallet_accounts SET sol_lamports = sol_lamports - ? WHERE telegram_id = ?",
                        (amount_units, user["id"]),
                    )
                db.execute(
                    """INSERT INTO wallet_withdrawals
                       (nonce, telegram_id, address, asset, amount_units, status, created_at)
                       VALUES (?, ?, ?, ?, ?, 'processing', ?)""",
                    (nonce, user["id"], linked, asset, amount_units, _utc_now()),
                )
                db.execute("UPDATE wallet_challenges SET used = 1 WHERE nonce = ?", (nonce,))
                db.commit()

            try:
                tx_signature = send_withdrawal(asset, linked, amount_units, nonce)
            except Exception as exc:
                failure = describe_failure(exc, nonce)
                app.logger.warning(
                    "withdrawal transfer failed: code=%s stage=%s types=%s http_status=%s reference=%s",
                    failure["code"], failure["stage"], failure["types"],
                    failure["httpStatus"], failure["reference"],
                )
                with _connect(db_path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    reserved = db.execute("SELECT * FROM wallet_withdrawals WHERE nonce=?", (nonce,)).fetchone()
                    if reserved["tx_signature"]:
                        db.commit()
                        return jsonify(success=True, status="processing", asset=asset,
                                       txHash=reserved["tx_signature"],
                                       message="Broadcast confirmation is uncertain; funds remain reserved until chain verification"), 202
                    if asset == "ZRL":
                        _ledger_entry(db, user["id"], "withdrawal_refund", amount_units, f"withdraw-refund:{nonce}")
                    else:
                        db.execute(
                            "UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?",
                            (amount_units, user["id"]),
                        )
                    db.execute(
                        "UPDATE wallet_withdrawals SET status = 'failed' WHERE nonce = ?",
                        (nonce,),
                    )
                    db.commit()
                return jsonify(
                    success=False,
                    error=f'{failure["message"]} The amount was returned to your game balance. Code: {failure["code"]}. Ref: {failure["reference"]}',
                    errorCode=failure["code"], supportReference=failure["reference"],
                    balanceRestored=True,
                ), 503
            with _connect(db_path) as db:
                db.execute(
                    "UPDATE wallet_withdrawals SET status = 'submitted', tx_signature = ? WHERE nonce = ?",
                    (tx_signature, nonce),
                )
            return jsonify({
                "success": True, "status": "submitted", "asset": asset,
                "amount": amount_units / (1_000_000_000 if asset == "SOL" else ZRL_SCALE),
                "txHash": tx_signature,
            })
        except ApprovalError as exc:
            return _response_error(str(exc), exc.status)
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("secure withdrawal failed")
            return _response_error("Withdrawal is temporarily unavailable", 503)

    @bp.post("/wallet/purchases/status")
    def merchant_purchase_status():
        try:
            data = _json_body()
            user = authenticated_user(data)
            result = merchant.status(str(user["id"]), str(data.get("purchaseId", "")))
            return jsonify(result), 202 if result["status"] == "pending" else 200
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except MerchantError as exc:
            return _response_error(str(exc), exc.status)
        except Exception:
            app.logger.exception("Merchant payment status unavailable")
            return _response_error("Payment status is unavailable; check the existing purchase before retrying", 503)

    @bp.post("/wallet/spend")
    def wallet_spend():
        try:
            data = _json_body()
            user = authenticated_user(data)
            purpose = str(data.get("purpose", ""))
            event_id = str(data.get("eventId", ""))
            if purpose not in ("repair", "dao_vote", "booster") or not event_id:
                return _response_error("Invalid wallet spend request", 400)
            if purpose == "booster":
                try:
                    result = merchant.purchase(str(user["id"]), event_id, data.get("costSol"))
                    return jsonify(result), 202 if result["status"] == "pending" else 200
                except MerchantError as exc:
                    return _response_error(str(exc), exc.status)

            zrl_cost = {"repair": 50, "dao_vote": 10}
            now = _utc_now()
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute(
                    "SELECT 1 FROM wallet_spend_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if prior:
                    db.rollback()
                    return _response_error("This spend request was already processed", 409)
                _ensure_account(db, user["id"])
                units = zrl_cost[purpose] * ZRL_SCALE
                balance = _balance_units(db, user["id"])
                if balance < units:
                    db.rollback()
                    return _response_error("Insufficient available ZRL balance", 402)
                _ledger_entry(db, user["id"], purpose, -units, event_id)
                db.execute(
                    "INSERT INTO wallet_spend_events (event_id, telegram_id, purpose, created_at) VALUES (?, ?, ?, ?)",
                    (event_id, user["id"], purpose, now),
                )
                balance = _balance_units(db, user["id"])
                account = db.execute(
                    "SELECT sol_lamports, booster_rate, booster_expires FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                db.commit()
            return jsonify({
                "success": True,
                "purpose": purpose,
                "availableZrl": _zrl(balance),
                "gameSolBalance": account["sol_lamports"] / 1_000_000_000,
                "boosterRate": account["booster_rate"] if account["booster_expires"] == 0 or account["booster_expires"] > now else 300,
                "boosterExpires": account["booster_expires"] * 1000,
            })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("wallet spend failed")
            return _response_error("Wallet spend is temporarily unavailable", 503)

    @bp.post("/wallet/rewards/register")
    def reward_registration():
        try:
            data = _json_body()
            user = authenticated_user(data)
            referral_code = str(data.get("referralCode") or "").strip().upper()
            referral_choice = str(data.get("referralChoice") or "").strip()
            if referral_choice and referral_choice not in {"with_referral", "without_referral"}:
                return _response_error("Choose whether to register with or without a referral", 400)
            if referral_choice == "without_referral":
                # An explicit opt-out wins over a referral carried by a Telegram invite link.
                referral_code = ""
            elif referral_choice == "with_referral" and not referral_code:
                return _response_error("Enter an invite code or choose to register without a referral", 400)
            if referral_code and not re.fullmatch(r"[A-F0-9]{10}", referral_code):
                return _response_error("Invalid referral code", 400)
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                account = db.execute(
                    "SELECT registration_claimed, referred_by FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                if account["registration_claimed"]:
                    db.rollback()
                    return _response_error("Registration reward was already claimed", 409)
                if referral_choice == "without_referral":
                    db.execute(
                        "UPDATE wallet_accounts SET referred_by = NULL WHERE telegram_id = ?",
                        (str(user["id"]),),
                    )
                elif referral_code:
                    referrer = db.execute(
                        "SELECT telegram_id FROM wallet_accounts WHERE referral_code = ?",
                        (referral_code,),
                    ).fetchone()
                    if not referrer:
                        db.rollback()
                        return _response_error("Referral code was not found", 400)
                    referrer_id = str(referrer["telegram_id"])
                    if referrer_id == str(user["id"]):
                        db.rollback()
                        return _response_error("You cannot use your own referral code", 400)
                    if account["referred_by"] and str(account["referred_by"]) != referrer_id:
                        db.rollback()
                        return _response_error("Referral attribution is already locked", 409)
                    ancestor_id = referrer_id
                    seen = {str(user["id"])}
                    for _ in range(1000):
                        if ancestor_id in seen:
                            db.rollback()
                            return _response_error("Referral chain cannot contain a cycle", 400)
                        seen.add(ancestor_id)
                        ancestor = db.execute(
                            "SELECT referred_by FROM wallet_accounts WHERE telegram_id = ?",
                            (ancestor_id,),
                        ).fetchone()
                        if not ancestor or not ancestor["referred_by"]:
                            break
                        ancestor_id = str(ancestor["referred_by"])
                    else:
                        db.rollback()
                        return _response_error("Referral chain is too deep", 400)
                    db.execute(
                        "UPDATE wallet_accounts SET referred_by = ? WHERE telegram_id = ?",
                        (referrer_id, str(user["id"])),
                    )
                _ledger_entry(db, user["id"], "registration", 1000 * ZRL_SCALE, f"registration:{user['id']}")
                db.execute(
                    "UPDATE wallet_accounts SET registration_claimed = 1 WHERE telegram_id = ?",
                    (user["id"],),
                )
                balance = _balance_units(db, user["id"])
                db.commit()
            return jsonify({"success": True, "reward": 1000, "availableZrl": _zrl(balance)})
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("registration reward failed")
            return _response_error("Registration reward is unavailable", 503)

    @bp.post("/wallet/rewards/check-in")
    def reward_checkin():
        try:
            user = authenticated_user(_json_body())
            today = _today_utc()
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                account = db.execute(
                    "SELECT checkin_day, last_checkin_date FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                if account["last_checkin_date"] == today:
                    db.rollback()
                    return _response_error("Today's reward was already claimed", 409)
                yesterday = (datetime.now(timezone.utc).date().fromordinal(
                    datetime.now(timezone.utc).date().toordinal() - 1
                )).isoformat()
                day = account["checkin_day"] if account["last_checkin_date"] == yesterday else 1
                rewards = [10, 20, 30, 40, 50, 60, 100]
                reward = rewards[max(0, min(day, 7) - 1)]
                event_id = f"checkin:{user['id']}:{today}"
                _ledger_entry(db, user["id"], "daily_checkin", reward * ZRL_SCALE, event_id)
                next_day = 1 if day >= 7 else day + 1
                db.execute(
                    "UPDATE wallet_accounts SET checkin_day = ?, last_checkin_date = ? WHERE telegram_id = ?",
                    (next_day, today, user["id"]),
                )
                balance = _balance_units(db, user["id"])
                db.commit()
            return jsonify({
                "success": True, "reward": reward, "day": day, "nextDay": next_day,
                "checkinDay": next_day, "lastCheckinDate": today,
                "availableZrl": _zrl(balance),
            })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("daily reward failed")
            return _response_error("Daily reward is unavailable", 503)

    @bp.post("/wallet/rewards/box/open")
    def reward_box_open():
        try:
            user = authenticated_user(_json_body())
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                account = db.execute(
                    "SELECT pending_boxes FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                if account["pending_boxes"] <= 0:
                    db.rollback()
                    return _response_error("No verified mystery box is ready", 409)
                reward = secrets.randbelow(50) + 1
                event_id = f"box:{user['id']}:{secrets.token_urlsafe(12)}"
                _ledger_entry(db, user["id"], "mystery_box", reward * ZRL_SCALE, event_id)
                db.execute(
                    "UPDATE wallet_accounts SET pending_boxes = pending_boxes - 1 WHERE telegram_id = ?",
                    (user["id"],),
                )
                balance = _balance_units(db, user["id"])
                db.commit()
            return jsonify({"success": True, "reward": reward, "availableZrl": _zrl(balance)})
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("mystery box claim failed")
            return _response_error("Mystery box reward is unavailable", 503)

    @bp.post("/wallet/games/reward")
    def game_reward():
        """Server-controlled casino outcomes; clients never submit an award amount."""
        try:
            data = _json_body()
            user = authenticated_user(data)
            game = data.get("game")
            if game not in ("wheel", "slots", "roulette"):
                return _response_error("Unknown game", 400)
            event_id = str(data.get("eventId", ""))
            if len(event_id) < 12 or len(event_id) > 80:
                return _response_error("Invalid game request id", 400)
            now = _utc_now()
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                replay = db.execute(
                    "SELECT source, delta_units FROM wallet_ledger WHERE event_id = ? AND telegram_id = ?",
                    (event_id, user["id"]),
                ).fetchone()
                if replay:
                    balance = _balance_units(db, user["id"])
                    db.rollback()
                    return jsonify({"success": True, "reward": _zrl(replay["delta_units"]), "availableZrl": _zrl(balance), "replayed": True})
                _ensure_account(db, user["id"])
                cooldown = db.execute(
                    "SELECT last_played FROM wallet_game_cooldowns WHERE telegram_id = ? AND game = ?",
                    (user["id"], game),
                ).fetchone()
                if cooldown and now - cooldown["last_played"] < 3:
                    db.rollback()
                    return _response_error("Please wait before playing again", 429)
                account = db.execute(
                    "SELECT * FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                reward = 0
                sol_reward_lamports = 0
                outcome = {}
                if game == "wheel":
                    if account["free_spins"] > 0:
                        db.execute("UPDATE wallet_accounts SET free_spins = free_spins - 1 WHERE telegram_id = ?", (user["id"],))
                    else:
                        bet_units = int(0.01 * 1_000_000_000)
                        if account["sol_lamports"] < bet_units:
                            db.rollback()
                            return _response_error("A verified 0.01 SOL game balance is required", 402)
                        db.execute("UPDATE wallet_accounts SET sol_lamports = sol_lamports - ? WHERE telegram_id = ?", (bet_units, user["id"]))
                    if secrets.randbelow(100000) == 0:
                        sol_reward_lamports = 10_000_000
                        db.execute("UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?", (sol_reward_lamports, user["id"]))
                        outcome = {"type": "sol", "text": "0.01 SOL", "value": 0.01}
                    else:
                        sector = WHEEL_REWARDS[secrets.randbelow(len(WHEEL_REWARDS))]
                        outcome = {"type": sector["type"], "text": sector["text"], "value": sector["value"]}
                        if sector["type"] == "zrl":
                            reward = sector["value"]
                        elif sector["type"] == "box":
                            db.execute("UPDATE wallet_accounts SET pending_boxes = pending_boxes + 1 WHERE telegram_id = ?", (user["id"],))
                        elif sector["type"] == "free_spin":
                            db.execute("UPDATE wallet_accounts SET free_spins = free_spins + 1 WHERE telegram_id = ?", (user["id"],))
                        elif sector["type"] == "boots":
                            current_rate = account["booster_rate"] if account["booster_expires"] == 0 or account["booster_expires"] > now else 300
                            db.execute(
                                "UPDATE wallet_accounts SET booster_rate = ?, booster_expires = ? WHERE telegram_id = ?",
                                (max(1500, current_rate), now + 3600, user["id"]),
                            )
                elif game == "slots":
                    bet = float(data.get("betSol", 0))
                    bet_lamports = int(round(bet * 1_000_000_000))
                    if bet <= 0 or bet > 1 or account["sol_lamports"] < bet_lamports:
                        db.rollback()
                        return _response_error("Insufficient verified SOL game balance", 402)
                    db.execute("UPDATE wallet_accounts SET sol_lamports = sol_lamports - ? WHERE telegram_id = ?", (bet_lamports, user["id"]))
                    symbols = SLOT_SYMBOLS
                    roll = secrets.randbelow(100000)
                    if roll == 0:
                        sol_reward_lamports = bet_lamports * 20
                        db.execute("UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?", (sol_reward_lamports, user["id"]))
                        reels = ["7️⃣", "7️⃣", "7️⃣"]
                    elif roll < 40000:
                        reward = secrets.randbelow(951) + 50
                        reels = ["🍇", "🍇", "🍇"]
                    elif roll < 55000:
                        reward = secrets.randbelow(200) + 20
                        reels = ["🍊", "🍊", symbols[secrets.randbelow(len(symbols))]]
                    else:
                        reels = ["🍒", symbols[secrets.randbelow(4)], "🍉"]
                    outcome = {"reels": reels}
                elif game == "roulette":
                    bet = float(data.get("betSol", 0))
                    bet_lamports = int(round(bet * 1_000_000_000))
                    bet_type = data.get("betType")
                    if bet_type not in ("red", "black", "even", "odd") or bet <= 0 or bet > 1 or account["sol_lamports"] < bet_lamports:
                        db.rollback()
                        return _response_error("Invalid bet or insufficient verified SOL game balance", 402)
                    db.execute("UPDATE wallet_accounts SET sol_lamports = sol_lamports - ? WHERE telegram_id = ?", (bet_lamports, user["id"]))
                    number = secrets.randbelow(37)
                    color = "green" if number == 0 else ("red" if number in RED_ROULETTE_NUMBERS else "black")
                    sol_jackpot = secrets.randbelow(100000) == 0
                    if sol_jackpot:
                        number = 1 if bet_type == "red" else 2
                        color = "red" if number in RED_ROULETTE_NUMBERS else "black"
                        sol_reward_lamports = bet_lamports * 2
                        db.execute("UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?", (sol_reward_lamports, user["id"]))
                    won = (
                        (bet_type == color) or
                        (bet_type == "even" and number != 0 and number % 2 == 0) or
                        (bet_type == "odd" and number % 2 == 1)
                    )
                    reward = 150 if won and not sol_jackpot else 0
                    outcome = {"number": number, "color": color, "won": won, "solJackpot": sol_jackpot}
                if reward:
                    _ledger_entry(db, user["id"], f"casino_{game}", reward * ZRL_SCALE, event_id)
                else:
                    # Store zero-value events too so a replay cannot reroll a loss.
                    db.execute(
                        "INSERT OR IGNORE INTO wallet_ledger (telegram_id, source, delta_units, event_id, created_at) VALUES (?, ?, 0, ?, ?)",
                        (user["id"], f"casino_{game}", event_id, now),
                    )
                db.execute(
                    """INSERT INTO wallet_game_cooldowns (telegram_id, game, last_played)
                       VALUES (?, ?, ?) ON CONFLICT(telegram_id, game)
                       DO UPDATE SET last_played = excluded.last_played""",
                    (user["id"], game, now),
                )
                balance = _balance_units(db, user["id"])
                final_account = db.execute(
                    "SELECT sol_lamports, free_spins, booster_rate, booster_expires FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                sol_lamports = final_account["sol_lamports"]
                db.commit()
            return jsonify({
                "success": True, "game": game, "reward": reward,
                "availableZrl": _zrl(balance),
                "gameSolBalance": sol_lamports / 1_000_000_000,
                "solReward": sol_reward_lamports / 1_000_000_000,
                "freeSpins": final_account["free_spins"],
                "boosterRate": final_account["booster_rate"] if final_account["booster_expires"] == 0 or final_account["booster_expires"] > now else 300,
                "boosterExpires": final_account["booster_expires"] * 1000,
                **outcome,
            })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("server game result failed")
            return _response_error("Game reward is unavailable", 503)

    def blackjack_score(hand):
        total = 0
        aces = 0
        for card in hand:
            value = card["val"]
            if value in ("J", "Q", "K"):
                total += 10
            elif value == "A":
                total += 11
                aces += 1
            else:
                total += int(value)
        while total > 21 and aces:
            total -= 10
            aces -= 1
        return total

    def blackjack_payload(session, finished, result=None, reward=0, sol_reward=0, available=0, sol_balance=0):
        dealer = session["dealer"]
        return {
            "success": True,
            "sessionId": session["session_id"],
            "player": session["player"],
            "dealer": dealer if finished else [dealer[0], {"val": "?", "suit": "?"}],
            "playerScore": blackjack_score(session["player"]),
            "dealerScore": blackjack_score(dealer) if finished else blackjack_score(dealer[:1]),
            "finished": finished,
            "result": result,
            "reward": reward,
            "solReward": sol_reward / 1_000_000_000,
            "availableZrl": _zrl(available),
            "gameSolBalance": sol_balance / 1_000_000_000,
        }

    @bp.post("/wallet/games/blackjack/start")
    def blackjack_start():
        try:
            data = _json_body()
            user = authenticated_user(data)
            bet = float(data.get("betSol", 0))
            bet_lamports = int(round(bet * 1_000_000_000))
            now = _utc_now()
            if bet <= 0 or bet > 1:
                return _response_error("Invalid blackjack bet", 400)
            suits = ["♠", "♥", "♦", "♣"]
            values = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
            deck = [{"suit": suit, "val": value} for suit in suits for value in values]
            secrets.SystemRandom().shuffle(deck)
            player = [deck.pop(), deck.pop()]
            dealer = [deck.pop(), deck.pop()]
            session_id = secrets.token_urlsafe(16)
            session = {
                "session_id": session_id, "player": player, "dealer": dealer,
                "deck": deck, "bet_lamports": bet_lamports,
            }
            finished = False
            result = None
            reward = 0
            sol_reward = 0
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                account = db.execute(
                    "SELECT sol_lamports FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                if account["sol_lamports"] < bet_lamports:
                    db.rollback()
                    return _response_error("Insufficient verified SOL game balance", 402)
                active = db.execute(
                    "SELECT 1 FROM wallet_blackjack_sessions WHERE telegram_id = ? AND status = 'active'",
                    (user["id"],),
                ).fetchone()
                if active:
                    db.rollback()
                    return _response_error("Finish your current blackjack hand first", 409)
                db.execute(
                    "UPDATE wallet_accounts SET sol_lamports = sol_lamports - ? WHERE telegram_id = ?",
                    (bet_lamports, user["id"]),
                )
                if secrets.randbelow(100000) == 0:
                    finished = True
                    result = "sol_jackpot"
                    sol_reward = int(bet_lamports * 2.5)
                    db.execute(
                        "UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?",
                        (sol_reward, user["id"]),
                    )
                elif blackjack_score(player) == 21:
                    finished = True
                    result = "win"
                    reward = 500
                    _ledger_entry(db, user["id"], "casino_blackjack", reward * ZRL_SCALE, f"blackjack:{session_id}")
                db.execute(
                    """INSERT INTO wallet_blackjack_sessions
                       (session_id, telegram_id, deck_json, player_json, dealer_json, bet_lamports, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, user["id"], json.dumps(deck), json.dumps(player),
                     json.dumps(dealer), bet_lamports, "finished" if finished else "active", now),
                )
                available = _balance_units(db, user["id"])
                sol_balance = db.execute(
                    "SELECT sol_lamports FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()["sol_lamports"]
                db.commit()
            return jsonify(blackjack_payload(session, finished, result, reward, sol_reward, available, sol_balance))
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("blackjack start failed")
            return _response_error("Blackjack could not start", 503)

    @bp.post("/wallet/games/blackjack/action")
    def blackjack_action():
        try:
            data = _json_body()
            user = authenticated_user(data)
            action = data.get("action")
            if action not in ("hit", "stand"):
                return _response_error("Invalid blackjack action", 400)
            session_id = str(data.get("sessionId", ""))
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM wallet_blackjack_sessions WHERE session_id = ? AND telegram_id = ? AND status = 'active'",
                    (session_id, user["id"]),
                ).fetchone()
                if not row:
                    db.rollback()
                    return _response_error("Active blackjack hand not found", 404)
                deck = json.loads(row["deck_json"])
                player = json.loads(row["player_json"])
                dealer = json.loads(row["dealer_json"])
                session = {
                    "session_id": session_id, "deck": deck, "player": player,
                    "dealer": dealer, "bet_lamports": row["bet_lamports"],
                }
                finished = False
                result = None
                reward = 0
                sol_reward = 0
                if action == "hit":
                    if not deck:
                        finished = True
                        result = "push"
                        db.execute(
                            "UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?",
                            (row["bet_lamports"], user["id"]),
                        )
                    else:
                        player.append(deck.pop())
                        if blackjack_score(player) > 21:
                            finished = True
                            result = "loss"
                else:
                    while blackjack_score(dealer) < 17 and deck:
                        dealer.append(deck.pop())
                    player_score, dealer_score = blackjack_score(player), blackjack_score(dealer)
                    if secrets.randbelow(100000) == 0:
                        result = "sol_jackpot"
                        sol_reward = int(row["bet_lamports"] * 2)
                        db.execute(
                            "UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?",
                            (sol_reward, user["id"]),
                        )
                    elif player_score > 21:
                        result = "loss"
                    elif dealer_score > 21 or player_score > dealer_score:
                        result = "win"
                        reward = 200
                        _ledger_entry(db, user["id"], "casino_blackjack", reward * ZRL_SCALE, f"blackjack:{session_id}")
                    elif player_score == dealer_score:
                        result = "push"
                        db.execute(
                            "UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?",
                            (row["bet_lamports"], user["id"]),
                        )
                    else:
                        result = "loss"
                    finished = True
                db.execute(
                    """UPDATE wallet_blackjack_sessions
                       SET deck_json = ?, player_json = ?, dealer_json = ?, status = ?
                       WHERE session_id = ?""",
                    (json.dumps(deck), json.dumps(player), json.dumps(dealer),
                     "finished" if finished else "active", session_id),
                )
                session.update({"deck": deck, "player": player, "dealer": dealer})
                available = _balance_units(db, user["id"])
                sol_balance = db.execute(
                    "SELECT sol_lamports FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()["sol_lamports"]
                db.commit()
            return jsonify(blackjack_payload(session, finished, result, reward, sol_reward, available, sol_balance))
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("blackjack action failed")
            return _response_error("Blackjack action failed", 503)

    @bp.post("/wallet/run/start")
    def run_start():
        try:
            user = authenticated_user(_json_body())
            run_id = secrets.token_urlsafe(18)
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                db.execute(
                    """UPDATE wallet_accounts SET active_run_id = ?, run_id = ?,
                       run_last_lat = NULL, run_last_lon = NULL, run_last_at = ?, run_active = 1
                       WHERE telegram_id = ?""",
                    (run_id, run_id, _utc_now(), user["id"]),
                )
                db.commit()
            return jsonify({"success": True, "runId": run_id})
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            return _response_error("Could not start verified run", 503)

    @bp.post("/wallet/run/segment")
    def run_segment():
        try:
            data = _json_body()
            user = authenticated_user(data)
            lat, lon = float(data["lat"]), float(data["lon"])
            reported_speed = float(data.get("speedKmh", 0) or 0)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return _response_error("Invalid GPS coordinates", 400)
            now = _utc_now()
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                account = db.execute(
                    "SELECT * FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                if not account or not account["run_active"] or account["run_id"] != data.get("runId"):
                    db.rollback()
                    return _response_error("No active verified run", 409)
                earned = 0
                box_found = False
                previous_at = account["run_last_at"] or now
                elapsed = max(0, now - previous_at)
                distance = 0.0
                if account["run_last_lat"] is not None and elapsed > 0:
                    r = 6371.0
                    p1, p2 = math.radians(account["run_last_lat"]), math.radians(lat)
                    dp = math.radians(lat - account["run_last_lat"])
                    dl = math.radians(lon - account["run_last_lon"])
                    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
                    distance = 2 * r * math.atan2(math.sqrt(a), math.sqrt(max(0, 1 - a)))
                    speed = distance / (elapsed / 3600)
                    if elapsed > 60 or distance > MAX_RUN_SEGMENT_KM or speed > MAX_RUN_SPEED_KMH or reported_speed > MAX_RUN_SPEED_KMH:
                        db.execute(
                            "UPDATE wallet_accounts SET run_last_lat = ?, run_last_lon = ?, run_last_at = ? WHERE telegram_id = ?",
                            (lat, lon, now, user["id"]),
                        )
                        db.commit()
                        return jsonify({"success": True, "accepted": False, "earnedZrl": 0, "pendingRunZrl": _zrl(account["pending_run_units"])})
                    if distance >= 0.003:
                        rate = account["booster_rate"] if account["booster_expires"] == 0 or account["booster_expires"] > now else 300
                        earned = int(distance * rate * ZRL_SCALE)
                        db.execute(
                            "UPDATE wallet_accounts SET pending_run_units = pending_run_units + ? WHERE telegram_id = ?",
                            (earned, user["id"]),
                        )
                        box_found = secrets.randbelow(10000) < 200
                        if box_found:
                            db.execute(
                                "UPDATE wallet_accounts SET pending_boxes = pending_boxes + 1 WHERE telegram_id = ?",
                                (user["id"],),
                            )
                db.execute(
                    "UPDATE wallet_accounts SET run_last_lat = ?, run_last_lon = ?, run_last_at = ? WHERE telegram_id = ?",
                    (lat, lon, now, user["id"]),
                )
                updated = db.execute(
                    "SELECT pending_run_units FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                db.commit()
            return jsonify({
                "success": True, "accepted": earned > 0 or distance < 0.003,
                "earnedZrl": _zrl(earned), "pendingRunZrl": _zrl(updated["pending_run_units"]),
                "boxFound": box_found, "distanceKm": round(distance, 5),
            })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except (KeyError, TypeError, ValueError):
            return _response_error("Invalid run segment", 400)
        except Exception:
            app.logger.exception("run segment failed")
            return _response_error("Run segment could not be verified", 503)

    @bp.post("/wallet/farms/open")
    def farm_open():
        try:
            data = _json_body()
            user = authenticated_user(data)
            asset = str(data.get("asset", "")).lower()
            months = int(data.get("months", 0))
            amount = Decimal(str(data.get("amount", 0)))
            if asset not in ("zrl", "sol") or months not in (1, 3, 6, 12, 300) or not amount.is_finite() or amount <= 0:
                return _response_error("Invalid farming plan", 400)
            if (asset == "sol" and amount > 10) or (asset == "zrl" and amount > 1_000_000):
                return _response_error("Deposit exceeds the farming plan limit", 400)

            if asset == "sol":
                if months == 300:
                    target, total_days = 300000.0, 365
                else:
                    target = {1: 8333.33, 3: 25000.0, 6: 50000.0, 12: 100000.0}[months]
                    total_days = months * 30
                daily_rate = target / total_days
                principal_units = 0
                principal_lamports = int((amount * Decimal(1_000_000_000)).quantize(Decimal("1"), rounding=ROUND_DOWN))
            else:
                daily_rate = {1: 0.0005, 3: 0.002, 6: 0.0035, 12: 0.0065, 300: 0.0005}[months]
                total_days = (12 if months == 300 else months) * 30
                principal_units = int((amount * ZRL_SCALE).quantize(Decimal("1"), rounding=ROUND_DOWN))
                principal_lamports = 0

            now = _utc_now()
            farm_id = secrets.token_urlsafe(16)
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                if asset == "zrl":
                    balance = _balance_units(db, user["id"])
                    if principal_units > balance:
                        db.rollback()
                        return _response_error("Insufficient available ZRL balance", 402)
                    _ledger_entry(db, user["id"], "farm_lock", -principal_units, f"farm-lock:{farm_id}")
                else:
                    account = db.execute(
                        "SELECT sol_lamports FROM wallet_accounts WHERE telegram_id = ?",
                        (user["id"],),
                    ).fetchone()
                    if principal_lamports > account["sol_lamports"]:
                        db.rollback()
                        return _response_error("Insufficient verified SOL deposit balance", 402)
                    db.execute(
                        "UPDATE wallet_accounts SET sol_lamports = sol_lamports - ? WHERE telegram_id = ?",
                        (principal_lamports, user["id"]),
                    )
                db.execute(
                    """INSERT INTO wallet_farms
                       (farm_id, telegram_id, asset, principal_units, principal_lamports,
                        daily_rate, start_time, end_time)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (farm_id, user["id"], asset, principal_units, principal_lamports,
                     daily_rate, now, now + total_days * 86400),
                )
                db.commit()
            return jsonify({
                "success": True, "farmId": farm_id, "asset": asset,
                "amount": float(amount), "dailyRate": daily_rate,
                "startTime": now * 1000, "endTime": (now + total_days * 86400) * 1000,
            })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("farm opening failed")
            return _response_error("Could not start farming deposit", 503)

    @bp.post("/wallet/farms/claim")
    def farm_claim():
        try:
            data = _json_body()
            user = authenticated_user(data)
            farm_id = str(data.get("farmId", ""))
            now = _utc_now()
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                farm = db.execute(
                    "SELECT * FROM wallet_farms WHERE farm_id = ? AND telegram_id = ? AND status = 'active'",
                    (farm_id, user["id"]),
                ).fetchone()
                if not farm:
                    db.rollback()
                    return _response_error("Active farming deposit not found", 404)
                elapsed_days = max(0, min(now, farm["end_time"]) - farm["start_time"]) / 86400
                principal = farm["principal_units"] / ZRL_SCALE if farm["asset"] == "zrl" else farm["principal_lamports"] / 1_000_000_000
                total_due = int(principal * farm["daily_rate"] * elapsed_days * ZRL_SCALE)
                reward = max(0, total_due - int(farm["claimed_units"]))
                if reward:
                    _ledger_entry(db, user["id"], "farm_yield", reward, f"farm-yield:{farm_id}:{total_due}")
                mature = now >= farm["end_time"]
                if mature:
                    if farm["asset"] == "zrl":
                        _ledger_entry(db, user["id"], "farm_principal_return", int(farm["principal_units"]), f"farm-return:{farm_id}")
                    else:
                        db.execute(
                            "UPDATE wallet_accounts SET sol_lamports = sol_lamports + ? WHERE telegram_id = ?",
                            (farm["principal_lamports"], user["id"]),
                        )
                    db.execute("UPDATE wallet_farms SET status = 'completed', claimed_units = ? WHERE farm_id = ?", (total_due, farm_id))
                else:
                    db.execute("UPDATE wallet_farms SET claimed_units = ? WHERE farm_id = ?", (total_due, farm_id))
                balance = _balance_units(db, user["id"])
                sol_balance = db.execute(
                    "SELECT sol_lamports FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()["sol_lamports"]
                db.commit()
            return jsonify({
                "success": True, "reward": _zrl(reward), "mature": mature,
                "returnedPrincipal": float(principal) if mature else 0,
                "asset": farm["asset"], "availableZrl": _zrl(balance),
                "gameSolBalance": sol_balance / 1_000_000_000,
            })
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            app.logger.exception("farm claim failed")
            return _response_error("Farm reward could not be claimed", 503)

    @bp.post("/wallet/run/claim")
    def run_claim():
        try:
            user = authenticated_user(_json_body())
            with _connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _ensure_account(db, user["id"])
                account = db.execute(
                    "SELECT pending_run_units FROM wallet_accounts WHERE telegram_id = ?",
                    (user["id"],),
                ).fetchone()
                amount = int(account["pending_run_units"])
                if amount <= 0:
                    db.rollback()
                    return _response_error("No run rewards to claim", 409)
                event_id = f"run-claim:{user['id']}:{secrets.token_urlsafe(12)}"
                _ledger_entry(db, user["id"], "run_reward", amount, event_id)
                db.execute("UPDATE wallet_accounts SET pending_run_units = 0 WHERE telegram_id = ?", (user["id"],))
                balance = _balance_units(db, user["id"])
                db.commit()
            return jsonify({"success": True, "reward": _zrl(amount), "availableZrl": _zrl(balance)})
        except PermissionError as exc:
            return _response_error(str(exc), 401)
        except Exception:
            return _response_error("Run rewards could not be claimed", 503)

    @bp.post("/wallet/run/stop")
    def run_stop():
        try:
            user = authenticated_user(_json_body())
            with _connect(db_path) as db:
                db.execute(
                    "UPDATE wallet_accounts SET run_active = 0, active_run_id = NULL, run_id = NULL WHERE telegram_id = ?",
                    (user["id"],),
                )
            return jsonify({"success": True})
        except PermissionError as exc:
            return _response_error(str(exc), 401)

    app.register_blueprint(bp)
    register_social_routes(app, db_path, _connect, authenticated_user)
    register_community_routes(
        app,
        db_path,
        _connect,
        authenticated_user,
        admin_telegram_id,
        ledger_entry=_ledger_entry,
        balance_units=_balance_units,
        zrl_scale=ZRL_SCALE,
    )
    register_recovery_routes(app, db_path, _connect, authenticated_user,
                             _ensure_account, _ledger_entry, admin_telegram_id, ZRL_SCALE)
    register_mobile_routes(app, db_path, _connect, authenticated_user,
                           _create_wallet_challenge, _verify_phantom_signature)
