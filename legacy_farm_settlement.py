"""Account for historical principal and yield separately, including old settlements."""
from decimal import Decimal, ROUND_DOWN
import json

DAY_MS = Decimal(86_400_000)


def init_settlement(db):
    db.execute("BEGIN IMMEDIATE")
    columns = {row["name"] for row in db.execute("PRAGMA table_info(wallet_legacy_farm_returns)")}
    for name in ("principal_credited_units", "yield_credited_units", "yield_reviewed", "closed_at_ms"):
        if name not in columns:
            db.execute(f"ALTER TABLE wallet_legacy_farm_returns ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
    # The first recovery really paid SOL principal, but did NOT pay ZRL
    # principal or unclaimed yield. Never interpret principal_units as paid.
    db.execute("""UPDATE wallet_legacy_farm_returns
        SET principal_credited_units=CASE WHEN asset='sol' THEN principal_units ELSE 0 END,
            closed_at_ms=returned_at*1000
        WHERE closed_at_ms=0""")
    db.commit()


def saved_rate(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Historical daily rate is invalid")
    rate = Decimal(str(value))
    if not rate.is_finite() or rate < 0 or rate > 1_000_000:
        raise ValueError("Historical daily rate requires reconciliation")
    return str(rate)


def missing_principal_farms(db, user_id):
    """Recover original approved terms even before the old device comes online."""
    farms = []
    for row in db.execute("""SELECT r.*,q.snapshot_json FROM wallet_legacy_farm_returns r
        JOIN wallet_recovery_requests q ON q.request_id=r.request_id
        WHERE r.telegram_id=? AND r.principal_credited_units<r.principal_units""", (user_id,)):
        original = next((f for f in json.loads(row["snapshot_json"])["farms"]
                         if f["id"] == row["farm_id"]), None)
        if original:
            farms.append(original)
    return farms


def validate_original_terms(db, user_id, farm):
    previous = db.execute("""SELECT * FROM wallet_legacy_farm_returns
        WHERE telegram_id=? AND farm_id=?""", (user_id, farm["id"])).fetchone()
    if previous and (previous["asset"] != farm["asset"] or previous["principal_units"] != farm["units"]):
        raise ValueError("Archived farm principal differs from the original approved record")
    if previous:
        original = db.execute("SELECT snapshot_json FROM wallet_recovery_requests WHERE request_id=?",
                              (previous["request_id"],)).fetchone()
        original_farm = next((f for f in json.loads(original["snapshot_json"])["farms"]
                              if f["id"] == farm["id"]), None) if original else None
        if not original_farm or any(original_farm.get(key) != farm.get(key)
                                    for key in ("startTime", "endTime", "claimedZrlUnits")):
            raise ValueError("Archived farm dates or already-claimed rewards differ from the original record")
    return previous


def quote_farms(db, user_id, farms, now_ms, zrl_scale):
    result = []
    for farm in farms:
        previous = validate_original_terms(db, user_id, farm)
        principal_before = previous["principal_credited_units"] if previous else 0
        yield_before = previous["yield_credited_units"] if previous else 0
        cutoff = previous["closed_at_ms"] if previous else now_ms
        rate = saved_rate(farm.get("dailyRate"))
        reviewed = bool(previous and previous["yield_reviewed"])
        due = 0
        if rate is not None:
            start, end = farm["startTime"], farm["endTime"]
            if not start or end <= start:
                raise ValueError("Archived farm dates are insufficient for a yield calculation")
            elapsed = max(0, min(cutoff, end) - start)
            scale = zrl_scale if farm["asset"] == "zrl" else 1_000_000_000
            earned = Decimal(farm["units"]) / scale * Decimal(rate) * elapsed / DAY_MS * zrl_scale
            accrued = int(earned.quantize(Decimal("1"), rounding=ROUND_DOWN))
            if max(0, accrued - farm["claimedZrlUnits"]) > 2**63 - 1:
                raise ValueError("Historical yield exceeds the accounting range")
            due = max(0, accrued - farm["claimedZrlUnits"] - yield_before)
            reviewed = True
        if due > 2**63 - 1:
            raise ValueError("Historical yield exceeds the accounting range")
        result.append({
            **farm, "dailyRate": rate, "principalPaidBefore": principal_before,
            "yieldPaidBefore": yield_before, "principalOwed": max(0, farm["units"] - principal_before),
            "yieldOwed": due, "yieldReviewed": reviewed, "cutoffMs": cutoff,
        })
    return result


def record_settlement(db, user_id, farm, request_id, now):
    previous = db.execute("""SELECT * FROM wallet_legacy_farm_returns
        WHERE telegram_id=? AND farm_id=?""", (user_id, farm["id"])).fetchone()
    before_principal = previous["principal_credited_units"] if previous else 0
    before_yield = previous["yield_credited_units"] if previous else 0
    if (before_principal != farm["principalPaidBefore"] or before_yield != farm["yieldPaidBefore"]
            or (previous and previous["closed_at_ms"] != farm["cutoffMs"])):
        raise ValueError("This farm was settled after the quote; reload the recovery list")
    if previous and (previous["asset"] != farm["asset"] or previous["principal_units"] != farm["units"]):
        raise ValueError("Farm terms differ from the original settlement")
    if previous:
        db.execute("""UPDATE wallet_legacy_farm_returns
            SET principal_credited_units=principal_credited_units+?,
                yield_credited_units=yield_credited_units+?,yield_reviewed=?
            WHERE telegram_id=? AND farm_id=?""",
            (farm["principalOwed"], farm["yieldOwed"], int(farm["yieldReviewed"]), user_id, farm["id"]))
    else:
        db.execute("""INSERT INTO wallet_legacy_farm_returns
            (telegram_id,farm_id,request_id,asset,principal_units,returned_at,
             principal_credited_units,yield_credited_units,yield_reviewed,closed_at_ms)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (user_id, farm["id"], request_id, farm["asset"], farm["units"], now,
             farm["principalOwed"], farm["yieldOwed"], int(farm["yieldReviewed"]), farm["cutoffMs"]))
