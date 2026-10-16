"""Owner-approved recovery of frozen historical records; never auto-trust uploads."""
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import time
from decimal import Decimal, ROUND_DOWN

from flask import Blueprint, jsonify, request


def units(value, scale):
    if isinstance(value, bool):
        raise ValueError("Invalid historical amount")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid historical amount")
    result = int((amount * scale).quantize(Decimal("1"), rounding=ROUND_DOWN))
    if result > 2**63 - 1:
        raise ValueError("Historical amount is too large")
    return result


def recovery_state(db, user_id, admin_id):
    returned = db.execute(
        "SELECT farm_id FROM wallet_legacy_farm_returns WHERE telegram_id=?", (user_id,)
    ).fetchall()
    restored = db.execute(
        "SELECT 1 FROM wallet_recovery_requests WHERE telegram_id=? AND status='approved' LIMIT 1",
        (user_id,),
    ).fetchone()
    return {
        "isRecoveryAdmin": bool(admin_id) and str(user_id) == str(admin_id),
        "restoredLegacyFarmIds": [row["farm_id"] for row in returned],
        "legacyBalanceRestored": bool(restored),
    }


def register_recovery_routes(app, db_path, connect, authenticate, ensure_account, ledger_entry, admin_id, zrl_scale=1_000_000):
    bp = Blueprint("legacy_recovery", __name__)
    with connect(db_path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS wallet_recovery_requests (
                request_id TEXT PRIMARY KEY, telegram_id TEXT NOT NULL,
                username TEXT NOT NULL, digest TEXT NOT NULL,
                snapshot_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL, approved_at INTEGER, approved_by TEXT,
                UNIQUE(telegram_id,digest)
            );
            CREATE TABLE IF NOT EXISTS wallet_legacy_farm_returns (
                telegram_id TEXT NOT NULL, farm_id TEXT NOT NULL,
                request_id TEXT NOT NULL, asset TEXT NOT NULL,
                principal_units INTEGER NOT NULL, returned_at INTEGER NOT NULL,
                PRIMARY KEY(telegram_id,farm_id)
            );
        """)

    def body_and_user(admin=False):
        data = request.get_json(silent=True) or {}
        user = authenticate(data)
        if admin and (not admin_id or str(user["id"]) != str(admin_id)):
            raise PermissionError("Only the configured Telegram administrator can approve recovery")
        return data, user

    def summary(row):
        saved = json.loads(row["snapshot_json"])
        # Old client ZRL farms never debited totalBalance. Their nominal
        # principal is already included in the saved account balance.
        farm_zrl = 0
        nominal_zrl = sum(f["units"] for f in saved["farms"] if f["asset"] == "zrl")
        farm_sol = sum(f["units"] for f in saved["farms"] if f["asset"] == "sol")
        return {
            "requestId": row["request_id"], "digest": row["digest"], "status": row["status"],
            "telegramId": row["telegram_id"], "username": row["username"],
            "balanceZrl": saved["zrl"] / zrl_scale,
            "balanceSol": saved["sol"] / 1_000_000_000,
            "farmZrl": farm_zrl / zrl_scale, "farmSol": farm_sol / 1_000_000_000,
            "farmZrlAlreadyInBalance": nominal_zrl / zrl_scale,
            "totalZrl": (saved["zrl"] + farm_zrl) / zrl_scale,
            "totalSol": (saved["sol"] + farm_sol) / 1_000_000_000,
            "farmCount": len(saved["farms"]), "farms": saved["farms"],
        }

    @bp.post("/wallet/recovery/submit")
    def submit():
        try:
            data, user = body_and_user()
            farms = data.get("farms", [])
            if not isinstance(farms, list) or len(farms) > 256 or len(json.dumps(farms)) > 256_000:
                raise ValueError("Historical farms must be a list of at most 256 saved records")
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                ensure_account(db, user["id"])
                account = db.execute("SELECT * FROM wallet_accounts WHERE telegram_id=?", (user["id"],)).fetchone()
                returned = {r["farm_id"] for r in db.execute(
                    "SELECT farm_id FROM wallet_legacy_farm_returns WHERE telegram_id=?", (user["id"],)
                )}
                known = {r["farm_id"] for r in db.execute(
                    "SELECT farm_id FROM wallet_farms WHERE telegram_id=?", (user["id"],)
                )}
                saved, seen = [], set()
                for farm in farms:
                    if not isinstance(farm, dict):
                        raise ValueError("A historical farm is unreadable; the original device copy was not changed")
                    farm_id = str(farm.get("id", "")).strip()
                    if not farm_id or len(farm_id) > 128 or farm_id in seen:
                        raise ValueError("Historical farm identifiers must be present and unique")
                    seen.add(farm_id)
                    if farm_id in returned:
                        continue
                    if farm_id in known:
                        raise ValueError("A historical record matches an existing verified farm; manual reconciliation is required")
                    asset = str(farm.get("asset", "")).lower()
                    if asset not in ("zrl", "sol") or farm.get("status", "active") != "active":
                        raise ValueError("The historical farm currency or status requires reconciliation")
                    principal = units(farm.get("amount"), zrl_scale if asset == "zrl" else 1_000_000_000)
                    if principal <= 0:
                        raise ValueError("Historical farm principal must be positive")
                    saved.append({
                        "id": farm_id, "asset": asset, "units": principal,
                        "startTime": units(farm.get("startTime", 0), 1),
                        "endTime": units(farm.get("endTime", 0), 1),
                        "claimedZrlUnits": units(farm.get("claimedReward", 0), zrl_scale),
                    })
                snapshot = {"zrl": account["legacy_pending_units"],
                            "sol": account["legacy_pending_sol_lamports"],
                            "farms": sorted(saved, key=lambda farm: farm["id"])}
                if not snapshot["zrl"] and not snapshot["sol"] and not saved:
                    db.commit()
                    return jsonify(success=True, status="nothing_pending")
                encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                existing = db.execute(
                    "SELECT * FROM wallet_recovery_requests WHERE telegram_id=? AND digest=?",
                    (user["id"], digest),
                ).fetchone()
                if not existing:
                    pending = db.execute(
                        "SELECT COUNT(*) FROM wallet_recovery_requests WHERE telegram_id=? AND status='pending'",
                        (user["id"],),
                    ).fetchone()[0]
                    if pending >= 3:
                        raise ValueError("Historical recovery is already awaiting administrator review")
                    request_id = secrets.token_hex(16)
                    db.execute(
                        """INSERT INTO wallet_recovery_requests
                        (request_id,telegram_id,username,digest,snapshot_json,created_at)
                        VALUES(?,?,?,?,?,?)""",
                        (request_id, user["id"], str(user.get("username", "")), digest, encoded, int(time.time())),
                    )
                    existing = db.execute(
                        "SELECT * FROM wallet_recovery_requests WHERE request_id=?", (request_id,)
                    ).fetchone()
                db.commit()
            return jsonify(success=True, recovery=summary(existing))
        except PermissionError as error:
            return jsonify(success=False, error=str(error)), 403
        except (ValueError, TypeError, ArithmeticError) as error:
            return jsonify(success=False, error=str(error)), 400
        except Exception:
            app.logger.exception("historical recovery submission failed")
            return jsonify(success=False, error="Historical records could not be saved"), 503

    @bp.post("/wallet/recovery/admin/list")
    def list_requests():
        try:
            body_and_user(admin=True)
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                # Accounts already saved on the server must not depend on every
                # player reopening Telegram. Device-only farms arrive separately.
                for account in db.execute("""SELECT telegram_id,legacy_pending_units,legacy_pending_sol_lamports
                    FROM wallet_accounts a
                    WHERE (legacy_pending_units>0 OR legacy_pending_sol_lamports>0)
                    AND NOT EXISTS(SELECT 1 FROM wallet_recovery_requests r
                                   WHERE r.telegram_id=a.telegram_id AND r.status='pending')
                    LIMIT 50""").fetchall():
                    snapshot = {"zrl": account["legacy_pending_units"],
                                "sol": account["legacy_pending_sol_lamports"], "farms": []}
                    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
                    digest = hashlib.sha256(encoded.encode()).hexdigest()
                    db.execute("""INSERT OR IGNORE INTO wallet_recovery_requests
                        (request_id,telegram_id,username,digest,snapshot_json,created_at)
                        VALUES(?,?,?,?,?,?)""",
                        (secrets.token_urlsafe(24), account["telegram_id"], "", digest, encoded, int(time.time())))
                db.commit()
                rows = db.execute("""
                    SELECT r.* FROM wallet_recovery_requests r
                    WHERE r.status='pending' AND r.rowid=(
                        SELECT MAX(other.rowid) FROM wallet_recovery_requests other
                        WHERE other.telegram_id=r.telegram_id AND other.status='pending'
                    ) ORDER BY r.created_at LIMIT 50
                """).fetchall()
            return jsonify(success=True, requests=[summary(row) for row in rows])
        except PermissionError as error:
            return jsonify(success=False, error=str(error)), 403

    def backup():
        folder = os.path.join(os.path.dirname(db_path), "recovery-backups")
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="before-approved-recovery-", suffix=".sqlite3", dir=folder)
        os.close(fd)
        source = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
        destination = sqlite3.connect(path)
        try:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Recovery backup integrity check failed")
        finally:
            destination.close()
            source.close()

    @bp.post("/wallet/recovery/admin/apply")
    def apply():
        try:
            data, admin = body_and_user(admin=True)
            selected = data.get("requests")
            if not isinstance(selected, list) or not 1 <= len(selected) <= 50:
                raise ValueError("Select between 1 and 50 frozen recovery requests")
            backup()  # Native SQLite copy; no full-disk rollback and no balance changes yet.
            receipts, seen_users = [], set()
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                for item in selected:
                    if not isinstance(item, dict):
                        raise ValueError("Invalid recovery request selection")
                    row = db.execute("SELECT * FROM wallet_recovery_requests WHERE request_id=?",
                                     (item.get("requestId"),)).fetchone()
                    if not row or row["digest"] != item.get("digest"):
                        raise ValueError("Recovery snapshot changed; reload the request list")
                    if row["telegram_id"] in seen_users:
                        raise ValueError("Select only one snapshot per player")
                    seen_users.add(row["telegram_id"])
                    if row["status"] == "approved":
                        receipts.append(summary(row))
                        continue
                    if row["status"] != "pending":
                        raise ValueError("Recovery request is no longer pending")
                    saved = json.loads(row["snapshot_json"])
                    account = db.execute("SELECT * FROM wallet_accounts WHERE telegram_id=?",
                                         (row["telegram_id"],)).fetchone()
                    if (account["legacy_pending_units"] != saved["zrl"]
                            or account["legacy_pending_sol_lamports"] != saved["sol"]):
                        raise ValueError("Historical balance changed; regenerate and review the snapshot")
                    totals = {"zrl": saved["zrl"], "sol": saved["sol"]}
                    for farm in saved["farms"]:
                        if db.execute("SELECT 1 FROM wallet_farms WHERE telegram_id=? AND farm_id=?",
                                      (row["telegram_id"], farm["id"])).fetchone():
                            raise ValueError("A farm is already in verified accounting")
                        if db.execute("SELECT 1 FROM wallet_legacy_farm_returns WHERE telegram_id=? AND farm_id=?",
                                      (row["telegram_id"], farm["id"])).fetchone():
                            raise ValueError("A farm principal was already returned; reload the snapshot")
                        if farm["asset"] == "sol":
                            totals["sol"] += farm["units"]
                        db.execute("INSERT INTO wallet_legacy_farm_returns VALUES(?,?,?,?,?,?)",
                                   (row["telegram_id"], farm["id"], row["request_id"],
                                    farm["asset"], farm["units"], int(time.time())))
                    existing_zrl = db.execute(
                        "SELECT COALESCE(SUM(delta_units),0) FROM wallet_ledger WHERE telegram_id=?",
                        (row["telegram_id"],),
                    ).fetchone()[0]
                    if totals["zrl"] > 2**63 - 1 - existing_zrl or totals["sol"] > 2**63 - 1 - account["sol_lamports"]:
                        raise ValueError("Recovery total exceeds the accounting range")
                    if totals["zrl"]:
                        ledger_entry(db, row["telegram_id"], "legacy_restoration",
                                     totals["zrl"], "legacy-recovery:" + row["request_id"])
                    db.execute("""UPDATE wallet_accounts SET sol_lamports=sol_lamports+?,
                               legacy_pending_units=0,legacy_pending_sol_lamports=0,legacy_recorded=1
                               WHERE telegram_id=?""", (totals["sol"], row["telegram_id"]))
                    db.execute("""UPDATE wallet_recovery_requests SET status='approved',approved_at=?,approved_by=?
                               WHERE request_id=?""", (int(time.time()), admin["id"], row["request_id"]))
                    db.execute("""UPDATE wallet_recovery_requests SET status='superseded'
                               WHERE telegram_id=? AND request_id<>? AND status='pending'""",
                               (row["telegram_id"], row["request_id"]))
                    receipt = summary(row)
                    receipt["status"] = "approved"
                    receipts.append(receipt)
                db.commit()
            return jsonify(success=True, receipts=receipts)
        except PermissionError as error:
            return jsonify(success=False, error=str(error)), 403
        except (ValueError, TypeError, ArithmeticError, sqlite3.IntegrityError) as error:
            return jsonify(success=False, error=str(error)), 409
        except Exception:
            app.logger.exception("approved historical recovery failed")
            return jsonify(success=False, error="Recovery was not applied; retry after checking the service"), 503

    app.register_blueprint(bp)
