"""Historical settlement corrections and explicit unresolved-data regressions."""
import hashlib
import json
from unittest.mock import patch

from recovery_support import DAY, NOW, RecoveryCase, service


class HistoricalRecoveryTests(RecoveryCase):
    def seed_first_generation_approval(self):
        """First recovery paid pending balances and SOL principal, but not ZRL principal/yield."""
        farms = [{
            "id": farm["id"], "asset": farm["asset"],
            "units": 1_000_000_000 if farm["asset"] == "zrl" else 2_000_000,
            "startTime": farm["startTime"], "endTime": farm["endTime"],
            "claimedZrlUnits": int(farm["claimedReward"] * 1_000_000),
        } for farm in self.snapshots["200"]["farms"]]
        saved = json.dumps({"zrl": 100_000_001, "sol": 10_000_000, "farms": farms})
        with service._connect(str(self.path)) as db:
            db.execute("""INSERT INTO wallet_recovery_requests
                (request_id,telegram_id,username,digest,snapshot_json,status,created_at,approved_at,approved_by)
                VALUES('synthetic-first','200','fixture',?,?,'approved',?,?,'100')""",
                (hashlib.sha256(saved.encode()).hexdigest(), saved, NOW, NOW))
            service._ledger_entry(db, "200", "legacy_restoration", 100_000_001, "synthetic-first-credit")
            db.execute("""UPDATE wallet_accounts SET legacy_pending_units=0,
                legacy_pending_sol_lamports=0,sol_lamports=sol_lamports+12000000 WHERE telegram_id='200'""")
            # Reconstruct the actual old registry, not a new empty table.
            db.execute("DROP TABLE wallet_legacy_farm_returns")
            db.execute("""CREATE TABLE wallet_legacy_farm_returns (
                telegram_id TEXT NOT NULL, farm_id TEXT NOT NULL, request_id TEXT NOT NULL,
                asset TEXT NOT NULL, principal_units INTEGER NOT NULL, returned_at INTEGER NOT NULL,
                PRIMARY KEY(telegram_id,farm_id))""")
            for farm in farms:
                db.execute("INSERT INTO wallet_legacy_farm_returns VALUES('200',?,'synthetic-first',?,?,?)",
                           (farm["id"], farm["asset"], farm["units"], NOW))
        self.restart()

    def test_upgrade_of_old_paid_registry_restores_only_missing_principal_and_income(self):
        self.seed_first_generation_approval()
        old = {r["asset"]: r for r in self.rows("wallet_legacy_farm_returns")}
        self.assertEqual(old["sol"]["principal_credited_units"], 2_000_000)
        self.assertEqual(old["zrl"]["principal_credited_units"], 0)
        self.assertTrue(all(row["yield_reviewed"] == 0 for row in old.values()))
        record = self.quote(("200",))[0]
        self.assertEqual(record["balanceZrl"], 0)
        self.assertEqual(record["balanceSol"], 0)
        self.assertEqual(record["farmZrl"], 1000)
        self.assertEqual(record["farmSol"], 0)
        self.assertEqual(record["farmYieldZrl"], 76.5)
        self.assertEqual(self.apply([record]).status_code, 200)
        self.assertEqual(self.totals()["200"], (1_201_500_004, 12_500_001, 0, 0))
        frozen = self.rows("wallet_legacy_farm_returns")
        self.restart()
        self.assertEqual(self.apply([record]).status_code, 200)
        self.assertEqual(self.rows("wallet_legacy_farm_returns"), frozen)
        self.assertEqual(self.totals()["200"], (1_201_500_004, 12_500_001, 0, 0))

    def test_unknown_rate_is_unresolved_not_zero_later_yield_uses_original_closure(self):
        farms = self.snapshots["200"]["farms"]
        rates = [f.pop("dailyRate") for f in farms]
        first = self.quote(("200",))[0]
        self.assertEqual(first["yieldPendingCount"], 2)
        self.assertTrue(all(not f["yieldReviewed"] for f in first["farms"]))
        self.assertEqual(self.apply([first]).status_code, 200)
        self.assertEqual(self.totals()["200"], (1_125_000_004, 12_500_001, 0, 0))
        self.assertEqual(self.post("/wallet/state").json["restoredLegacyFarmIds"], [])
        self.restart()
        for farm, rate in zip(farms, rates):
            farm["dailyRate"] = rate
        with patch("time.time", return_value=NOW + 15 * DAY):
            later = self.quote(("200",))[0]
            self.assertEqual(later["farmZrl"], 0)
            self.assertEqual(later["farmSol"], 0)
            self.assertEqual(later["farmYieldZrl"], 76.5)  # Not 15 extra days' income.
            self.assertTrue(all(f["cutoffMs"] == NOW * 1000 for f in later["farms"]))
            self.assertEqual(self.apply([later]).status_code, 200)
            self.assertEqual(self.apply([later]).status_code, 200)
        self.assertEqual(self.totals()["200"], (1_201_500_004, 12_500_001, 0, 0))

    def test_partially_credited_principal_and_yield_are_subtracted_per_currency(self):
        self.seed_first_generation_approval()
        with service._connect(str(self.path)) as db:
            db.execute("""UPDATE wallet_legacy_farm_returns SET principal_credited_units=600000000,
                yield_credited_units=50000000 WHERE telegram_id='200' AND asset='zrl'""")
            db.execute("""UPDATE wallet_legacy_farm_returns SET principal_credited_units=1000000,
                yield_credited_units=500000 WHERE telegram_id='200' AND asset='sol'""")
            service._ledger_entry(db, "200", "legacy_restoration", 650_500_000, "synthetic-partial")
            db.execute("UPDATE wallet_accounts SET sol_lamports=sol_lamports-1000000 WHERE telegram_id='200'")
        quote = self.quote(("200",))[0]
        self.assertEqual(quote["farmZrl"], 400)
        self.assertEqual(quote["farmSol"], .001)
        self.assertEqual(quote["farmYieldZrl"], 26)
        self.assertEqual(self.apply([quote]).status_code, 200)
        self.assertEqual(self.apply([quote]).status_code, 200)
        self.assertEqual(self.totals()["200"], (1_201_500_004, 12_500_001, 0, 0))

    def test_original_asset_principal_dates_and_claimed_rewards_cannot_be_rewritten(self):
        self.seed_first_generation_approval()
        before = self.totals()
        original = self.snapshots["200"]["farms"]
        changes = {"asset": "sol", "amount": 5000, "startTime": NOW * 1000,
                   "endTime": (NOW + 400 * DAY) * 1000, "claimedReward": 0}
        for field, value in changes.items():
            with self.subTest(field=field):
                farms = json.loads(json.dumps(original))
                farms[0][field] = value
                response = self.post("/wallet/recovery/submit", {"farms": farms})
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(self.totals(), before)

    def test_invalid_duplicates_missing_dates_and_verified_collision_fail_without_credit(self):
        before = self.totals()
        farm = self.snapshots["200"]["farms"][0]
        for invalid in ([farm, farm], [dict(farm, amount="NaN")], [dict(farm, asset="usdc")], [None]):
            self.assertEqual(self.post("/wallet/recovery/submit", {"farms": invalid}).status_code, 400)
        self.assertEqual(self.post("/wallet/recovery/submit", {
            "farms": [dict(farm, startTime=0)]}).status_code, 200)
        quote = self.post("/wallet/recovery/admin/list", user="100")
        self.assertEqual(quote.status_code, 200, quote.json)
        self.assertNotIn("200", [row["telegramId"] for row in quote.json["requests"]])
        self.assertIn("300", [row["telegramId"] for row in quote.json["requests"]])
        error, = quote.json["quoteErrors"]
        self.assertEqual(error["telegramId"], "200")
        self.assertEqual(error["stage"], "quote")
        self.assertIn("dates", error["error"])
        with service._connect(str(self.path)) as db:
            db.execute("UPDATE wallet_recovery_requests SET status='superseded' WHERE status='pending'")
            db.execute("""INSERT INTO wallet_farms
                (farm_id,telegram_id,asset,principal_units,daily_rate,start_time,end_time)
                VALUES(?,'200','zrl',1000000000,.01,?,?)""", (farm["id"], NOW - DAY, NOW + DAY))
        self.assertEqual(self.post("/wallet/recovery/submit", {"farms": [farm]}).status_code, 400)
        self.assertEqual(self.totals(), before)
        self.assertEqual(self.rows("wallet_legacy_farm_returns"), [])

    def test_expired_tampered_or_stale_pending_quote_never_credits(self):
        records = self.quote()
        before = self.totals()
        tampered = [dict(records[0], digest="wrong-digest")]
        self.assertEqual(self.apply(tampered).status_code, 409)
        with patch("time.time", return_value=NOW + 901):
            self.assertEqual(self.apply(records).status_code, 409)
        with service._connect(str(self.path)) as db:
            db.execute("UPDATE wallet_accounts SET legacy_pending_units=legacy_pending_units+1 WHERE telegram_id='200'")
        self.assertEqual(self.apply(records).status_code, 409)
        expected = dict(before)
        expected["200"] = (*before["200"][:2], before["200"][2] + 1, before["200"][3])
        self.assertEqual(self.totals(), expected)
        self.assertEqual(self.rows("wallet_legacy_farm_returns"), [])
