"""Real transaction construction against a synthetic RPC; never uses real funds."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import base58
from flask import Flask
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction
from solders.transaction_status import TransactionConfirmationStatus as Confirmation
import wallet_service as service
from merchant_payments import MERCHANT_SOL_WALLET

TOKEN = "synthetic-merchant-test-token"


def init_data(user):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": int(user)})}
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        key, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


class FakeChain:
    def __init__(self):
        self.sent, self.status, self.balance = [], None, 100_000_000_000
        self.height, self.timeout, self.fail_status = 100, False, False

    def get_latest_blockhash(self, **kwargs):
        return NS(value=NS(blockhash=Hash.default(), last_valid_block_height=150))

    def get_fee_for_message(self, message, **kwargs):
        return NS(value=10000)

    def get_balance(self, key, **kwargs):
        return NS(value=self.balance)

    def send_raw_transaction(self, data):
        self.sent.append(data)
        if self.timeout:
            raise TimeoutError("synthetic RPC timeout after acceptance")
        return NS(value=Transaction.from_bytes(data).signatures[0])

    def get_signature_statuses(self, signatures, **kwargs):
        if self.fail_status:
            raise TimeoutError("synthetic status failure")
        return NS(value=[self.status])

    def get_block_height(self, **kwargs):
        return NS(value=self.height)


class MerchantTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.key, self.chain = Keypair(), FakeChain()
        for context in [
            patch.dict(os.environ, {"BOT_TOKEN": TOKEN, "PRIVATE_KEY": base58.b58encode(bytes(self.key)).decode()}),
            patch.object(service, "SHARED_POOL_WALLET", str(self.key.pubkey())),
            patch.object(service, "_rpc_client", return_value=self.chain),
            patch.object(service, "_onchain_balances", return_value={
                "solBalance": None, "zrlBalance": None, "zrlConfigured": False,
            }),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.app = Flask(__name__)
        service.register_wallet_routes(self.app, self.folder.name)
        self.client = self.app.test_client()
        self.path = os.path.join(self.folder.name, "wallet_ledger.sqlite3")
        with service._connect(self.path) as db:
            for user, amount in [("200", 2_000_000_000), ("201", 1_000_000_000)]:
                service._ensure_account(db, user)
                db.execute("UPDATE wallet_accounts SET sol_lamports=? WHERE telegram_id=?", (amount, user))

    def post(self, route, body=None, user="200", client=None):
        return (client or self.client).post(route, json={"initData": init_data(user), **(body or {})})

    def buy(self, event="boots-test", cost=.2, **kwargs):
        return self.post("/wallet/spend", {
            "purpose": "booster", "eventId": event, "costSol": cost,
            # Untrusted fields must never control the recipient or rate.
            "recipient": str(self.key.pubkey()), "boosterRate": 999999,
        }, **kwargs)

    def status(self, event="boots-test", **kwargs):
        return self.post("/wallet/purchases/status", {"purchaseId": event}, **kwargs)

    def account(self, user="200"):
        with service._connect(self.path) as db:
            return dict(db.execute("SELECT * FROM wallet_accounts WHERE telegram_id=?", (user,)).fetchone())

    def finalize(self, error=None):
        self.chain.status = NS(confirmation_status=Confirmation.Finalized, err=error)

    def test_pending_payment_builds_real_signed_transfer_to_fixed_merchant_and_reserves_once(self):
        result = self.buy()
        self.assertEqual(result.status_code, 202, result.json)
        self.assertEqual(result.json["status"], "pending")
        self.assertEqual(result.json["recipient"], MERCHANT_SOL_WALLET)
        self.assertEqual(self.account()["sol_lamports"], 1_800_000_000)
        self.assertEqual(self.account()["booster_rate"], 300)
        tx = Transaction.from_bytes(self.chain.sent[0])
        tx.verify()
        instruction = tx.message.instructions[0]
        self.assertEqual(str(tx.message.account_keys[instruction.accounts[1]]), MERCHANT_SOL_WALLET)
        self.assertEqual(int.from_bytes(bytes(instruction.data)[4:], "little"), 200_000_000)
        self.assertEqual(bytes(tx.message.instructions[1].data), b"Zer0Life Boots:boots-test")
        with service._connect(self.path) as db:
            row = db.execute("SELECT * FROM wallet_merchant_purchases").fetchone()
            self.assertEqual(base64.b64decode(row["signed_transaction"]), self.chain.sent[0])
            self.assertEqual(row["tx_signature"], str(tx.signatures[0]))
        self.assertEqual(self.account("201")["sol_lamports"], 1_000_000_000)

    def test_finalized_payment_activates_once_and_replays_never_debit_again(self):
        self.buy()
        self.finalize()
        confirmed = self.status()
        self.assertEqual(confirmed.json["status"], "confirmed")
        first = self.account()
        self.assertEqual(first["booster_rate"], 1500)
        self.assertAlmostEqual(first["booster_expires"], int(time.time()) + 7 * 86400, delta=2)
        self.assertEqual(self.buy().json["status"], "confirmed")
        self.assertEqual(self.account(), first)
        self.assertEqual(len(self.chain.sent), 1)

    def test_confirmed_but_not_finalized_does_not_activate_or_refund(self):
        self.buy()
        self.chain.status = NS(confirmation_status=Confirmation.Confirmed, err=None)
        self.assertEqual(self.status().json["status"], "pending")
        self.assertEqual(self.account()["booster_rate"], 300)
        self.assertEqual(self.account()["sol_lamports"], 1_800_000_000)

    def test_finalized_failure_refunds_exactly_once_and_preserves_existing_boost(self):
        with service._connect(self.path) as db:
            db.execute("UPDATE wallet_accounts SET booster_rate=6000 WHERE telegram_id='200'")
        self.buy()
        self.finalize({"InstructionError": [0, "synthetic"]})
        self.assertEqual(self.status().json["status"], "failed")
        self.status()
        self.buy()
        self.assertEqual(self.account()["sol_lamports"], 2_000_000_000)
        self.assertEqual(self.account()["booster_rate"], 6000)

    def test_timeout_after_broadcast_never_refunds_and_same_bytes_can_be_rebroadcast(self):
        self.chain.timeout = True
        self.assertEqual(self.buy().json["status"], "pending")
        with service._connect(self.path) as db:
            db.execute("UPDATE wallet_merchant_purchases SET last_broadcast_at=0")
        self.status()
        self.assertEqual(self.chain.sent[0], self.chain.sent[1])
        self.assertEqual(self.account()["sol_lamports"], 1_800_000_000)
        self.finalize()
        self.assertEqual(self.status().json["status"], "confirmed")

    def test_expired_unseen_transaction_refunds_once(self):
        self.buy()
        self.chain.height = 151
        self.assertEqual(self.status().json["status"], "failed")
        self.status()
        self.assertEqual(self.account()["sol_lamports"], 2_000_000_000)

    def test_rpc_failure_during_expiry_check_cannot_refund(self):
        self.buy()
        self.chain.height, self.chain.fail_status = 151, True
        self.assertEqual(self.status().json["status"], "pending")
        self.assertEqual(self.account()["sol_lamports"], 1_800_000_000)

    def test_missing_signer_restores_balance_without_activation(self):
        with patch.dict(os.environ, {"PRIVATE_KEY": ""}):
            self.assertEqual(self.buy().json["status"], "failed")
        self.assertEqual(self.account()["sol_lamports"], 2_000_000_000)
        self.assertEqual(self.account()["booster_rate"], 300)
        self.assertEqual(self.chain.sent, [])

    def test_insufficient_pool_reserves_does_not_spend_other_players_sol(self):
        self.chain.balance = 2_500_000_000  # obligations are 3 SOL plus fee
        self.assertEqual(self.buy().json["status"], "failed")
        self.assertEqual(self.account()["sol_lamports"], 2_000_000_000)
        self.assertEqual(self.chain.sent, [])

    def test_another_player_cannot_query_or_reuse_purchase(self):
        self.buy()
        self.assertEqual(self.status(user="201").status_code, 404)
        self.assertEqual(self.buy(user="201").status_code, 403)
        self.assertEqual(self.account("201")["sol_lamports"], 1_000_000_000)

    def test_pending_purchase_blocks_another_id_and_package_change(self):
        self.buy()
        self.assertEqual(self.buy(event="boots-other").status_code, 409)
        self.assertEqual(self.buy(cost=.5).status_code, 409)
        self.assertEqual(self.account()["sol_lamports"], 1_800_000_000)

    def test_invalid_packages_and_insufficient_balance_never_create_payment(self):
        for cost in ["NaN", "-1", "0.200000001", "Infinity", "1e999999999", None, {}, 0]:
            self.assertEqual(self.buy(cost=cost).status_code, 400)
        self.assertEqual(self.buy(cost=10).status_code, 402)
        self.assertEqual(self.chain.sent, [])
        self.assertEqual(self.account()["sol_lamports"], 2_000_000_000)

    def test_same_amount_for_distinct_players_has_distinct_transaction_signatures(self):
        first = self.buy().json
        second = self.buy(event="boots-player-201", user="201").json
        self.assertNotEqual(first["txHash"], second["txHash"])
        self.assertEqual(len(self.chain.sent), 2)

    def test_concurrent_replay_is_one_reservation_and_one_settlement(self):
        self.buy()
        self.finalize()
        def replay(_):
            with self.app.test_client() as client:
                return self.buy(client=client).json["status"]
        with ThreadPoolExecutor(max_workers=4) as workers:
            self.assertEqual(set(workers.map(replay, range(8))), {"confirmed"})
        self.assertEqual(self.account()["sol_lamports"], 1_800_000_000)
        self.assertEqual(self.account()["booster_rate"], 1500)

    def test_server_state_advertises_release_gate_and_owner_only_pending(self):
        self.buy()
        state = self.post("/wallet/state").json
        other = self.post("/wallet/state", user="201").json
        self.assertTrue(state["merchantPaymentsEnabled"])
        self.assertEqual(state["pendingPurchases"][0]["purchaseId"], "boots-test")
        self.assertEqual(other["pendingPurchases"], [])
        self.assertNotIn("signed_transaction", json.dumps(state))

    def test_restart_recovers_saved_transaction_and_receipt_without_repayment(self):
        purchase = self.buy().json
        self.finalize()
        restarted = Flask("merchant-restart-test")
        service.register_wallet_routes(restarted, self.folder.name)
        response = self.status(client=restarted.test_client()).json
        self.assertEqual(response["status"], "confirmed")
        self.assertEqual(response["txHash"], purchase["txHash"])
        self.assertEqual(len(self.chain.sent), 1)
        state = self.post("/wallet/state", client=restarted.test_client()).json
        self.assertEqual(state["lastMerchantPurchase"]["txHash"], purchase["txHash"])
        self.assertEqual(state["pendingPurchases"], [])

    def test_unauthenticated_request_cannot_charge(self):
        result = self.client.post("/wallet/spend", json={
            "purpose": "booster", "eventId": "bad-auth", "costSol": .2,
        })
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self.chain.sent, [])


if __name__ == "__main__":
    unittest.main()
