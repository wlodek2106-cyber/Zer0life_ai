"""Authenticated SOL deposits must be finalized on-chain and bound to a unique reference."""
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from urllib.parse import urlencode

from flask import Flask

import wallet_service as service

TOKEN = "synthetic-deposit-test-token"


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


class DepositTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env_patch = patch.dict(os.environ, {"BOT_TOKEN": TOKEN, "SOLANA_RPC_URL": "https://rpc.test/"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.app = Flask(__name__)
        service.register_wallet_routes(self.app, self.folder.name)
        self.client = self.app.test_client()
        self.path = os.path.join(self.folder.name, "wallet_ledger.sqlite3")
        self.pool = service.SHARED_POOL_WALLET

    def post(self, route, body=None, user="200"):
        return self.client.post(
            route,
            json={"initData": init_data(user), **(body or {})},
        )

    def get_reference(self, user="200"):
        response = self.post("/wallet/deposit/reference", user=user)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["address"], self.pool)
        return response.json["reference"]

    def transaction(self, reference, signature="synthetic-tx-signature", lamports=1_250_000_000,
                    destination=None, include_reference=True, failure=None, loaded_reference=False):
        account_keys = [{"pubkey": "synthetic-source-address"}]
        if include_reference and not loaded_reference:
            account_keys.append({"pubkey": reference})
        meta = {
            "err": failure,
            "loadedAddresses": {
                "writable": [],
                "readonly": [reference] if include_reference and loaded_reference else [],
            },
        }
        return {
            "slot": 123456,
            "meta": meta,
            "transaction": {
                "signatures": [signature],
                "message": {
                    "accountKeys": account_keys,
                    "instructions": [{
                        "program": "system",
                        "parsed": {
                            "type": "transfer",
                            "info": {
                                "source": "synthetic-source-address",
                                "destination": destination or self.pool,
                                "lamports": lamports,
                            },
                        },
                    }],
                },
            },
        }

    def verify(self, signature, reference, result, user="200"):
        with patch.object(service.requests, "post", return_value=FakeRpcResponse(result)) as rpc:
            response = self.post(
                "/wallet/deposit/verify",
                {"txSignature": signature, "depositReference": reference},
                user=user,
            )
        return response, rpc

    def balance(self, user="200"):
        with service._connect(self.path) as db:
            row = db.execute(
                "SELECT sol_lamports, address FROM wallet_accounts WHERE telegram_id = ?",
                (user,),
            ).fetchone()
            return dict(row)

    def test_reference_is_account_bound_and_reused_until_a_deposit_is_credited(self):
        first = self.get_reference("200")
        self.assertEqual(self.get_reference("200"), first)
        second = self.get_reference("201")
        self.assertNotEqual(first, second)
        self.assertEqual(len(__import__("base58").b58decode(first)), 32)
        self.assertIsNone(self.balance("200")["address"])

        response, _ = self.verify(
            "signature-one", first, self.transaction(first, signature="signature-one")
        )
        self.assertEqual(response.status_code, 200, response.json)
        next_reference = self.get_reference("200")
        self.assertNotEqual(next_reference, first)

    def test_finalized_transaction_with_reference_credits_exact_chain_amount_without_linked_wallet(self):
        reference = self.get_reference()
        result = self.transaction(reference)
        response, rpc = self.verify("synthetic-tx-signature", reference, result)

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["depositedSOL"], 1.25)
        self.assertEqual(response.json["gameSolBalance"], 1.25)
        self.assertEqual(response.json["depositReference"], reference)
        self.assertEqual(self.balance()["sol_lamports"], 1_250_000_000)
        self.assertIsNone(self.balance()["address"])
        rpc_json = rpc.call_args.kwargs["json"]
        self.assertEqual(rpc_json["method"], "getTransaction")
        self.assertEqual(rpc_json["params"][1]["commitment"], "finalized")
        with service._connect(self.path) as db:
            deposit = db.execute("SELECT * FROM wallet_deposits").fetchone()
            reference_row = db.execute(
                "SELECT * FROM wallet_deposit_references WHERE reference = ?",
                (reference,),
            ).fetchone()
            self.assertEqual(deposit["deposit_reference"], reference)
            self.assertEqual(reference_row["credited_signature"], "synthetic-tx-signature")

    def test_exactly_ten_sol_is_within_the_per_transaction_limit(self):
        reference = self.get_reference()
        response, _ = self.verify(
            "maximum-valid-signature",
            reference,
            self.transaction(
                reference,
                signature="maximum-valid-signature",
                lamports=service.MAX_DEPOSIT_LAMPORTS,
            ),
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["depositedSOL"], 10)

    def test_reference_in_v0_loaded_addresses_is_accepted(self):
        reference = self.get_reference()
        response, _ = self.verify(
            "loaded-address-signature",
            reference,
            self.transaction(
                reference, signature="loaded-address-signature", loaded_reference=True
            ),
        )
        self.assertEqual(response.status_code, 200, response.json)

    def test_reference_from_another_telegram_account_cannot_claim_transaction(self):
        first_reference = self.get_reference("200")
        self.get_reference("201")
        response, _ = self.verify(
            "other-account-signature",
            first_reference,
            self.transaction(first_reference, signature="other-account-signature"),
            user="201",
        )
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(self.balance("201")["sol_lamports"], 0)

    def test_missing_reference_wrong_recipient_failed_tx_and_signature_mismatch_are_rejected(self):
        reference = self.get_reference()
        invalid_results = [
            self.transaction(reference, signature="missing-reference", include_reference=False),
            self.transaction(reference, signature="wrong-recipient", destination="wrong-pool"),
            self.transaction(reference, signature="failed-transaction", failure={"InstructionError": [0, "failed"]}),
            self.transaction(reference, signature="different-signature"),
        ]
        signatures = [
            "missing-reference",
            "wrong-recipient",
            "failed-transaction",
            "requested-signature-not-in-result",
        ]
        for signature, result in zip(signatures, invalid_results):
            with self.subTest(signature=signature):
                response, _ = self.verify(signature, reference, result)
                self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.balance()["sol_lamports"], 0)

    def test_zero_or_over_limit_transfer_is_rejected(self):
        reference = self.get_reference()
        for signature, amount in [
            ("zero-amount", 0),
            ("over-limit", service.MAX_DEPOSIT_LAMPORTS + 1),
        ]:
            with self.subTest(signature=signature):
                response, _ = self.verify(
                    signature,
                    reference,
                    self.transaction(reference, signature=signature, lamports=amount),
                )
                self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.balance()["sol_lamports"], 0)

    def test_duplicate_tx_and_reuse_of_credited_reference_never_credit_twice(self):
        reference = self.get_reference()
        result = self.transaction(reference, signature="once-only-signature")
        first, _ = self.verify("once-only-signature", reference, result)
        duplicate, duplicate_rpc = self.verify("once-only-signature", reference, result)
        reused_reference, _ = self.verify(
            "another-signature", reference,
            self.transaction(reference, signature="another-signature"),
        )

        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(duplicate.status_code, 409, duplicate.json)
        self.assertEqual(duplicate_rpc.call_count, 0)
        self.assertEqual(reused_reference.status_code, 409, reused_reference.json)
        self.assertEqual(self.balance()["sol_lamports"], 1_250_000_000)

    def test_unauthenticated_reference_and_verification_requests_are_rejected(self):
        reference_response = self.client.post("/wallet/deposit/reference", json={})
        verify_response = self.client.post(
            "/wallet/deposit/verify",
            json={"txSignature": "sig", "depositReference": "reference"},
        )
        self.assertEqual(reference_response.status_code, 401)
        self.assertEqual(verify_response.status_code, 401)

    def test_existing_deposit_table_gets_additive_reference_column(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "wallet_ledger.sqlite3")
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE wallet_deposits "
                "(signature TEXT PRIMARY KEY, telegram_id TEXT NOT NULL, "
                "lamports INTEGER NOT NULL, created_at INTEGER NOT NULL)"
            )
            db.execute(
                "INSERT INTO wallet_deposits VALUES "
                "('old-signature', '200', 500, 1)"
            )

        service._init_db(path)
        with service._connect(path) as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(wallet_deposits)")}
            existing = db.execute(
                "SELECT signature, telegram_id, lamports FROM wallet_deposits"
            ).fetchone()
        self.assertIn("deposit_reference", columns)
        self.assertEqual(tuple(existing), ("old-signature", "200", 500))


if __name__ == "__main__":
    unittest.main()
