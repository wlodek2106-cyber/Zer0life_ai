"""Deterministic synthetic SQLite/browser fixtures; never accepts a database path."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
# The Replit review mirror stores test-only files beside the application mirror.
# A normal repository checkout still resolves its own application first.
APP_ROOT = ROOT if (ROOT / "wallet_service.py").is_file() else ROOT.parent / "zerolife-run"
sys.path.insert(0, str(APP_ROOT))
from flask import Flask
import wallet_service as service
from recovery_release import RELEASE_ID, RECOVERY_PROTOCOL

NOW = 1_800_000_000
DAY = 86400
TOKEN = "offline-synthetic-recovery-token"
ROOT = Path(__file__).resolve().parents[1]
RECOVERY_CLIENT = {"releaseId": RELEASE_ID, "protocol": RECOVERY_PROTOCOL}


def signed_data(user):
    data = {"auth_date": str(int(time.time())), "user": json.dumps({"id": int(user)})}
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(data.items())).encode(),
                           hashlib.sha256).hexdigest()
    return urlencode(data)


def make_app(folder):
    app = Flask(__name__)
    service.register_wallet_routes(app, str(folder), admin_telegram_id="100")
    return app


def copy_sqlite(source, destination):
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def seed_old_database(path):
    """PR 4 account/ledger columns, without later trusted-destination or farm tables."""
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE wallet_accounts (
                telegram_id TEXT PRIMARY KEY, address TEXT UNIQUE,
                legacy_pending_units INTEGER NOT NULL DEFAULT 0,
                legacy_pending_sol_lamports INTEGER NOT NULL DEFAULT 0,
                legacy_recorded INTEGER NOT NULL DEFAULT 0,
                sol_lamports INTEGER NOT NULL DEFAULT 0,
                pending_run_units INTEGER NOT NULL DEFAULT 0,
                checkin_day INTEGER NOT NULL DEFAULT 1, last_checkin_date TEXT,
                registration_claimed INTEGER NOT NULL DEFAULT 0,
                free_spins INTEGER NOT NULL DEFAULT 0, pending_boxes INTEGER NOT NULL DEFAULT 0,
                booster_rate INTEGER NOT NULL DEFAULT 300, booster_expires INTEGER NOT NULL DEFAULT 0,
                active_run_id TEXT, run_last_lat REAL, run_last_lon REAL, run_last_at INTEGER,
                run_id TEXT, run_active INTEGER NOT NULL DEFAULT 0,
                referral_code TEXT, referred_by TEXT
            );
            CREATE TABLE wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id TEXT NOT NULL,
                source TEXT NOT NULL, delta_units INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL
            );
            CREATE TABLE wallet_legacy_farm_returns (
                telegram_id TEXT NOT NULL, farm_id TEXT NOT NULL, request_id TEXT NOT NULL,
                asset TEXT NOT NULL, principal_units INTEGER NOT NULL, returned_at INTEGER NOT NULL,
                PRIMARY KEY(telegram_id,farm_id)
            );
        """)
        for user, zrl, sol, pending_zrl, pending_sol in [
            ("200", 25_000_003, 500_001, 100_000_001, 10_000_000),
            ("300", 7_000_004, 700_003, 60_123_456, 20_000_000),
        ]:
            db.execute("""INSERT INTO wallet_accounts
                (telegram_id,legacy_pending_units,legacy_pending_sol_lamports,legacy_recorded,
                 sol_lamports,registration_claimed,pending_run_units,referral_code)
                VALUES(?,?,?,1,?,1,123456,?)""", (user, pending_zrl, pending_sol, sol, "FIX" + user))
            db.execute("INSERT INTO wallet_ledger VALUES(NULL,?,'synthetic-earned',?,?,?)",
                       (user, zrl, "original:" + user, NOW - DAY))


class RecoveryCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="zerolife-synthetic-recovery-")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "wallet_ledger.sqlite3"
        # Real credentials/settings are neither read nor copied into child processes.
        safe = {key: value for key, value in os.environ.items()
                if key in ("PATH", "PYTHONPATH", "LD_LIBRARY_PATH", "PYTHONHOME")}
        safe["BOT_TOKEN"] = TOKEN
        self.enterContext(patch.dict(os.environ, safe, clear=True))
        self.enterContext(patch("time.time", return_value=NOW))
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "socket.create_connection", "requests.sessions.Session.request"):
            self.enterContext(patch(target, side_effect=AssertionError("Network forbidden in recovery checks")))
        self.enterContext(patch.object(service, "_rpc_client",
                                      side_effect=AssertionError("RPC forbidden in recovery checks")))
        self.enterContext(patch.object(service, "_onchain_balances", return_value={
            "solBalance": None, "zrlBalance": None, "zrlConfigured": False,
        }))
        seed_old_database(self.path)
        self.archive = Path(self.folder.name) / "synthetic-old.sqlite3"
        copy_sqlite(self.path, self.archive)
        self.app = make_app(self.folder.name)
        self.client = self.app.test_client()
        self.snapshots = json.loads(subprocess.check_output(
            ["node", str(ROOT / "tests/helpers/recovery-storage.cjs")], text=True, timeout=15))

    def post(self, route, body=None, user="200", client=None):
        return (client or self.client).post(route, json={
            "initData": signed_data(user), "recoveryClient": RECOVERY_CLIENT, **(body or {})})

    def rows(self, table):
        # Table names are test constants, not external input.
        with service._connect(str(self.path)) as db:
            return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def totals(self):
        with service._connect(str(self.path)) as db:
            return {row["telegram_id"]: (
                db.execute("SELECT COALESCE(SUM(delta_units),0) FROM wallet_ledger WHERE telegram_id=?",
                           (row["telegram_id"],)).fetchone()[0],
                row["sol_lamports"], row["legacy_pending_units"], row["legacy_pending_sol_lamports"])
                for row in db.execute("SELECT * FROM wallet_accounts")}

    def quote(self, users=("200", "300")):
        for user in users:
            response = self.post("/wallet/recovery/submit", {"farms": self.snapshots[user]["farms"]}, user)
            self.assertEqual(response.status_code, 200, response.json)
        response = self.post("/wallet/recovery/admin/list", user="100")
        self.assertEqual(response.status_code, 200, response.json)
        return [row for row in response.json["requests"] if row["telegramId"] in users]

    def apply(self, records, user="100", client=None):
        return self.post("/wallet/recovery/admin/apply", {"requests": [
            {"requestId": row["requestId"], "digest": row["digest"]} for row in records
        ]}, user, client)

    def restart(self):
        self.app = make_app(self.folder.name)
        self.client = self.app.test_client()

    def add_new_rewards(self):
        with service._connect(str(self.path)) as db:
            for user in ("200", "300"):
                service._ledger_entry(db, user, "run_reward", 3_000_007, "new-run:" + user)
                db.execute("UPDATE wallet_accounts SET sol_lamports=sol_lamports+9 WHERE telegram_id=?", (user,))

    def assert_recovered(self, new_rewards=False):
        # Independent integer oracle: pending + principal + earned minus claimed.
        # A: 100 + 1000 + (100 - 25) + (2 - .5); B: 60.123456 + 250 + (150 - 40).
        zrl_delta = 3_000_007 if new_rewards else 0
        sol_delta = 9 if new_rewards else 0
        totals = self.totals()
        self.assertEqual(totals["200"], (1_201_500_004 + zrl_delta, 12_500_001 + sol_delta, 0, 0))
        self.assertEqual(totals["300"], (427_123_460 + zrl_delta, 20_700_003 + sol_delta, 0, 0))
