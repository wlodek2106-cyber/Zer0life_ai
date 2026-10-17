"""Offline recovery invariants only; no Phantom, payouts or real player fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from recovery_support import DAY, NOW, ROOT, RecoveryCase, copy_sqlite, make_app, service


class AccountingRecoveryTests(RecoveryCase):
    def test_old_sqlite_upgrade_preserves_all_original_values_and_does_not_infer_no_farms(self):
        with closing(sqlite3.connect(self.archive.as_uri() + "?mode=ro", uri=True)) as old, service._connect(str(self.path)) as new:
            columns = [row[1] for row in old.execute("PRAGMA table_info(wallet_accounts)")]
            query = "SELECT " + ",".join(columns) + " FROM wallet_accounts ORDER BY telegram_id"
            self.assertEqual([tuple(r) for r in old.execute(query)], [tuple(r) for r in new.execute(query)])
            self.assertEqual([tuple(r) for r in old.execute("SELECT * FROM wallet_ledger ORDER BY id")],
                             [tuple(r) for r in new.execute("SELECT * FROM wallet_ledger ORDER BY id")])
        self.assertEqual(self.rows("wallet_farms"), [])
        before = self.totals()
        for user, expected_count in (("200", 2), ("300", 1)):
            state = self.post("/wallet/state", user=user)
            self.assertEqual(state.status_code, 200, state.json)
            self.assertEqual(state.json["farms"], [])
            quoted = self.quote((user,))
            self.assertEqual(quoted[0]["farmCount"], expected_count)
        self.assertEqual(self.totals(), before)
        # The read-only source copy was never migrated or written by the application.
        with closing(sqlite3.connect(self.archive.as_uri() + "?mode=ro", uri=True)) as old:
            self.assertNotIn("wallet_farms", {r[0] for r in old.execute("SELECT name FROM sqlite_master")})

    def test_exact_per_user_currency_dates_claimed_yield_new_rewards_and_restart(self):
        records = self.quote()
        self.assertEqual({r["telegramId"] for r in records}, {"200", "300"})
        expected = {"200": (1000, .002, 76.5), "300": (250, 0, 110)}
        for row in records:
            self.assertEqual((row["farmZrl"], row["farmSol"], row["farmYieldZrl"]),
                             expected[row["telegramId"]])
            original = {f["id"]: f for f in self.snapshots[row["telegramId"]]["farms"]}
            for farm in row["farms"]:
                saved = original[farm["id"]]
                self.assertEqual(farm["asset"], saved["asset"])
                scale = 1_000_000 if saved["asset"] == "zrl" else 1_000_000_000
                self.assertEqual(farm["units"], int(saved["amount"] * scale))
                self.assertEqual(farm["startTime"], saved["startTime"])
                self.assertEqual(farm["endTime"], saved["endTime"])
                self.assertEqual(farm["claimedZrlUnits"], int(saved["claimedReward"] * 1_000_000))
        self.add_new_rewards()  # New earnings arrive after the owner has frozen the quote.
        earned = [r for r in self.rows("wallet_ledger") if r["source"] != "legacy_restoration"]
        self.assertEqual(self.apply(records).status_code, 200)
        self.assert_recovered(new_rewards=True)
        frozen = self.rows("wallet_legacy_farm_returns")
        self.restart()
        for _ in range(2):
            self.assertEqual(self.apply(records).status_code, 200)
            for user in ("200", "300"):
                response = self.post("/wallet/recovery/submit",
                                     {"farms": self.snapshots[user]["farms"]}, user)
                self.assertEqual(response.json.get("status"), "nothing_pending", response.json)
        self.assert_recovered(new_rewards=True)
        self.assertEqual(self.rows("wallet_legacy_farm_returns"), frozen)
        self.assertEqual([r for r in self.rows("wallet_ledger") if r["source"] != "legacy_restoration"], earned)
        self.assertEqual(len([r for r in self.rows("wallet_ledger") if r["source"] == "legacy_restoration"]), 2)
        self.assertFalse(any(r["source"].startswith("affiliate") for r in self.rows("wallet_ledger")))
        for backup in Path(self.folder.name, "recovery-backups").glob("*.sqlite3"):
            with service._connect(str(backup)) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_unreviewed_client_money_and_spoofed_profile_cannot_become_spendable(self):
        before = self.totals()
        # Server archive is authoritative even if another device uploads inflated totals.
        response = self.post("/wallet/legacy", {"amount": 9000000, "solAmount": 9000, "telegramId": "300"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.totals(), before)
        with service._connect(str(self.path)) as db:
            service._ensure_account(db, "400")
        response = self.post("/wallet/legacy", {"amount": 999999, "solAmount": 123, "telegramId": "200"}, "400")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.totals()["400"], (0, 0, 999999000000, 123000000000))
        forged = self.post("/wallet/recovery/submit", {
            "telegramId": "300", "farms": self.snapshots["200"]["farms"]}, "400")
        self.assertEqual(forged.status_code, 200, forged.json)
        self.assertEqual(forged.json["recovery"]["telegramId"], "400")
        quoted = self.quote()
        for user in ("200", "300", "400"):
            self.assertEqual(self.apply(quoted, user=user).status_code, 403)
            self.assertEqual(self.post("/wallet/recovery/admin/list", user=user).status_code, 403)
        unsigned = self.client.post("/wallet/recovery/admin/apply", json={"telegramId": "100"})
        self.assertGreaterEqual(unsigned.status_code, 400)
        self.assertEqual(self.totals()["400"][:2], (0, 0))
        for user in ("200", "300"):
            self.assertEqual(self.totals()[user], before[user])

    def test_same_legacy_id_is_scoped_to_user_and_other_profile_is_not_settled(self):
        records = {r["telegramId"]: r for r in self.quote()}
        other_before = self.totals()["300"]
        self.assertEqual(self.apply([records["200"]]).status_code, 200)
        self.assertEqual(self.totals()["300"], other_before)
        state = self.post("/wallet/state", user="300").json
        self.assertEqual(state["restoredLegacyFarmIds"], [])
        self.assertEqual(state["legacyFarmSettlements"], [])
        self.assertEqual(self.apply([records["300"]]).status_code, 200)
        self.assert_recovered()
        self.assertEqual(sum(r["farm_id"] == "shared-old-id" for r in self.rows("wallet_legacy_farm_returns")), 2)

    def test_existing_verified_farms_and_future_income_match_unrestored_control(self):
        with service._connect(str(self.path)) as db:
            for user, asset, principal, lamports, rate in [
                ("200", "zrl", 200_000_000, 0, .01), ("300", "sol", 0, 1_000_000, 50),
            ]:
                db.execute("""INSERT INTO wallet_farms
                    (farm_id,telegram_id,asset,principal_units,principal_lamports,daily_rate,
                     start_time,end_time,claimed_units)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    ("new-" + user, user, asset, principal, lamports, rate, NOW - DAY, NOW + DAY, 10000))
        original = self.rows("wallet_farms")
        records = self.quote()
        self.add_new_rewards()
        control_dir = Path(self.folder.name) / "control"
        control_dir.mkdir()
        copy_sqlite(self.path, control_dir / "wallet_ledger.sqlite3")
        control = make_app(control_dir).test_client()
        self.assertEqual(self.apply(records).status_code, 200)
        self.assertEqual(self.rows("wallet_farms"), original)
        with patch("time.time", return_value=NOW + 2 * DAY):
            for user in ("200", "300"):
                payload = {"farmId": "new-" + user}
                actual = self.post("/wallet/farms/claim", payload, user)
                expected = self.post("/wallet/farms/claim", payload, user, control)
                self.assertEqual(actual.status_code, 200, actual.json)
                self.assertEqual(expected.status_code, 200, expected.json)
                self.assertEqual(actual.json["reward"], expected.json["reward"])
                self.assertEqual(actual.json["reward"], {"200": 3.99, "300": .09}[user])
        with service._connect(str(control_dir / "wallet_ledger.sqlite3")) as db:
            self.assertEqual(self.rows("wallet_farms"), [dict(r) for r in db.execute("SELECT * FROM wallet_farms ORDER BY rowid")])
            control_events = [tuple(r) for r in db.execute(
                "SELECT telegram_id,source,delta_units,event_id FROM wallet_ledger WHERE source LIKE 'farm_%' ORDER BY id")]
        actual_events = [(r["telegram_id"], r["source"], r["delta_units"], r["event_id"])
                         for r in self.rows("wallet_ledger") if r["source"].startswith("farm_")]
        self.assertEqual(actual_events, control_events)

    def test_database_failure_mid_batch_rolls_back_then_retry_is_exactly_once(self):
        records = self.quote()
        before = {table: self.rows(table) for table in (
            "wallet_accounts", "wallet_ledger", "wallet_legacy_farm_returns", "wallet_recovery_requests")}
        with service._connect(str(self.path)) as db:
            db.execute("""CREATE TRIGGER synthetic_failure BEFORE UPDATE ON wallet_accounts
                WHEN NEW.telegram_id='300' AND NEW.legacy_pending_units=0
                BEGIN SELECT RAISE(ABORT,'synthetic interrupted batch'); END""")
        response = self.apply(sorted(records, key=lambda r: r["telegramId"]))
        self.assertEqual(response.status_code, 409, response.json)
        for table, rows in before.items():
            self.assertEqual(self.rows(table), rows, table)
        with service._connect(str(self.path)) as db:
            db.execute("DROP TRIGGER synthetic_failure")
        self.restart()
        self.assertEqual(self.apply(records).status_code, 200)
        self.assertEqual(self.apply(records).status_code, 200)
        self.assert_recovered()

    def test_process_death_after_uncommitted_credit_and_lost_success_response(self):
        records = sorted(self.quote(), key=lambda r: r["telegramId"])
        before = self.totals()
        script = """
import json, os, socket, sys
from unittest.mock import patch
from recovery_support import make_app, signed_data, service, NOW, TOKEN
def forbidden(*args, **kwargs):
    raise AssertionError('Network forbidden in crash subprocess')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
os.environ['BOT_TOKEN'] = TOKEN
with patch('time.time', return_value=NOW):
    original = service._ledger_entry
    def die_after_credit(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(73)
    # Route registration captures this wrapper. It executes the real INSERT,
    # then dies without exception handling, a COMMIT or normal connection close.
    service._ledger_entry = die_after_credit
    app = make_app(sys.argv[1])
    payload = json.loads(sys.argv[2])
    app.test_client().post('/wallet/recovery/admin/apply',
        json={'initData': signed_data('100'), 'requests': payload})
sys.exit(74)
"""
        selected = [{"requestId": r["requestId"], "digest": r["digest"]} for r in records]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "tests") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        result = subprocess.run([sys.executable, "-c", script, self.folder.name, json.dumps(selected)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.restart()
        self.assertEqual(self.totals(), before)
        self.assertEqual(self.rows("wallet_legacy_farm_returns"), [])
        # Simulate a committed response lost in transport: discard it, reboot, retry.
        self.assertEqual(self.apply(records).status_code, 200)
        self.restart()
        self.assertEqual(self.apply(records).status_code, 200)
        self.assert_recovered()

    def test_simultaneous_owner_retries_do_not_duplicate(self):
        records = self.quote()
        def apply(_):
            with self.app.test_client() as client:
                return self.apply(records, client=client).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(apply, range(2))), [200, 200])
        self.assert_recovered()


if __name__ == "__main__":
    import unittest
    unittest.main()
