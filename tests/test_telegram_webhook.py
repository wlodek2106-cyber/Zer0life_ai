"""Synthetic Telegram updates only; no real bot credentials, messages or network."""

import asyncio
import ast
from concurrent.futures import Future
import io
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from flask import Flask
from werkzeug.test import EnvironBuilder

from telegram_webhook import (
    MAX_UPDATE_BYTES, WEBHOOK_PATH, configure_telegram_webhook,
    register_telegram_webhook, webhook_secret,
)


class WebhookIngressTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.secret = webhook_secret("synthetic-test-token")
        self.headers = {"X-Telegram-Bot-Api-Secret-Token": self.secret}
        self.validate = Mock(side_effect=lambda data: data)
        self.now = [100.0]
        self.future = Future()
        self.enqueue = Mock(return_value=self.future)
        register_telegram_webhook(
            self.app, self.secret, self.validate, self.enqueue,
            acknowledgement_timeout=0.005, clock=lambda: self.now[0],
            max_inflight=1, max_receipts=2,
        )
        self.client = self.app.test_client()

    def send(self, update_id=1, **kwargs):
        return self.client.post(WEBHOOK_PATH, json={"update_id": update_id},
                                headers=self.headers, **kwargs)

    def test_guests_and_wrong_secrets_never_validate_or_enqueue(self):
        for headers in [{}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}]:
            response = self.client.post(WEBHOOK_PATH, json={"update_id": 1}, headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.headers["X-Zer0Life-Telegram-Webhook"],
                             "authenticated-retry-safe-1")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.validate.assert_not_called()
        self.enqueue.assert_not_called()
        self.assertEqual(self.client.post("/webhook/synthetic-old-token").status_code, 404)

    def test_processing_timeout_coalesces_retry_until_same_work_finishes(self):
        self.assertEqual(self.send().status_code, 503)
        self.assertEqual(self.send().status_code, 503)
        self.enqueue.assert_called_once()
        self.future.set_result(None)
        self.assertEqual(self.send().status_code, 200)
        self.enqueue.assert_called_once()

    def test_full_inflight_queue_rejects_without_starting_more_handlers(self):
        self.assertEqual(self.send(1).status_code, 503)
        self.assertEqual(self.send(2).status_code, 503)
        self.enqueue.assert_called_once()
        self.future.set_result(None)
        replacement = Future()
        replacement.set_result(None)
        self.enqueue.return_value = replacement
        self.assertEqual(self.send(2).status_code, 200)
        self.assertEqual(self.enqueue.call_count, 2)

    def test_dispatch_exception_returns_retry_and_never_leaks_sdk_error(self):
        with self.assertLogs(self.app.logger, "WARNING") as logs:
            self.future.set_exception(RuntimeError("synthetic-sensitive-provider-url"))
            response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "2")
        self.assertNotIn("synthetic-sensitive", response.get_data(as_text=True) + str(logs.output))
        replacement = Future()
        replacement.set_result(None)
        self.enqueue.return_value = replacement
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.enqueue.call_count, 2)

    def test_unavailable_dispatch_loop_is_retryable_not_a_false_acknowledgement(self):
        self.enqueue.side_effect = RuntimeError("synthetic loop unavailable")
        with self.assertLogs(self.app.logger, "WARNING"):
            self.assertEqual(self.send().status_code, 503)

    def test_invalid_payloads_never_enqueue(self):
        for data in [None, [], {}, {"update_id": True}, {"update_id": -1}, {"update_id": "1"}]:
            response = self.client.post(WEBHOOK_PATH, data=json.dumps(data),
                                        content_type="application/json", headers=self.headers)
            self.assertEqual(response.status_code, 400)
        self.validate.side_effect = ValueError("synthetic invalid Telegram model")
        self.assertEqual(self.send().status_code, 400)
        self.assertEqual(self.client.post(WEBHOOK_PATH, data="{", content_type="application/json",
                                         headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(WEBHOOK_PATH, data="plain",
                                         headers=self.headers).status_code, 415)
        self.enqueue.assert_not_called()

    def test_body_limit_applies_to_chunked_requests_too(self):
        raw = b"x" * (MAX_UPDATE_BYTES + 1)
        self.assertEqual(self.client.post(WEBHOOK_PATH, data=raw,
                         content_type="application/json", headers=self.headers).status_code, 413)
        builder = EnvironBuilder(path=WEBHOOK_PATH, method="POST",
                                 content_type="application/json", headers=self.headers)
        env = builder.get_environ()
        env.pop("CONTENT_LENGTH", None)
        env["wsgi.input"] = io.BytesIO(raw)
        env["wsgi.input_terminated"] = True
        self.assertEqual(self.client.open(env).status_code, 413)
        self.enqueue.assert_not_called()

    def test_json_charset_and_missing_bot_configuration(self):
        self.future.set_result(None)
        response = self.client.post(WEBHOOK_PATH, data='{"update_id":1}',
                                    content_type="application/json; charset=utf-8", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        app = Flask("no-bot")
        register_telegram_webhook(app, None, self.validate, self.enqueue)
        self.assertEqual(app.test_client().post(WEBHOOK_PATH).status_code, 503)

    def test_receipts_are_bounded_and_expire_without_changing_running_work(self):
        self.future.set_result(None)
        for update_id in [1, 2, 3]:
            self.assertEqual(self.send(update_id).status_code, 200)
        self.assertEqual(self.enqueue.call_count, 3)
        self.assertEqual(self.send(3).status_code, 200)
        self.assertEqual(self.send(1).status_code, 200)  # Oldest completed receipt was evicted.
        self.assertEqual(self.enqueue.call_count, 4)
        self.now[0] += 601
        self.assertEqual(self.send(3).status_code, 200)
        self.assertEqual(self.enqueue.call_count, 5)

    def test_actual_aiogram_start_update_reaches_dispatcher_once(self):
        bot = Bot(token="123456:" + "a" * 35)  # Valid-format synthetic token, never sent.
        dispatcher = Dispatcher()
        observed = []

        @dispatcher.message(CommandStart())
        async def receive(message):
            observed.append(message.text)

        def enqueue(update):
            result = Future()
            result.set_result(asyncio.run(dispatcher.feed_update(bot, update)))
            return result

        app = Flask("real-dispatcher")
        register_telegram_webhook(
            app, self.secret,
            lambda data: types.Update.model_validate(data, context={"bot": bot}), enqueue,
        )
        payload = {
            "update_id": 123,
            "message": {
                "message_id": 1, "date": 1794336000,
                "from": {"id": 999, "is_bot": False, "first_name": "Synthetic"},
                "chat": {"id": 999, "type": "private"},
                "text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
            },
        }
        with app.test_client() as client:
            for _ in range(2):
                self.assertEqual(client.post(WEBHOOK_PATH, json=payload,
                                            headers=self.headers).status_code, 200)
        self.assertEqual(observed, ["/start"])

    def test_secret_is_stable_domain_separated_and_nonprivileged(self):
        self.assertEqual(webhook_secret("synthetic-test-token"), self.secret)
        self.assertNotEqual(webhook_secret("synthetic-other-token"), self.secret)
        self.assertNotIn("synthetic-test-token", self.secret)
        self.assertEqual(len(self.secret), 64)
        self.assertIsNone(webhook_secret(None))

    def test_bot_wiring_schedules_setup_without_blocking_http_startup(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text()
        tree = ast.parse(source)
        setup = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == "setup_webhook_sync")
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                             and n.func.attr == "result" for n in ast.walk(setup)))
        self.assertNotIn("/webhook/{TOKEN}", source)
        registrations = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name)
                         and n.func.id == "register_telegram_webhook"]
        self.assertEqual(len(registrations), 1)
        self.assertIn("drop_pending_updates=False",
                      (Path(__file__).resolve().parents[1] / "telegram_webhook.py").read_text())


class WebhookRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_error_and_false_result_retry_same_signed_route(self):
        bot = Mock()
        bot.set_webhook = AsyncMock(side_effect=[RuntimeError("synthetic-sensitive"), False, True])
        sleep = AsyncMock()
        logger = Mock()
        self.assertTrue(await configure_telegram_webhook(
            bot, "https://example.test/", "synthetic-header-secret",
            sleep=sleep, max_attempts=3, logger=logger,
        ))
        self.assertEqual(bot.set_webhook.await_count, 3)
        self.assertEqual(sleep.await_count, 2)
        for call in bot.set_webhook.call_args_list:
            self.assertEqual(call.args, ("https://example.test/telegram/webhook",))
            self.assertEqual(call.kwargs, {
                "secret_token": "synthetic-header-secret", "drop_pending_updates": False,
            })
        self.assertNotIn("synthetic-sensitive", str(logger.mock_calls))

    async def test_exhausted_attempts_never_report_success(self):
        bot = Mock(set_webhook=AsyncMock(return_value=False))
        logger = Mock()
        self.assertFalse(await configure_telegram_webhook(
            bot, "https://example.test", "synthetic", sleep=AsyncMock(),
            max_attempts=2, logger=logger,
        ))
        logger.info.assert_not_called()

    async def test_invalid_origin_never_calls_telegram(self):
        bot = Mock(set_webhook=AsyncMock())
        for origin in ["http://example.test", "https://user:pass@example.test",
                       "https://example.test?token=anything", "https://example.test/path"]:
            with self.assertRaises(ValueError):
                await configure_telegram_webhook(bot, origin, "synthetic", max_attempts=1)
        bot.set_webhook.assert_not_called()


if __name__ == "__main__":
    unittest.main()
