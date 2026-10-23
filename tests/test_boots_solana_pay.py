"""Solana Pay Boots purchases must be exact, account-bound, and finalized."""

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

from flask import Flask
from solders.keypair import Keypair

import wallet_service as service

TOKEN = "synthetic-boots-pay-test-token"


def init_data(user):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": int(user)})}
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        key, "\n".join(f"{name}={value}" for name, value in sorted(fields.items())).encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


class FakeRpcResponse:
    def __init__(self, result):
        self.result = result

    def raise_for_status(self):
        return None

    def json(self):
        return {"result": self.result}


class BootsSolanaPayTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env_patch = patch.dict(
            os.environ,
            {"BOT_TOKEN": TOKEN, "SOLANA_RPC_URL": "https://rpc.test/"},
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.app = Flask(__name__)
        service.register_wallet_routes(self.app, self.folder.name)
        self.client = self.app.test_client()
        self.path = os.path.join(self.folder.name, "wallet_ledger.sqlite3")
        with service._connect(self.path) as db:
            for user, balance in [("200", 2_000_000_000), ("201", 1_000_000_000)]:
                service._ensure_account(db, user)
                db.execute(
                    "UPDATE wallet_accounts SET sol_lamports=? WHERE telegram_id=?",
                    (balance, user),
                )
        self.sender = Keypair()
        self.signature = str(self.sender.sign_message(b"synthetic finalized Boots payment"))

    def post(self, route, body=None, user="200", client=None):
        return (client or self.client).post(
            route, json={"initData": init_data(user), **(body or {})}
        )

    def prepare(self, purchase_id="boots-direct-1", cost=.2, user="200", **extra):
        return self.post(
            "/wallet/boots/payments/prepare",
            {"purchaseId": purchase_id, "costSol": cost, **extra},
            user=user,
        )

    def transaction(self, reference, signature=None, lamports=200_000_000,
                    destination=None, include_reference=True, failed=False, signer=True):
        keys = [{"pubkey": str(self.sender.pubkey()), "signer": signer, "writable": True}]
        if include_reference:
            keys.append({"pubkey": reference, "signer": False, "writable": False})
        keys.append({
            "pubkey": destination or "57S7TryAMhxRq5sMSyZxTkyCmzT8tPFKfXjzZwvmv5db",
            "signer": False,
            "writable": True,
        })
        return {
            "slot": 123456,
            "meta": {
                "err": {"InstructionError": [0, "synthetic"]} if failed else None,
                "loadedAddresses": {"writable": [], "readonly": []},
            },
            "transaction": {
                "signatures": [signature or self.signature],
                "message": {
                    "accountKeys": keys,
                    "instructions": [{
                        "program": "system",
                        "parsed": {
                            "type": "transfer",
                            "info": {
                                "source": str(self.sender.pubkey()),
                                "destination": destination or "57S7TryAMhxRq5sMSyZxTkyCmzT8tPFKfXjzZwvmv5db",
                                "lamports": lamports,
                            },
                        },
                    }],
                },
            },
        }

    def verify(self, purchase_id, signature, result, user="200"):
        with patch.object(service.requests, "post", return_value=FakeRpcResponse(result)) as rpc:
            response = self.post(
                "/wallet/boots/payments/verify",
                {"purchaseId": purchase_id, "txSignature": signature},
                user=user,
            )
        return response, rpc

    def account(self, user="200"):
        with service._connect(self.path) as db:
            return dict(db.execute(
                "SELECT * FROM wallet_accounts WHERE telegram_id=?", (user,)
            ).fetchone())

    def test_prepare_returns_solana_pay_uri_with_fixed_package_and_does_not_debit_game_balance(self):
        before = self.account()["sol_lamports"]
        response = self.prepare(
            recipient="attacker-address",
            boosterRate=999999,
        )
        self.assertEqual(response.status_code, 200, response.json)
        invoice = response.json
        self.assertEqual(invoice["recipient"], "57S7TryAMhxRq5sMSyZxTkyCmzT8tPFKfXjzZwvmv5db")
        self.assertEqual(invoice["amountLamports"], 200_000_000)
        self.assertEqual(invoice["boosterRate"], 300)
        self.assertEqual(self.account()["sol_lamports"], before)

        parsed = urlparse(invoice["payUri"])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "solana")
        self.assertEqual(parsed.path, invoice["recipient"])
        self.assertEqual(query["amount"], ["0.2"])
        self.assertEqual(query["reference"], [invoice["reference"]])
        self.assertEqual(len(__import__("base58").b58decode(invoice["reference"])), 32)

    def test_all_five_boots_tariffs_create_exact_solana_pay_invoices(self):
        for index, (cost, expected_lamports) in enumerate((
            ("0.2", 200_000_000),
            ("0.5", 500_000_000),
            ("1", 1_000_000_000),
            ("5", 5_000_000_000),
            ("10", 10_000_000_000),
        )):
            with self.subTest(cost=cost):
                invoice = self.prepare(
                    purchase_id=f"boots-tier-{index}",
                    cost=cost,
                    user=str(300 + index),
                )
                self.assertEqual(invoice.status_code, 200, invoice.json)
                self.assertEqual(invoice.json["amountLamports"], expected_lamports)
                parsed = urlparse(invoice.json["payUri"])
                self.assertEqual(parse_qs(parsed.query)["amount"], [cost])
                self.assertEqual(
                    parse_qs(parsed.query)["reference"],
                    [invoice.json["reference"]],
                )

    def test_finalized_exact_payment_activates_once_and_leaves_game_sol_unchanged(self):
        invoice = self.prepare().json
        before = self.account()["sol_lamports"]
        response, rpc = self.verify(
            invoice["purchaseId"],
            self.signature,
            self.transaction(invoice["reference"]),
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["status"], "confirmed")
        self.assertEqual(response.json["boosterRate"], 1500)
        self.assertGreater(response.json["boosterExpires"], int(time.time() * 1000))
        self.assertEqual(self.account()["sol_lamports"], before)
        rpc_json = rpc.call_args.kwargs["json"]
        self.assertEqual(rpc_json["method"], "getTransaction")
        self.assertEqual(rpc_json["params"][1]["commitment"], "finalized")

        replay, _ = self.verify(
            invoice["purchaseId"],
            self.signature,
            self.transaction(invoice["reference"]),
        )
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(self.account()["booster_rate"], 1500)
        with service._connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM wallet_boots_solana_payments WHERE status='confirmed'"
            ).fetchone()[0], 1)

    def test_non_finalized_transaction_does_not_activate_or_consume_invoice(self):
        invoice = self.prepare().json
        response, _ = self.verify(invoice["purchaseId"], self.signature, None)
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(response.json["status"], "pending")
        self.assertEqual(self.account()["booster_rate"], 300)
        self.assertEqual(self.account()["sol_lamports"], 2_000_000_000)
        self.assertIn("do not pay again", response.json["message"].lower())

    def test_wrong_amount_recipient_reference_signature_or_sender_is_rejected(self):
        cases = [
            ("wrong-amount", {"lamports": 199_000_000}),
            ("wrong-recipient", {"destination": "wrong-recipient"}),
            ("missing-reference", {"include_reference": False}),
            ("failed-tx", {"failed": True}),
            ("unsigned-source", {"signer": False}),
            ("signature-mismatch", {"signature": str(Keypair().sign_message(b"other"))}),
        ]
        for index, (purchase_id, overrides) in enumerate(cases):
            user = str(300 + index)
            with self.subTest(purchase_id=purchase_id):
                invoice = self.prepare(purchase_id=purchase_id, user=user).json
                response, _ = self.verify(
                    invoice["purchaseId"],
                    self.signature,
                    self.transaction(invoice["reference"], **overrides),
                    user=user,
                )
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(self.account(user)["booster_rate"], 300)
                self.assertEqual(self.account(user)["sol_lamports"], 0)

    def test_invoice_is_bound_to_telegram_account_and_only_one_can_be_pending(self):
        first = self.prepare().json
        other = self.prepare(purchase_id="boots-direct-2", user="200")
        self.assertEqual(other.status_code, 409)
        replay_as_other = self.post(
            "/wallet/boots/payments/verify",
            {"purchaseId": first["purchaseId"], "txSignature": self.signature},
            user="201",
        )
        self.assertEqual(replay_as_other.status_code, 404)

    def test_invalid_package_and_replayed_signature_are_rejected(self):
        for cost in ["NaN", "-1", "0.200000001", "11", None]:
            with self.subTest(cost=cost):
                response = self.prepare(purchase_id=f"bad-{str(cost)}", cost=cost)
                self.assertEqual(response.status_code, 400)

        first = self.prepare().json
        response, _ = self.verify(
            first["purchaseId"], self.signature, self.transaction(first["reference"])
        )
        self.assertEqual(response.status_code, 200)
        second = self.prepare(purchase_id="boots-other-player", user="201").json
        replay, _ = self.verify(
            second["purchaseId"], self.signature,
            self.transaction(second["reference"]), user="201",
        )
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(self.account("201")["booster_rate"], 300)

    def test_legacy_game_balance_purchase_route_is_disabled_and_release_marker_is_present(self):
        before = self.account()["sol_lamports"]
        response = self.post(
            "/wallet/spend",
            {"purpose": "booster", "eventId": "old-client", "costSol": .2},
        )
        self.assertEqual(response.status_code, 410, response.json)
        self.assertEqual(self.account()["sol_lamports"], before)
        state = self.post("/wallet/state").json
        self.assertFalse(state["merchantPaymentsEnabled"])
        self.assertTrue(state["bootsSolanaPayEnabled"])
        self.assertEqual(state["pendingBootsPayments"], [])
        self.assertEqual(response.headers["X-Zer0Life-Boots-Pay"], "solana-pay-reference-1")

    def test_unauthenticated_invoice_request_is_rejected(self):
        response = self.client.post(
            "/wallet/boots/payments/prepare",
            json={"purchaseId": "unauthorized", "costSol": .2},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
