"""Encrypted Phantom approvals that return to Telegram, not Phantom's browser."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import base58
from flask import Blueprint, jsonify, redirect, request
from nacl.public import Box, PrivateKey, PublicKey
from nacl.secret import SecretBox


RETURN_URL = "https://t.me/Zer0lifelabs_ai_bot?startapp=wallet_return"
APP_URL = "https://wlodek2106-cyber.github.io/Zer0life_ai/"
PUBLIC_API = "https://zer0life-ai-iz5n.onrender.com"


def register_mobile_routes(app, db_path, connect, authenticate, create_challenge, verify_signature):
    bp = Blueprint("phantom_mobile", __name__)
    with connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS wallet_mobile_sessions (
            session_id TEXT PRIMARY KEY,telegram_id TEXT NOT NULL,purpose TEXT NOT NULL,
            challenge_nonce TEXT,address TEXT,sealed_key TEXT NOT NULL,
            peer_key TEXT,status TEXT NOT NULL,signature TEXT,error TEXT,
            expires_at INTEGER NOT NULL
        )""")

    def vault():
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise PermissionError("Telegram authentication is not configured")
        key = hmac.new(token.encode(), b"zerolife-phantom-mobile-key-v1", hashlib.sha256).digest()
        return SecretBox(key)

    def callback(session_id, stage):
        return os.getenv("PUBLIC_API_URL", PUBLIC_API).rstrip("/") + "/wallet/mobile/callback/" + session_id + "?stage=" + stage

    def valid_session(db, session_id):
        row = db.execute("SELECT * FROM wallet_mobile_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row or row["expires_at"] < int(time.time()):
            raise ValueError("Phantom confirmation expired; start again from Telegram")
        return row

    @bp.post("/wallet/mobile/start")
    def start():
        try:
            data = request.get_json(silent=True) or {}
            user = authenticate(data)
            purpose = data.get("purpose")
            if purpose not in ("link", "withdraw_zrl", "withdraw_sol"):
                raise ValueError("Invalid Phantom approval purpose")
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM wallet_mobile_sessions WHERE expires_at<?", (int(time.time()) - 3600,))
                if db.execute("""SELECT COUNT(*) FROM wallet_mobile_sessions
                              WHERE telegram_id=? AND expires_at>? AND status IN ('connecting','signing')""",
                              (user["id"], int(time.time()))).fetchone()[0] >= 3:
                    raise ValueError("There are already pending Phantom approvals; wait for them to expire")
                nonce, address, expires = None, None, int(time.time()) + 300
                if purpose != "link":
                    challenge = db.execute("SELECT * FROM wallet_challenges WHERE nonce=?",
                                           (str(data.get("nonce", "")),)).fetchone()
                    if (not challenge or challenge["telegram_id"] != user["id"]
                            or challenge["purpose"] != purpose or challenge["used"]
                            or challenge["expires_at"] <= int(time.time())):
                        raise ValueError("Withdrawal challenge expired")
                    account = db.execute("SELECT address FROM wallet_accounts WHERE telegram_id=?",
                                         (user["id"],)).fetchone()
                    if not account or account["address"] != challenge["address"]:
                        raise ValueError("Withdrawal wallet is not linked")
                    nonce, address, expires = challenge["nonce"], challenge["address"], challenge["expires_at"]
                private = PrivateKey.generate()
                session_id = secrets.token_urlsafe(32)
                sealed = base64.b64encode(vault().encrypt(bytes(private))).decode()
                db.execute("""INSERT INTO wallet_mobile_sessions
                    (session_id,telegram_id,purpose,challenge_nonce,address,sealed_key,status,expires_at)
                    VALUES(?,?,?,?,?,?,'connecting',?)""",
                    (session_id, user["id"], purpose, nonce, address, sealed, expires))
                db.commit()
            url = "https://phantom.com/ul/v1/connect?" + urlencode({
                "app_url": APP_URL, "cluster": "mainnet-beta",
                "dapp_encryption_public_key": base58.b58encode(bytes(private.public_key)).decode(),
                "redirect_link": callback(session_id, "connect"),
            })
            return jsonify(success=True, sessionId=session_id, expiresAt=expires, url=url)
        except PermissionError as error:
            return jsonify(success=False, error=str(error)), 401
        except (ValueError, TypeError) as error:
            return jsonify(success=False, error=str(error)), 400
        except Exception:
            app.logger.exception("Phantom mobile start failed")
            return jsonify(success=False, error="Phantom mobile approval could not be started"), 503

    @bp.get("/wallet/mobile/callback/<session_id>")
    def receive(session_id):
        try:
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                row = valid_session(db, session_id)
                stage = request.args.get("stage")
                expected = "connecting" if stage == "connect" else "signing" if stage == "sign" else None
                if row["status"] != expected:
                    raise ValueError("This Phantom response has already been used")
                if request.args.get("errorCode"):
                    db.execute("""UPDATE wallet_mobile_sessions SET status='failed',sealed_key='',
                               error='Phantom approval was cancelled' WHERE session_id=?""", (session_id,))
                    db.commit()
                    return redirect(RETURN_URL)
                for key in ("data", "nonce", "phantom_encryption_public_key"):
                    if len(request.args.get(key, "")) > 12_000:
                        raise ValueError("Phantom response exceeds the allowed size")
                private = PrivateKey(vault().decrypt(base64.b64decode(row["sealed_key"])))
                peer_text = request.args.get("phantom_encryption_public_key") if stage == "connect" else row["peer_key"]
                peer = PublicKey(base58.b58decode(peer_text))
                box = Box(private, peer)
                payload = json.loads(box.decrypt(base58.b58decode(request.args["data"]),
                                                base58.b58decode(request.args["nonce"])))
                if stage == "connect":
                    address = str(payload["public_key"])
                    if len(base58.b58decode(address)) != 32 or not isinstance(payload.get("session"), str):
                        raise ValueError("Invalid Phantom wallet response")
                    if row["purpose"] != "link" and address != row["address"]:
                        raise ValueError("Selected Phantom wallet does not match the linked withdrawal wallet")
                    if row["purpose"] == "link":
                        challenge = create_challenge(db, row["telegram_id"], address, "link", None)
                        nonce, message = challenge["nonce"], challenge["message"]
                    else:
                        challenge = db.execute("SELECT * FROM wallet_challenges WHERE nonce=?",
                                               (row["challenge_nonce"],)).fetchone()
                        if not challenge or challenge["used"] or challenge["expires_at"] <= int(time.time()):
                            raise ValueError("Withdrawal approval expired")
                        nonce, message = challenge["nonce"], challenge["message"]
                    encrypted = box.encrypt(json.dumps({
                        "session": payload["session"], "message": base58.b58encode(message.encode()).decode(),
                        "display": "utf8",
                    }).encode())
                    url = "https://phantom.com/ul/v1/signMessage?" + urlencode({
                        "dapp_encryption_public_key": base58.b58encode(bytes(private.public_key)).decode(),
                        "nonce": base58.b58encode(encrypted.nonce).decode(),
                        "payload": base58.b58encode(encrypted.ciphertext).decode(),
                        "redirect_link": callback(session_id, "sign"),
                    })
                    db.execute("""UPDATE wallet_mobile_sessions SET status='signing',address=?,
                               peer_key=?,challenge_nonce=? WHERE session_id=?""",
                               (address, peer_text, nonce, session_id))
                    db.commit()
                    return redirect(url)
                challenge = db.execute("SELECT * FROM wallet_challenges WHERE nonce=?",
                                       (row["challenge_nonce"],)).fetchone()
                signature = base64.b64encode(base58.b58decode(payload["signature"])).decode()
                if (not challenge or challenge["used"] or challenge["expires_at"] <= int(time.time())
                        or not verify_signature(row["address"], challenge["message"], signature)):
                    raise ValueError("Phantom signature could not be verified")
                db.execute("""UPDATE wallet_mobile_sessions SET status='complete',signature=?,
                           sealed_key='',peer_key=NULL WHERE session_id=?""", (signature, session_id))
                db.commit()
            # The callback never links accounts or pays money by itself. Only the
            # authenticated Telegram client can submit the signature to existing routes.
            return redirect(RETURN_URL)
        except Exception:
            # No callback payload, session token or encrypted private key in logs.
            with connect(db_path) as db:
                db.execute("""UPDATE wallet_mobile_sessions SET status='failed',sealed_key='',
                           error='Phantom response was invalid or expired' WHERE session_id=?
                           AND status IN ('connecting','signing')""", (session_id,))
            return redirect(RETURN_URL)

    @bp.post("/wallet/mobile/status")
    def status():
        try:
            data = request.get_json(silent=True) or {}
            user = authenticate(data)
            with connect(db_path) as db:
                row = valid_session(db, str(data.get("sessionId", "")))
                if row["telegram_id"] != user["id"]:
                    raise PermissionError("This Phantom approval belongs to another Telegram account")
                result = {"success": True, "status": row["status"], "purpose": row["purpose"]}
                if row["status"] == "complete":
                    result.update(nonce=row["challenge_nonce"], signature=row["signature"], address=row["address"])
                if row["status"] == "failed":
                    result["error"] = row["error"]
            return jsonify(result)
        except PermissionError as error:
            return jsonify(success=False, error=str(error)), 403
        except ValueError as error:
            return jsonify(success=False, error=str(error)), 410

    app.register_blueprint(bp)
