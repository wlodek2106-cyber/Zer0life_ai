"""Authenticated, bounded Telegram ingress with retry-safe in-process dispatch."""

import asyncio
from collections import OrderedDict
from concurrent.futures import TimeoutError as FutureTimeout
import hashlib
import hmac
import json
import logging
import threading
import time
from urllib.parse import urlsplit

from flask import current_app, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

WEBHOOK_PATH = "/telegram/webhook"
MAX_UPDATE_BYTES = 1024 * 1024


def webhook_secret(bot_token):
    """A non-privileged Telegram header secret; never put the bot token in a URL."""
    if not bot_token:
        return None
    return hmac.new(
        bot_token.encode(), b"Zer0Life Run Telegram webhook header v1", hashlib.sha256,
    ).hexdigest()


def register_telegram_webhook(app, secret, validate_update, enqueue, *,
                             acknowledgement_timeout=8, clock=time.monotonic,
                             max_inflight=64, max_receipts=4096):
    # Coalesce retries in this process. Financial idempotency remains in the ledger,
    # not in this bounded, temporary message receipt cache.
    lock = threading.RLock()
    pending = {}
    completed = OrderedDict()

    def retry():
        response = jsonify(success=False, error="Telegram processing is temporarily unavailable")
        response.status_code = 503
        response.headers["Retry-After"] = "2"
        return response

    def finish(update_id, future):
        with lock:
            pending.pop(update_id, None)
            try:
                future.result()
            except BaseException as exc:
                # Future exceptions must be observed, without logging messages,
                # update payloads, SDK URLs or credentials.
                app.logger.warning("Telegram update failed (%s)", type(exc).__name__)
                return
            completed[update_id] = clock()
            while len(completed) > max_receipts:
                completed.popitem(last=False)

    @app.after_request
    def telegram_webhook_release(response):
        if request.path == WEBHOOK_PATH:
            response.headers["X-Zer0Life-Telegram-Webhook"] = "authenticated-retry-safe-1"
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post(WEBHOOK_PATH)
    def telegram_webhook():
        if not secret:
            return retry()
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied.encode(), secret.encode()):
            return jsonify(success=False, error="Invalid Telegram webhook authentication"), 403
        if not request.is_json:
            return jsonify(success=False, error="JSON is required"), 415
        if request.content_length is not None and request.content_length > MAX_UPDATE_BYTES:
            return jsonify(success=False, error="Update is too large"), 413
        try:
            raw = request.stream.read(MAX_UPDATE_BYTES + 1)
            if len(raw) > MAX_UPDATE_BYTES:
                return jsonify(success=False, error="Update is too large"), 413
            data = json.loads(raw)
            if not isinstance(data, dict) or type(data.get("update_id")) is not int:
                raise ValueError("Invalid update envelope")
            if data["update_id"] < 0:
                raise ValueError("Invalid update identifier")
            update = validate_update(data)
        except RequestEntityTooLarge:
            return jsonify(success=False, error="Update is too large"), 413
        except (BadRequest, ValueError, UnicodeError, TypeError):
            return jsonify(success=False, error="Invalid Telegram update"), 400

        update_id = data["update_id"]
        with lock:
            while completed and clock() - next(iter(completed.values())) > 600:
                completed.popitem(last=False)
            if update_id in completed:
                return "", 200
            future = pending.get(update_id)
            if future is None:
                if len(pending) >= max_inflight:
                    return retry()
                try:
                    future = enqueue(update)
                except Exception as exc:
                    current_app.logger.warning("Telegram enqueue failed (%s)", type(exc).__name__)
                    return retry()
                pending[update_id] = future
                future.add_done_callback(lambda done: finish(update_id, done))

        try:
            future.result(timeout=acknowledgement_timeout)
        except FutureTimeout:
            # Keep the same future: a Telegram retry must not start a second handler
            # while the first is still running.
            return retry()
        except Exception:
            return retry()
        return "", 200


async def configure_telegram_webhook(bot, base_url, secret, *, sleep=asyncio.sleep,
                                     max_attempts=None, logger=None):
    """Retry registration without blocking HTTP startup or dropping queued messages."""
    logger = logger or logging.getLogger(__name__)
    parsed = urlsplit(base_url)
    if (not secret or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("Telegram webhook requires a valid HTTPS server origin")
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await asyncio.wait_for(bot.set_webhook(
                base_url.rstrip("/") + WEBHOOK_PATH,
                secret_token=secret,
                drop_pending_updates=False,
            ), timeout=10)
            if not result:
                raise RuntimeError("Telegram did not confirm webhook registration")
            logger.info("Telegram webhook registered successfully.")
            return True
        except Exception as exc:
            logger.warning("Telegram webhook registration failed (attempt=%s, type=%s)",
                           attempt, type(exc).__name__)
            if max_attempts is not None and attempt >= max_attempts:
                return False
            await sleep(min(60, 2 ** min(attempt, 6)))
