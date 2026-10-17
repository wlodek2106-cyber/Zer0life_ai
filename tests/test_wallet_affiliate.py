"""Affiliate commissions must be posted directly to each referrer's wallet ledger."""
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


TOKEN = "synthetic-affiliate-test-token"
PLAYER = "600"
REFERRERS_BY_LEVEL = ("500", "400", "300", "200", "100")
REWARD_SOURCES = ("daily_checkin", "farm_yield", "mystery_box", "run_reward")


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


class AffiliateCommissionTests(unittest.TestCase):
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
        self._create_referrer_chain()

    def _create_referrer_chain(self):
        ids = ("100", "200", "300", "400", "500", PLAYER)
        parents = {
            "200": "100",
            "300": "200",
            "400": "300",
            "500": "400",
        }
        with service._connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            for user_id in ids:
                service._ensure_account(db, user_id)
            for user_id, parent_id in parents.items():
                db.execute(
                    "UPDATE wallet_accounts SET referred_by = ? WHERE telegram_id = ?",
                    (parent_id, user_id),
                )
            db.commit()

    def post(self, path, body=None, user=PLAYER):
        return self.client.post(
            path,
            json={"initData": init_data(user), **(body or {})},
        )

    def wallet_state(self, user):
        response = self.post("/wallet/state", user=user)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        return response.json

    def register_player(self):
        referral_code = self.wallet_state("500")["referralCode"]
        response = self.post(
            "/wallet/rewards/register",
            {"referralCode": referral_code},
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["availableZrl"], 1000)

    def credit_reward(self, source, event_id, amount_units):
        with service._connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            inserted = service._ledger_entry(db, PLAYER, source, amount_units, event_id)
            db.commit()
        return inserted

    def test_all_five_levels_auto_credit_main_balance_for_every_eligible_source(self):
        self.register_player()
        reward_units = 10_000 * service.ZRL_SCALE
        for index, source in enumerate(REWARD_SOURCES):
            self.assertTrue(self.credit_reward(source, f"eligible:{source}:{index}", reward_units))

        expected_zrl_by_level = (4_000, 1_600, 1_200, 800, 400)
        for level, (user_id, expected_zrl) in enumerate(
            zip(REFERRERS_BY_LEVEL, expected_zrl_by_level),
            start=1,
        ):
            state = self.wallet_state(user_id)
            self.assertEqual(state["availableZrl"], expected_zrl)
            self.assertEqual(state["affiliateLevels"][level - 1]["earnedZrl"], expected_zrl)

        player_state = self.wallet_state(PLAYER)
        self.assertEqual(player_state["availableZrl"], 41_000)

        # Replaying a previously credited reward event must not credit its uplines twice.
        self.assertFalse(self.credit_reward(
            REWARD_SOURCES[0], f"eligible:{REWARD_SOURCES[0]}:0", reward_units,
        ))
        self.assertEqual(self.wallet_state("500")["availableZrl"], expected_zrl_by_level[0])

    def test_signup_and_casino_rewards_do_not_generate_partner_commissions(self):
        self.register_player()
        self.assertTrue(self.credit_reward(
            "casino_blackjack",
            "ineligible:casino:blackjack",
            10_000 * service.ZRL_SCALE,
        ))
        for user_id in REFERRERS_BY_LEVEL:
            state = self.wallet_state(user_id)
            self.assertEqual(state["availableZrl"], 0)
            self.assertTrue(all(level["earnedZrl"] == 0 for level in state["affiliateLevels"]))


if __name__ == "__main__":
    unittest.main()
