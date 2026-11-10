"""No real Telegram delivery: test actual route and bot wiring with synthetic sessions."""

import ast
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from flask import Flask
from werkzeug.test import EnvironBuilder

from admin_broadcast import MAX_BODY_BYTES, register_admin_broadcast
from wallet_service import _verify_telegram_init_data

TOKEN = "synthetic-admin-broadcast-test-token"
ROOT = Path(__file__).resolve().parents[1]


def signed_session(user="999", age=0):
    fields = {
        "auth_date": str(int(time.time()) - age),
        "user": json.dumps({"id": int(user)}),
    }
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        key, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


class AdminBroadcastTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"BOT_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)
        self.app = Flask(__name__)
        self.enqueue = Mock()
        self.count = Mock(return_value=3)
        self.now = [100.0]
        register_admin_broadcast(
            self.app, _verify_telegram_init_data, "999", self.enqueue,
            self.count, clock=lambda: self.now[0],
        )
        self.client = self.app.test_client()

    def post(self, text="Synthetic administrator message", **extra):
        return self.client.post("/broadcast", json={
            "initData": signed_session(), "text": text, **extra,
        })

    def test_guest_forgery_expiry_and_future_sessions_never_queue(self):
        for body in [
            {"text": "guest"},
            {"text": "forged", "initData": "auth_date=1&user=%7B%7D&hash=forged"},
            {"text": "expired", "initData": signed_session(age=7200)},
            {"text": "future", "initData": signed_session(age=-120)},
            {"text": "forged admin flags", "telegramId": "999", "isAdmin": True},
        ]:
            response = self.client.post("/broadcast", json=body)
            self.assertEqual(response.status_code, 401, response.json)
        self.enqueue.assert_not_called()
        self.count.assert_not_called()

    def test_valid_nonadmin_cannot_impersonate_admin_in_body(self):
        response = self.post(initData=signed_session("100"), telegramId="999", isAdmin=True)
        self.assertEqual(response.status_code, 403)
        self.enqueue.assert_not_called()

    def test_admin_receives_queued_not_delivered_response(self):
        response = self.post("  Synthetic message  ")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json, {"success": True, "status": "queued", "recipients": 3})
        self.enqueue.assert_called_once_with("Synthetic message")
        self.assertEqual(response.headers["X-Zer0Life-Admin-API"], "authenticated-broadcast-1")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_malformed_bodies_and_text_never_queue(self):
        for payload in [[], "string", None, 42]:
            response = self.client.post("/broadcast", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(response.status_code, 400)
        for text in ["", " \n ", None, [], {}, "x" * 4097, "\U0001f600" * 2049, "\ud800"]:
            self.assertEqual(self.post(text).status_code, 400)
        response = self.client.post("/broadcast", data="{", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/broadcast", data="text=not-json").status_code, 415)
        self.enqueue.assert_not_called()

    def test_utf16_boundary_accepts_complete_telegram_message(self):
        self.assertEqual(self.post("\U0001f600" * 2048).status_code, 202)

    def test_body_limit_with_and_without_content_length(self):
        payload = json.dumps({"initData": signed_session(), "text": "x" * MAX_BODY_BYTES}).encode()
        response = self.client.post("/broadcast", data=payload, content_type="application/json")
        self.assertEqual(response.status_code, 413)
        builder = EnvironBuilder(path="/broadcast", method="POST", content_type="application/json")
        env = builder.get_environ()
        env.pop("CONTENT_LENGTH", None)
        env["wsgi.input"] = io.BytesIO(payload)
        env["wsgi.input_terminated"] = True
        self.assertEqual(self.client.open(env).status_code, 413)
        self.enqueue.assert_not_called()

    def test_cooldown_and_concurrent_requests_queue_only_once(self):
        def send(_):
            with self.app.test_client() as client:
                return client.post("/broadcast", json={
                    "initData": signed_session(), "text": "synthetic concurrent message",
                }).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(send, range(2))), [202, 429])
        self.enqueue.assert_called_once()
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.now[0] += 60
        self.assertEqual(self.post().status_code, 202)
        self.assertEqual(self.enqueue.call_count, 2)

    def test_queue_failure_does_not_leak_details_or_consume_cooldown(self):
        self.enqueue.side_effect = [RuntimeError("synthetic-sensitive-provider-url"), None]
        with self.assertLogs(self.app.logger, "WARNING") as logs:
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("synthetic-sensitive", response.get_data(as_text=True) + str(logs.output))
        self.assertEqual(self.post().status_code, 202)

    def test_count_failure_does_not_queue(self):
        self.count.side_effect = RuntimeError("synthetic-counter-error")
        with self.assertLogs(self.app.logger, "WARNING"):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.enqueue.assert_not_called()

    def test_authentication_service_failure_is_closed(self):
        app = Flask("failed-auth")
        register_admin_broadcast(app, Mock(side_effect=RuntimeError("synthetic-sensitive")),
                                 "999", self.enqueue, self.count)
        with self.assertLogs(app.logger, "WARNING"):
            response = app.test_client().post("/broadcast", json={"text": "message"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("synthetic-sensitive", response.get_data(as_text=True))
        self.enqueue.assert_not_called()

    def test_bot_wiring_uses_guard_and_closes_unscheduled_coroutine(self):
        tree = ast.parse((ROOT / "bot.py").read_text())
        registrations = [
            node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "register_admin_broadcast"
        ]
        self.assertEqual(len(registrations), 1)
        self.assertEqual([arg.id for arg in registrations[0].args], [
            "api_app", "_verify_telegram_init_data", "ADMIN_TELEGRAM_ID",
            "enqueue_admin_broadcast", "get_total_users",
        ])
        self.assertFalse(any(isinstance(node, ast.FunctionDef) and node.name == "api_broadcast"
                             for node in tree.body))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "enqueue_admin_broadcast")
        coroutine = Mock()
        schedule = Mock(side_effect=RuntimeError("synthetic queue stopped"))
        scope = {
            "bot": object(), "bot_loop": SimpleNamespace(is_running=lambda: True),
            "send_broadcast_to_all": Mock(return_value=coroutine),
            "asyncio": SimpleNamespace(run_coroutine_threadsafe=schedule),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), "isolated-bot-wiring", "exec"), scope)
        with self.assertRaises(RuntimeError):
            scope["enqueue_admin_broadcast"]("synthetic")
        coroutine.close.assert_called_once()
        scope["bot"] = None
        with self.assertRaises(RuntimeError):
            scope["enqueue_admin_broadcast"]("synthetic")
        self.assertEqual(schedule.call_count, 1)


if __name__ == "__main__":
    unittest.main()
