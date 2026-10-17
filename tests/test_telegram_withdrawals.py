"""Synthetic treasury, bot delivery and signed Telegram sessions. No real funds."""
import base64
from concurrent.futures import ThreadPoolExecutor
import os
import re
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import base58
from flask import Flask
from solders.keypair import Keypair
from solders.transaction import Transaction
from test_merchant_payments import FakeChain, TOKEN, init_data
import wallet_service as service
import wallet_withdrawal_otp as otp


class TelegramWithdrawals(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.key, self.recipient, self.chain = Keypair(), Keypair(), FakeChain()
        self.chain.get_account_info = lambda *a, **kw: NS(value=None)
        self.chain.get_token_account_balance = lambda *a, **kw: NS(value=NS(amount="1000000000000"))
        self.chain.get_minimum_balance_for_rent_exemption = lambda *a, **kw: NS(value=2039280)
        self.messages = []
        for context in [
            patch.dict(os.environ, {"BOT_TOKEN": TOKEN, "PRIVATE_KEY": base58.b58encode(bytes(self.key)).decode(),
                                    "ZRL_MINT": str(Keypair().pubkey())}),
            patch.object(service, "SHARED_POOL_WALLET", str(self.key.pubkey())),
            patch.object(service, "_rpc_client", return_value=self.chain),
            patch.object(service, "_onchain_balances", return_value={}),
            patch.object(otp.requests, "post", side_effect=self.send),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.app = Flask(__name__)
        service.register_wallet_routes(self.app, folder.name)
        self.client = self.app.test_client()
        self.path = os.path.join(folder.name, "wallet_ledger.sqlite3")
        with service._connect(self.path) as db:
            for user in ["200", "201"]:
                service._ensure_account(db, user)
                db.execute("UPDATE wallet_accounts SET sol_lamports=2000000000 WHERE telegram_id=?", (user,))
                service._ledger_entry(db, user, "test_reward", 20000 * service.ZRL_SCALE, "test-seed:" + user)

    def send(self, url, json, **kwargs):
        self.messages.append(json)
        return NS(raise_for_status=lambda: None, json=lambda: {"ok": True})

    def post(self, route, body=None, user="200", client=None):
        return (client or self.client).post(route, json={"initData": init_data(user), **(body or {})})

    def request(self, asset="SOL", amount="1", event="synthetic-approval-0001", **extra):
        return self.post("/wallet/withdrawals/request", {
            "asset": asset, "amount": amount, "address": str(self.recipient.pubkey()),
            "requestId": event, **extra,
        })

    def code(self):
        return re.search(r"Code: (\d{6})", self.messages[-1]["text"]).group(1)

    def confirm(self, nonce, code=None, **kwargs):
        return self.post("/wallet/withdrawals/confirm", {"nonce": nonce, "code": code or self.code()}, **kwargs)

    def test_unauthenticated_routes_and_schema(self):
        for route in ["request", "confirm", "cancel"]:
            response = self.client.post("/wallet/withdrawals/" + route, json={})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["X-Zer0Life-Wallet-Schema"], "telegram-deposits-reference-1")
        self.assertEqual(self.messages, [])

    def test_both_assets_are_signed_by_treasury_not_by_a_player_wallet(self):
        for asset in ["SOL", "ZRL"]:
            self.recipient = Keypair()
            response = self.request(asset=asset, event="synthetic-approval-" + asset)
            self.assertEqual(response.status_code, 200, response.json)
            nonce = response.json["nonce"]
            self.assertNotIn(self.code(), str(response.json))
            self.assertEqual(self.messages[-1]["chat_id"], "200")
            result = self.confirm(nonce)
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(result.json["status"], "submitted")
            tx = Transaction.from_bytes(self.chain.sent[-1])
            self.assertEqual(tx.message.account_keys[0], self.key.pubkey())
            with service._connect(self.path) as db:
                self.assertIsNone(db.execute("SELECT address FROM wallet_accounts WHERE telegram_id='200'").fetchone()[0])
                self.assertEqual(db.execute("SELECT address FROM wallet_withdrawals WHERE nonce=?", (nonce,)).fetchone()[0],
                                 str(self.recipient.pubkey()))

    def test_request_does_not_debit_and_same_id_does_not_send_another_code(self):
        first, second = self.request(), self.request()
        self.assertEqual(first.json["nonce"], second.json["nonce"])
        self.assertEqual(len(self.messages), 1)
        state = self.post("/wallet/state").json
        self.assertEqual(state["gameSolBalance"], 2)
        self.assertEqual(state["availableZrl"], 20000)
        self.assertEqual(len(state["pendingWithdrawalApprovals"]), 1)
        self.assertEqual(self.chain.sent, [])

    def test_confirmed_address_skips_future_codes_and_new_address_requires_otp(self):
        state = self.post("/wallet/state").json
        self.assertEqual(state["withdrawalMode"], "trusted_destination_v2")
        self.assertIsNone(state["trustedWithdrawalAddress"])

        first = self.request(asset="ZRL", amount="1", event="trusted-address-first")
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.confirm(first.json["nonce"]).status_code, 200)
        with service._connect(self.path) as db:
            trusted = db.execute(
                "SELECT trusted_withdrawal_address FROM wallet_accounts WHERE telegram_id='200'"
            ).fetchone()["trusted_withdrawal_address"]
        self.assertEqual(trusted, str(self.recipient.pubkey()))

        repeat = self.request(asset="SOL", amount="0.5", event="trusted-address-repeat")
        self.assertEqual(repeat.status_code, 200, repeat.json)
        self.assertEqual(repeat.json["status"], "submitted")
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(len(self.chain.sent), 2)

        replacement = Keypair()
        changed = self.post("/wallet/withdrawals/request", {
            "asset": "ZRL", "amount": "1", "address": str(replacement.pubkey()),
            "requestId": "trusted-address-change",
        })
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertIn("nonce", changed.json)
        self.assertNotIn("status", changed.json)
        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.confirm(changed.json["nonce"]).status_code, 200)
        state = self.post("/wallet/state").json
        self.assertEqual(state["trustedWithdrawalAddress"], str(replacement.pubkey()))

    def test_wrong_codes_count_and_lock_without_debiting(self):
        nonce = self.request().json["nonce"]
        wrong = "000000" if self.code() != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.confirm(nonce, wrong).status_code, 403)
        self.assertEqual(self.confirm(nonce).status_code, 429)
        self.assertEqual(self.post("/wallet/state").json["gameSolBalance"], 2)
        self.assertIsNone(self.post("/wallet/state").json["trustedWithdrawalAddress"])
        self.assertEqual(self.chain.sent, [])

    def test_cross_account_code_and_request_cannot_be_used(self):
        nonce = self.request().json["nonce"]
        self.assertEqual(self.confirm(nonce, user="201").status_code, 403)
        self.assertEqual(self.post("/wallet/withdrawals/cancel", {"nonce": nonce}, user="201").status_code, 404)
        collision = self.post("/wallet/withdrawals/request", {
            "asset": "SOL", "amount": "1", "address": str(self.recipient.pubkey()),
            "requestId": "synthetic-approval-0001",
        }, user="201")
        self.assertEqual(collision.status_code, 403)
        self.assertEqual(self.request(requestId="synthetic-approval-0001", userId="201").status_code, 200)
        self.assertEqual(self.post("/wallet/state", user="201").json["pendingWithdrawalApprovals"], [])

    def test_cancel_and_expiry_make_old_code_unusable(self):
        nonce = self.request().json["nonce"]
        self.assertEqual(self.post("/wallet/withdrawals/cancel", {"nonce": nonce}).status_code, 200)
        self.assertEqual(self.confirm(nonce).status_code, 400)
        nonce = self.request(event="synthetic-approval-0002").json["nonce"]
        with service._connect(self.path) as db:
            db.execute("UPDATE wallet_challenges SET expires_at=0 WHERE nonce=?", (nonce,))
        self.assertEqual(self.confirm(nonce).status_code, 400)
        self.assertEqual(self.chain.sent, [])

    def test_duplicate_concurrent_confirmations_send_once(self):
        nonce = self.request().json["nonce"]
        code = self.code()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.confirm(nonce, code, client=self.app.test_client()), range(2)))
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(len(self.chain.sent), 1)
        self.assertEqual(self.post("/wallet/state").json["gameSolBalance"], 1)

    def test_same_amount_recipient_and_blockhash_have_unique_transfer_signatures(self):
        for index in range(2):
            self.recipient = Keypair()
            nonce = self.request(amount="0.1", event=f"synthetic-repeat-{index:04d}").json["nonce"]
            self.assertEqual(self.confirm(nonce).status_code, 200)
        self.assertNotEqual(self.chain.sent[0], self.chain.sent[1])

    def test_balance_and_daily_cap_are_rechecked_at_confirmation(self):
        nonce = self.request(asset="ZRL", amount="10000").json["nonce"]
        self.assertEqual(self.confirm(nonce).status_code, 200)
        self.assertEqual(self.request(asset="ZRL", amount="1", event="synthetic-over-limit").status_code, 429)
        self.recipient = Keypair()
        nonce = self.request(asset="SOL", event="synthetic-cap-sol").json["nonce"]
        with service._connect(self.path) as db:
            db.execute("UPDATE wallet_accounts SET sol_lamports=0 WHERE telegram_id='200'")
        self.assertEqual(self.confirm(nonce).status_code, 402)
        self.assertEqual(len(self.chain.sent), 1)

    def test_invalid_inputs_and_unavailable_bot_never_send_funds(self):
        for change in [{"address":"bad"}, {"address":str(self.key.pubkey())},
                       {"amount":"NaN"}, {"amount":"0.0000000001"}, {"amount":"-1"}, {"asset":"ETH"}]:
            self.assertEqual(self.request(**change).status_code, 400)
        with patch.object(otp.requests, "post", side_effect=TimeoutError("synthetic bot timeout")):
            self.assertEqual(self.request().status_code, 503)
        self.assertEqual(self.post("/wallet/state").json["pendingWithdrawalApprovals"], [])
        self.assertEqual(self.chain.sent, [])

    def test_active_requests_are_owner_scoped_and_rate_limited(self):
        for index in range(3):
            nonce = self.request(event=f"synthetic-rate-{index:04d}").json["nonce"]
            self.post("/wallet/withdrawals/cancel", {"nonce": nonce})
        self.assertEqual(self.request(event="synthetic-rate-fourth").status_code, 429)

    def test_ambiguous_broadcast_stays_reserved_and_replay_never_resends(self):
        nonce = self.request().json["nonce"]
        self.chain.timeout = True
        self.assertEqual(self.confirm(nonce).json["status"], "processing")
        self.assertEqual(self.confirm(nonce).json["status"], "processing")
        self.assertEqual(len(self.chain.sent), 1)
        self.assertEqual(self.post("/wallet/state").json["gameSolBalance"], 1)

    def test_preparation_failure_restores_balance_once(self):
        nonce = self.request().json["nonce"]
        self.chain.balance = 0
        self.assertEqual(self.confirm(nonce).status_code, 503)
        self.assertEqual(self.confirm(nonce).status_code, 400)
        self.assertEqual(self.post("/wallet/state").json["gameSolBalance"], 2)
        self.assertEqual(self.chain.sent, [])

    def test_unfunded_zrl_treasury_never_broadcasts_or_locks_player_funds(self):
        nonce = self.request(asset="ZRL").json["nonce"]
        self.chain.get_token_account_balance = lambda *a, **kw: NS(value=NS(amount="0"))
        self.assertEqual(self.confirm(nonce).status_code, 503)
        self.assertEqual(self.post("/wallet/state").json["availableZrl"], 20000)
        self.assertEqual(self.chain.sent, [])

    def test_blank_rpc_failure_is_explained_and_refunded_only_once(self):
        nonce = self.request(asset="ZRL").json["nonce"]
        class EmptyRpcError(Exception):
            pass
        def broken(*args, **kwargs):
            try:
                root = RuntimeError("synthetic-sensitive-rpc-url")
                root.response = NS(status_code=403)
                raise root
            except Exception as exc:
                raise EmptyRpcError() from exc
        self.chain.get_token_account_balance = broken
        with self.assertLogs(self.app.logger, level="WARNING") as logs:
            result = self.confirm(nonce)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json["errorCode"], "RPC_ACCESS_DENIED")
        self.assertTrue(result.json["balanceRestored"])
        self.assertIn("stage=get_zrl_balance", str(logs.output))
        self.assertIn("http_status=403", str(logs.output))
        self.assertNotIn("sensitive", str(logs.output) + str(result.json))
        self.assertEqual(self.confirm(nonce).status_code, 400)
        self.assertEqual(self.post("/wallet/state").json["availableZrl"], 20000)
        self.assertEqual(self.chain.sent, [])

    def test_finalized_failure_refunds_each_asset_once_but_processed_error_does_not(self):
        for asset in ["SOL", "ZRL"]:
            self.recipient = Keypair()
            nonce = self.request(asset=asset, event="synthetic-fail-" + asset).json["nonce"]
            self.assertEqual(self.confirm(nonce).status_code, 200)
            outcome = {"err": {"InstructionError": [0, "synthetic"]}, "confirmationStatus": "processed"}
            response = NS(raise_for_status=lambda: None, json=lambda: {"result": {"value": [outcome]}})
            with patch.object(service.requests, "post", return_value=response):
                first = self.post("/wallet/withdrawals/status", {"nonce": nonce})
                self.assertEqual(first.json["status"], "submitted")
                outcome["confirmationStatus"] = "finalized"
                self.assertEqual(self.post("/wallet/withdrawals/status", {"nonce": nonce}).json["status"], "failed")
                self.assertEqual(self.post("/wallet/withdrawals/status", {"nonce": nonce}).json["status"], "failed")
        state = self.post("/wallet/state").json
        self.assertEqual(state["availableZrl"], 20000)
        self.assertEqual(state["gameSolBalance"], 2)
