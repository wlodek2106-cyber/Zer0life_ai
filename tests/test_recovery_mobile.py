"""Synthetic accounts only. No production keys, requests or transactions."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import base58
from flask import Flask
from nacl.public import Box, PrivateKey, PublicKey
from solders.hash import Hash
from solders.keypair import Keypair

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wallet_service as service


TOKEN = "synthetic-test-token-not-valid-for-telegram"


def signed_data(user):
    values = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": int(user), "username": "Fixture" + str(user)}, separators=(",", ":"))}
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(values.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.env = patch.dict(os.environ, {"BOT_TOKEN": TOKEN, "PRIVATE_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = Flask(__name__)
        service.register_wallet_routes(self.app, self.folder.name, admin_telegram_id="100")
        self.client = self.app.test_client()
        self.path = os.path.join(self.folder.name, "wallet_ledger.sqlite3")
        self.chain = patch.object(service, "_onchain_balances", return_value={
            "solBalance": None, "zrlBalance": None, "zrlConfigured": False,
        })
        self.chain.start()
        self.addCleanup(self.chain.stop)
        with service._connect(self.path) as db:
            service._ensure_account(db, "200")
            service._ledger_entry(db, "200", "fixture", 25_000_000, "fixture-credit")
            db.execute("""UPDATE wallet_accounts SET legacy_pending_units=100000000,
                       legacy_pending_sol_lamports=10000000,legacy_recorded=1,
                       sol_lamports=500000,registration_claimed=1 WHERE telegram_id='200'""")
        self.farms = [
            {"id": "old-zrl", "asset": "zrl", "amount": 1000, "startTime": 1, "endTime": 2, "claimedReward": 5, "dailyRate": .001},
            {"id": "old-sol", "asset": "sol", "amount": .001, "startTime": 1, "endTime": 2, "claimedReward": 2, "dailyRate": .001},
        ]

    def post(self, path, body=None, user="200", client=None):
        return (client or self.client).post(path, json={"initData": signed_data(user), **(body or {})})

    def state(self, user="200"):
        response = self.post("/wallet/state", user=user)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_embedded_wallet_state_uses_verified_telegram_identity_and_separate_balances(self):
        first = self.state("200")
        second = self.post("/wallet/state", {"telegramId": "200"}, user="201").json
        self.assertEqual(first["telegramId"], "200")
        self.assertEqual(second["telegramId"], "201")
        self.assertEqual(first["availableZrl"], 25)
        self.assertEqual(second["availableZrl"], 0)
        self.assertNotEqual(first["referralCode"], second["referralCode"])
        self.assertEqual(first["gameSolBalance"], .0005)
        self.assertEqual(second["gameSolBalance"], 0)

    def test_release_can_be_verified_without_exposing_a_player_session(self):
        response = self.client.post("/wallet/state", json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["X-Zer0Life-Wallet-Schema"], "telegram-deposits-reference-1")
        self.assertNotIn("availableZrl", response.json)

    def test_embedded_wallet_link_requires_real_signature_and_cannot_be_stolen_or_rebound(self):
        key = Keypair()
        address = str(key.pubkey())
        challenge = self.post("/wallet/challenge", {"purpose": "link", "address": address}).json
        proof = base64.b64encode(bytes(key.sign_message(challenge["message"].encode()))).decode()
        payload = {"nonce": challenge["nonce"], "signature": proof}
        stolen = self.post("/wallet/link", payload, user="201")
        self.assertEqual(stolen.status_code, 400)
        self.assertFalse(self.state("201")["walletAddress"])
        self.assertEqual(self.post("/wallet/link", payload).status_code, 200)
        self.assertEqual(self.state()["walletAddress"], address)
        self.assertEqual(self.state()["availableZrl"], 25)
        self.assertEqual(self.post("/wallet/link", payload).status_code, 400)
        other_key = Keypair()
        replacement = self.post("/wallet/challenge", {"purpose": "link", "address": str(other_key.pubkey())}).json
        other_proof = base64.b64encode(bytes(other_key.sign_message(replacement["message"].encode()))).decode()
        refused = self.post("/wallet/link", {"nonce": replacement["nonce"], "signature": other_proof})
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(self.state()["walletAddress"], address)

    def submit(self, farms=None):
        response = self.post("/wallet/recovery/submit", {"farms": self.farms if farms is None else farms})
        self.assertEqual(response.status_code, 200, response.json)
        quoted = self.post("/wallet/recovery/admin/list", user="100")
        self.assertEqual(quoted.status_code, 200, quoted.json)
        return next(row for row in quoted.json["requests"] if row["telegramId"] == "200")

    def approve(self, record, user="100", client=None):
        return self.post("/wallet/recovery/admin/apply", {
            "requests": [{"requestId": record["requestId"], "digest": record["digest"]}],
        }, user=user, client=client)

    def historical_farms(self, now_ms):
        for farm, rate, claimed in zip(self.farms, [.01, 100], [25, .2]):
            farm.update(startTime=now_ms-10*86400000, endTime=now_ms+20*86400000,
                        dailyRate=rate, claimedReward=claimed)

    def seed_incomplete_old_settlement(self, now_ms):
        from legacy_farm_settlement import init_settlement
        from wallet_recovery import units
        normalized = [
            {"id": f["id"], "asset": f["asset"], "units": units(f["amount"], 1000000 if f["asset"] == "zrl" else 1000000000),
             "startTime": f["startTime"], "endTime": f["endTime"], "claimedZrlUnits": units(f["claimedReward"], 1000000)}
            for f in self.farms
        ]
        old = json.dumps({"zrl": 100000000, "sol": 10000000, "farms": normalized})
        with service._connect(self.path) as db:
            db.execute("""INSERT INTO wallet_recovery_requests
                (request_id,telegram_id,username,digest,snapshot_json,status,created_at,approved_at,approved_by)
                VALUES('old-approved','200','Fixture200',?,?,'approved',?,?,'100')""",
                (hashlib.sha256(old.encode()).hexdigest(), old, now_ms//1000, now_ms//1000))
            service._ledger_entry(db, "200", "legacy_restoration", 100000000, "legacy-recovery:old-approved")
            db.execute("""UPDATE wallet_accounts SET legacy_pending_units=0,legacy_pending_sol_lamports=0,
                       sol_lamports=sol_lamports+11000000 WHERE telegram_id='200'""")
            for farm in normalized:
                db.execute("""INSERT INTO wallet_legacy_farm_returns
                    (telegram_id,farm_id,request_id,asset,principal_units,returned_at) VALUES('200',?,'old-approved',?,?,?)""",
                    (farm["id"], farm["asset"], farm["units"], now_ms//1000))
            init_settlement(db)

    def test_corrects_already_closed_zrl_principal_and_yield_without_double_sol_or_claimed_rewards(self):
        now_ms = int(time.time()) * 1000
        self.historical_farms(now_ms)
        self.seed_incomplete_old_settlement(now_ms)
        with patch("wallet_recovery.time.time", return_value=now_ms/1000):
            record = self.submit()
            self.assertEqual(record["balanceZrl"], 0)
            self.assertEqual(record["farmZrl"], 1000)
            self.assertEqual(record["farmSol"], 0)
            self.assertEqual(record["farmYieldZrl"], 75.8)
            self.assertEqual(self.approve(record).status_code, 200)
            self.assertEqual(self.approve(record).status_code, 200)
        state = self.state()
        self.assertEqual(state["availableZrl"], 1200.8)
        self.assertAlmostEqual(state["gameSolBalance"], .0115)
        self.assertEqual(set(state["restoredLegacyFarmIds"]), {"old-zrl", "old-sol"})
        self.assertEqual(len(state["legacyFarmSettlements"]), 2)
        self.assertTrue(all(row["principalReturned"] and row["yieldReviewed"] for row in state["legacyFarmSettlements"]))

    def test_corrects_principal_without_device_and_later_adds_only_missing_yield(self):
        now_ms = int(time.time()) * 1000
        self.historical_farms(now_ms)
        self.seed_incomplete_old_settlement(now_ms)
        with patch("wallet_recovery.time.time", return_value=now_ms/1000):
            rows = self.post("/wallet/recovery/admin/list", user="100").json["requests"]
            record = next(row for row in rows if row["telegramId"] == "200")
            self.assertEqual(record["farmZrl"], 1000)
            self.assertGreater(record["yieldPendingCount"], 0)
            self.assertEqual(self.approve(record).status_code, 200)
            self.assertEqual(self.state()["availableZrl"], 1125)
            self.assertEqual(self.state()["restoredLegacyFarmIds"], [])
            record = self.submit()
            self.assertEqual(record["farmZrl"], 0)
            self.assertEqual(record["farmSol"], 0)
            self.assertEqual(record["farmYieldZrl"], 75.8)
            self.assertEqual(self.approve(record).status_code, 200)
        self.assertEqual(self.state()["availableZrl"], 1200.8)
        self.assertAlmostEqual(self.state()["gameSolBalance"], .0115)

    def test_old_cached_recovery_quote_is_rejected_before_any_credit(self):
        raw = self.post("/wallet/recovery/submit", {"farms": self.farms}).json["recovery"]
        self.assertEqual(self.approve(raw).status_code, 409)
        self.assertEqual(self.state()["availableZrl"], 25)
        self.assertEqual(self.state()["legacyPendingZrl"], 100)

    def test_changed_historical_claim_or_dates_cannot_change_a_closed_farm(self):
        now_ms = int(time.time()) * 1000
        self.historical_farms(now_ms)
        self.seed_incomplete_old_settlement(now_ms)
        self.farms[0]["claimedReward"] = 0
        response = self.post("/wallet/recovery/submit", {"farms": self.farms})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state()["availableZrl"], 125)

    def test_old_closed_farms_stop_accruing_at_the_original_close_not_the_correction(self):
        from legacy_farm_settlement import missing_principal_farms, quote_farms
        now_ms = int(time.time()) * 1000
        self.historical_farms(now_ms)
        self.seed_incomplete_old_settlement(now_ms)
        with service._connect(self.path) as db:
            terms = missing_principal_farms(db, "200")
            terms[0]["dailyRate"] = ".01"
            before = quote_farms(db, "200", terms, now_ms, 1000000)
            after = quote_farms(db, "200", terms, now_ms+15*86400000, 1000000)
        self.assertEqual(before, after)
        self.assertEqual(before[0]["yieldOwed"], 75000000)

    def test_upload_is_not_a_credit_and_only_owner_can_approve(self):
        record = self.submit()
        self.assertEqual(self.state()["availableZrl"], 25)
        self.assertEqual(self.approve(record, user="200").status_code, 403)
        self.assertEqual(self.post("/wallet/recovery/admin/list").status_code, 403)
        self.assertEqual(self.state()["legacyPendingZrl"], 100)

    def test_owner_can_restore_server_archives_without_waiting_for_players_to_reopen(self):
        response = self.post("/wallet/recovery/admin/list", user="100")
        self.assertEqual(response.status_code, 200)
        record = next(row for row in response.json["requests"] if row["telegramId"] == "200")
        self.assertEqual(self.approve(record).status_code, 200)
        self.assertEqual(self.state()["availableZrl"], 125)
        # Farms are device-local and are reviewed separately when submitted.
        farm_record = self.submit()
        self.assertEqual(farm_record["balanceZrl"], 0)
        self.assertEqual(self.approve(farm_record).status_code, 200)
        self.assertAlmostEqual(self.state()["gameSolBalance"], .0115)

    def test_restore_returns_zrl_principal_once_and_preserves_existing_balances(self):
        record = self.submit()
        self.assertEqual(record["farmZrl"], 1000)
        self.assertEqual(record["farmYieldZrl"], 0)
        self.assertEqual(self.approve(record).status_code, 200)
        self.assertEqual(self.approve(record).status_code, 200)
        state = self.state()
        self.assertEqual(state["availableZrl"], 1125)
        self.assertAlmostEqual(state["gameSolBalance"], .0115)
        self.assertEqual(state["legacyPendingZrl"], 0)
        self.assertEqual(state["legacyPendingSol"], 0)
        self.assertEqual(set(state["restoredLegacyFarmIds"]), {"old-zrl", "old-sol"})
        self.assertTrue(state["legacyBalanceRestored"])
        self.assertEqual(self.post("/wallet/recovery/submit", {"farms": self.farms}).json["status"], "nothing_pending")
        with service._connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM wallet_ledger WHERE source='legacy_restoration'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM wallet_ledger WHERE source LIKE 'affiliate%'").fetchone()[0], 0)
        self.assertTrue(list(Path(self.folder.name, "recovery-backups").glob("*.sqlite3")))

    def test_concurrent_approval_cannot_duplicate_a_refund(self):
        record = self.submit()
        def apply(_):
            with self.app.test_client() as client:
                return self.approve(record, client=client).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(apply, range(2))), [200, 200])
        self.assertEqual(self.state()["availableZrl"], 1125)
        self.assertAlmostEqual(self.state()["gameSolBalance"], .0115)

    def test_stale_or_tampered_snapshot_rolls_back_all_funds(self):
        record = self.submit()
        forged = dict(record, digest="changed")
        self.assertEqual(self.approve(forged).status_code, 409)
        with service._connect(self.path) as db:
            db.execute("UPDATE wallet_accounts SET legacy_pending_units=101000000 WHERE telegram_id='200'")
        self.assertEqual(self.approve(record).status_code, 409)
        self.assertEqual(self.state()["availableZrl"], 25)
        with service._connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM wallet_legacy_farm_returns").fetchone()[0], 0)

    def test_a_failed_second_account_rolls_back_the_entire_batch(self):
        first = self.submit()
        with service._connect(self.path) as db:
            service._ensure_account(db, "300")
            db.execute("UPDATE wallet_accounts SET legacy_pending_units=50000000 WHERE telegram_id='300'")
        second = self.post("/wallet/recovery/submit", {"farms": []}, user="300").json["recovery"]
        response = self.post("/wallet/recovery/admin/apply", {"requests": [
            {"requestId": first["requestId"], "digest": first["digest"]},
            {"requestId": second["requestId"], "digest": "wrong-digest"},
        ]}, user="100")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state()["availableZrl"], 25)
        self.assertEqual(self.state()["legacyPendingZrl"], 100)
        self.assertAlmostEqual(self.state()["gameSolBalance"], .0005)

    def test_zrl_daily_withdrawal_cap_is_not_bypassed_by_restoration(self):
        wallet = self.link_mobile()
        with service._connect(self.path) as db:
            service._ledger_entry(db, "200", "fixture", 20_000_000_000, "fixture-large-credit")
        challenge = self.post("/wallet/challenge", {
            "purpose": "withdraw_zrl", "address": str(wallet.pubkey()), "amount": 10001,
        }).json
        signature = base64.b64encode(bytes(wallet.sign_message(challenge["message"].encode()))).decode()
        response = self.post("/wallet/withdraw", {"nonce": challenge["nonce"], "signature": signature})
        self.assertGreaterEqual(response.status_code, 400)
        self.assertIn("limit", response.json["error"].lower())
        self.assertEqual(self.state()["availableZrl"], 20025)

    def test_duplicates_invalid_amounts_and_verified_farms_do_not_get_recovered(self):
        for farms in [[self.farms[0]] * 2, [{"id": "bad", "amount": "NaN", "asset": "sol"}], [None]]:
            self.assertEqual(self.post("/wallet/recovery/submit", {"farms": farms}).status_code, 400)
        with service._connect(self.path) as db:
            db.execute("""INSERT INTO wallet_farms
                (farm_id,telegram_id,asset,principal_units,principal_lamports,daily_rate,start_time,end_time)
                VALUES('old-sol','200','sol',0,1000000,0,1,2)""")
        self.assertEqual(self.post("/wallet/recovery/submit", {"farms": self.farms}).status_code, 400)
        self.assertEqual(self.state()["availableZrl"], 25)

    def mobile_roundtrip(self, purpose="link", nonce=None, signing_wallet=None, complete=True):
        wallet = signing_wallet or Keypair()
        response = self.post("/wallet/mobile/start", {"purpose": purpose, "nonce": nonce})
        self.assertEqual(response.status_code, 200, response.json)
        started = response.json
        options = {k: v[0] for k, v in parse_qs(urlsplit(started["url"]).query).items()}
        peer = PrivateKey.generate()
        box = Box(peer, PublicKey(base58.b58decode(options["dapp_encryption_public_key"])))
        encrypted = box.encrypt(json.dumps({"public_key": str(wallet.pubkey()), "session": "synthetic-session"}).encode())
        target = urlsplit(options["redirect_link"])
        connected = self.client.get(target.path + "?" + target.query + "&" + urlencode({
            "phantom_encryption_public_key": base58.b58encode(bytes(peer.public_key)).decode(),
            "nonce": base58.b58encode(encrypted.nonce).decode(),
            "data": base58.b58encode(encrypted.ciphertext).decode(),
        }))
        if not complete:
            return wallet, started, connected
        self.assertEqual(connected.status_code, 302)
        self.assertIn("phantom.com/ul/v1/signMessage", connected.location)
        sign = {k: v[0] for k, v in parse_qs(urlsplit(connected.location).query).items()}
        decoded = json.loads(box.decrypt(base58.b58decode(sign["payload"]), base58.b58decode(sign["nonce"])))
        message = base58.b58decode(decoded["message"])
        signature = wallet.sign_message(message)
        encrypted = box.encrypt(json.dumps({"signature": base58.b58encode(bytes(signature)).decode()}).encode())
        target = urlsplit(sign["redirect_link"])
        signed = self.client.get(target.path + "?" + target.query + "&" + urlencode({
            "nonce": base58.b58encode(encrypted.nonce).decode(),
            "data": base58.b58encode(encrypted.ciphertext).decode(),
        }))
        self.assertIn("t.me/Zer0lifelabs_ai_bot", signed.location)
        approved = self.post("/wallet/mobile/status", {"sessionId": started["sessionId"]})
        self.assertEqual(approved.json["status"], "complete", approved.json)
        return wallet, started, approved.json

    def link_mobile(self):
        wallet, started, approved = self.mobile_roundtrip()
        self.assertEqual(self.post("/wallet/link", approved).status_code, 200)
        return wallet

    def test_encrypted_mobile_link_is_bound_to_telegram_and_requires_existing_link_route(self):
        wallet, started, approved = self.mobile_roundtrip()
        self.assertIsNone(self.state()["walletAddress"])
        self.assertEqual(self.post("/wallet/mobile/status", {"sessionId": started["sessionId"]}, user="300").status_code, 403)
        self.assertEqual(self.post("/wallet/link", approved).status_code, 200)
        self.assertEqual(self.state()["walletAddress"], str(wallet.pubkey()))
        with service._connect(self.path) as db:
            self.assertEqual(db.execute("SELECT sealed_key FROM wallet_mobile_sessions").fetchone()[0], "")

    def test_mobile_cancellation_and_wrong_wallet_do_not_move_money(self):
        wallet = self.link_mobile()
        challenge = self.post("/wallet/challenge", {"purpose": "withdraw_sol", "address": str(wallet.pubkey()), "amount": .0001}).json
        _, started, response = self.mobile_roundtrip("withdraw_sol", challenge["nonce"], complete=False)
        self.assertIn("t.me/Zer0lifelabs_ai_bot", response.location)
        self.assertEqual(self.post("/wallet/mobile/status", {"sessionId": started["sessionId"]}).json["status"], "failed")
        self.assertAlmostEqual(self.state()["gameSolBalance"], .0005)

    def test_both_assets_require_valid_signature_and_local_failure_refunds_once(self):
        wallet = self.link_mobile()
        for asset, amount in [("zrl", 5), ("sol", .0001)]:
            before = self.state()
            challenge = self.post("/wallet/challenge", {"purpose": "withdraw_" + asset, "address": str(wallet.pubkey()), "amount": amount}).json
            invalid = self.post("/wallet/withdraw", {"nonce": challenge["nonce"], "signature": ""})
            self.assertEqual(invalid.status_code, 401)
            _, _, approved = self.mobile_roundtrip("withdraw_" + asset, challenge["nonce"], wallet)
            failed = self.post("/wallet/withdraw", approved)
            self.assertEqual(failed.status_code, 503)
            after = self.state()
            self.assertEqual(before["availableZrl"], after["availableZrl"])
            self.assertEqual(before["gameSolBalance"], after["gameSolBalance"])
            self.assertEqual(self.post("/wallet/withdraw", approved).status_code, 400)

    def test_rpc_timeout_after_broadcast_does_not_refund_or_send_twice(self):
        wallet = self.link_mobile()
        challenge = self.post("/wallet/challenge", {"purpose": "withdraw_sol", "address": str(wallet.pubkey()), "amount": .0001}).json
        signature = base64.b64encode(bytes(wallet.sign_message(challenge["message"].encode()))).decode()
        treasury = Keypair()
        class Rpc:
            calls = 0
            def get_balance(self, _): return SimpleNamespace(value=1_000_000_000)
            def get_latest_blockhash(self): return SimpleNamespace(value=SimpleNamespace(blockhash=Hash.default()))
            def send_raw_transaction(inner, _):
                inner.calls += 1
                raise TimeoutError("Synthetic post-broadcast timeout")
        rpc = Rpc()
        with patch.object(service, "SHARED_POOL_WALLET", str(treasury.pubkey())), \
             patch.object(service, "_rpc_client", return_value=rpc), \
             patch.dict(os.environ, {"PRIVATE_KEY": base58.b58encode(bytes(treasury)).decode()}):
            body = {"nonce": challenge["nonce"], "signature": signature}
            response = self.post("/wallet/withdraw", body)
            self.assertEqual(response.status_code, 202, response.json)
            self.assertEqual(self.post("/wallet/withdraw", body).json["status"], "processing")
            self.assertEqual(rpc.calls, 1)
            self.assertAlmostEqual(self.state()["gameSolBalance"], .0004)
        chain = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "result": {"value": [{"err": {"InstructionError": [0, "fixture"]}, "confirmationStatus": "finalized"}]}
        })
        with patch.object(service.requests, "post", return_value=chain):
            for _ in range(2):
                self.assertEqual(self.post("/wallet/withdrawals/status", {"nonce": challenge["nonce"]}).json["status"], "failed")
        self.assertAlmostEqual(self.state()["gameSolBalance"], .0005)

    def test_submitted_transaction_is_reconciled_and_failed_chain_result_refunds_once(self):
        with service._connect(self.path) as db:
            db.execute("UPDATE wallet_accounts SET sol_lamports=400000 WHERE telegram_id='200'")
            db.execute("""INSERT INTO wallet_withdrawals
                (nonce,telegram_id,address,asset,amount_units,status,tx_signature,created_at)
                VALUES('fixture-submitted','200','fixture','SOL',100000,'submitted','fixture-signature',?)""",
                (int(time.time()),))
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "result": {"value": [{"err": {"InstructionError": [0, "fixture"]}, "confirmationStatus": "finalized"}]}
        })
        with patch.object(service.requests, "post", return_value=response):
            for _ in range(2):
                result = self.post("/wallet/withdrawals/status", {"nonce": "fixture-submitted"})
                self.assertEqual(result.json["status"], "failed")
        self.assertAlmostEqual(self.state()["gameSolBalance"], .0005)


if __name__ == "__main__":
    unittest.main()
