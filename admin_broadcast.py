"""Authenticated, bounded manual broadcasts; no Telegram/network side effects on import."""

import json
import math
import threading
import time

from flask import current_app, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

MAX_BODY_BYTES = 64 * 1024
MAX_TEXT_UTF16_UNITS = 4096
BROADCAST_COOLDOWN_SECONDS = 60


def register_admin_broadcast(app, verify_session, admin_id, enqueue, recipient_count, clock=time.monotonic):
    lock = threading.Lock()
    last_queued_at = None

    def error(message, status):
        return jsonify(success=False, error=message), status

    @app.after_request
    def broadcast_release(response):
        if request.path == "/broadcast":
            response.headers["X-Zer0Life-Admin-API"] = "authenticated-broadcast-1"
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/broadcast")
    def admin_broadcast():
        nonlocal last_queued_at
        if not request.is_json:
            return error("A JSON request is required", 415)
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            return error("Broadcast request is too large", 413)
        try:
            # Bound the read even for a chunked body without Content-Length.
            raw = request.stream.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                return error("Broadcast request is too large", 413)
            data = json.loads(raw)
        except RequestEntityTooLarge:
            return error("Broadcast request is too large", 413)
        except (BadRequest, ValueError, UnicodeError):
            return error("Invalid JSON request", 400)
        if not isinstance(data, dict):
            return error("A JSON object is required", 400)

        try:
            user = verify_session(data.get("initData"))
        except PermissionError:
            return error("A valid Telegram administrator session is required", 401)
        except Exception as exc:
            current_app.logger.warning("Broadcast authentication unavailable (%s)", type(exc).__name__)
            return error("Broadcast authentication is temporarily unavailable", 503)
        if not admin_id or not isinstance(user, dict) or str(user.get("id", "")) != str(admin_id):
            return error("Administrator access is required", 403)

        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return error("Broadcast text is required", 400)
        text = text.strip()
        try:
            if len(text.encode("utf-16-le")) // 2 > MAX_TEXT_UTF16_UNITS:
                return error("Broadcast text exceeds the Telegram message limit", 400)
        except UnicodeError:
            return error("Broadcast text contains invalid characters", 400)

        if not lock.acquire(blocking=False):
            response, status = error("Another broadcast is being queued; try later", 429)
            response.headers["Retry-After"] = str(BROADCAST_COOLDOWN_SECONDS)
            return response, status
        try:
            now = clock()
            if last_queued_at is not None and now - last_queued_at < BROADCAST_COOLDOWN_SECONDS:
                response, status = error("Please wait before sending another broadcast", 429)
                response.headers["Retry-After"] = str(max(
                    1, math.ceil(BROADCAST_COOLDOWN_SECONDS - (now - last_queued_at)),
                ))
                return response, status
            try:
                # Count first, so a failed count never produces an unacknowledged send.
                count = int(recipient_count())
                enqueue(text)
            except Exception as exc:
                current_app.logger.warning("Broadcast could not be queued (%s)", type(exc).__name__)
                return error("The broadcast could not be queued; try again later", 503)
            last_queued_at = clock()
            return jsonify(success=True, status="queued", recipients=count), 202
        finally:
            lock.release()
