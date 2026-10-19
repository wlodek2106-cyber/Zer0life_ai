"""Run claims return authoritative state when the displayed reward is stale."""

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from flask import Flask

import wallet_service as service


TOKEN = "synthetic-run-claim-test-token"
PLAYER = "700"


def init_data(user_id):
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": int(user_id)}),
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret,
        "\n".join(f"{name}={value}" for name, value in sorted(fields.items())).encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


class RunClaimTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env_patch = patch.dict(os.environ, {"BOT_TOKEN": TOKEN})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.app = Flask(__name__)
        service.register_wallet_routes(self.app, self.folder.name)
        self.client = self.app.test_client()
        self.db_path = os.path.join(self.folder.name, "wallet_ledger.sqlite3")

    def post(self, route):
        return self.client.post(route, json={"initData": init_data(PLAYER)})

    def test_empty_run_claim_returns_authoritative_balance_without_debit(self):
        with service._connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            service._ensure_account(db, PLAYER)
            db.commit()
            before = service._balance_units(db, PLAYER)

        response = self.post("/wallet/run/claim")

        self.assertEqual(response.status_code, 409, response.json)
        self.assertFalse(response.json["success"])
        self.assertEqual(response.json["pendingRunZrl"], 0)
        self.assertEqual(response.json["availableZrl"], 0)
        with service._connect(self.db_path) as db:
            self.assertEqual(service._balance_units(db, PLAYER), before)
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM wallet_ledger WHERE telegram_id=? AND source='run_reward'",
                    (PLAYER,),
                ).fetchone()[0],
                0,
            )

    def test_run_claim_credits_pending_once_and_returns_zero_pending(self):
        pending_units = 51368 * (service.ZRL_SCALE // 100)
        with service._connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            service._ensure_account(db, PLAYER)
            db.execute(
                "UPDATE wallet_accounts SET pending_run_units=? WHERE telegram_id=?",
                (pending_units, PLAYER),
            )
            db.commit()

        response = self.post("/wallet/run/claim")

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["reward"], 513.68)
        self.assertEqual(response.json["availableZrl"], 513.68)
        self.assertEqual(response.json["pendingRunZrl"], 0)
        repeated = self.post("/wallet/run/claim")
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.json["availableZrl"], 513.68)
        with service._connect(self.db_path) as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM wallet_ledger WHERE telegram_id=? AND source='run_reward'",
                    (PLAYER,),
                ).fetchone()[0],
                1,
            )
