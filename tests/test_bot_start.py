import asyncio
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from telegram_users import TelegramUsers

ROOT = Path(__file__).resolve().parents[1]


class TelegramUserStorageTests(unittest.TestCase):
    def test_concurrent_starts_preserve_every_recipient(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            path.write_text('["777"]')
            stores = [TelegramUsers(path), TelegramUsers(path)]
            with ThreadPoolExecutor(max_workers=4) as workers:
                list(workers.map(lambda n: stores[n % 2].add(n), range(100, 120)))
            self.assertEqual(stores[0].read(), {"777", *(str(n) for n in range(100, 120))})
            self.assertEqual(stores[0].add(100), 21)

    def test_invalid_existing_files_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            for raw in [b"{", b'{"not":"a list"}', b'["777", null]', b'[true]']:
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    TelegramUsers(path).add(888)
                self.assertEqual(path.read_bytes(), raw)

    def test_failed_atomic_replace_preserves_history_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            path.write_text('["777"]')
            with patch("telegram_users.os.replace", side_effect=OSError("synthetic disk failure")):
                with self.assertRaises(OSError):
                    TelegramUsers(path).add(888)
            self.assertEqual(json.loads(path.read_text()), ["777"])
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),
                             ["users.json", "users.json.lock"])

    def test_missing_store_is_initialized_without_touching_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            store = TelegramUsers(path)
            self.assertEqual(store.read(), set())
            self.assertEqual(store.add(777), 1)
            self.assertEqual(store.read(), {"777"})


class BotStartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((ROOT / "bot.py").read_text())
        names = {"cmd_start", "get_start_runner_count"}
        nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name in names]
        for node in nodes:
            node.decorator_list = []
        self.scope = {
            "asyncio": asyncio, "logging": Mock(), "save_user": Mock(return_value=7),
            "re": re, "types": types, "InlineKeyboardButton": InlineKeyboardButton,
            "InlineKeyboardMarkup": InlineKeyboardMarkup, "WebAppInfo": WebAppInfo,
            "APP_URL": "https://example.test/Zer0life_ai/?build=synthetic",
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "isolated-real-start", "exec"),
             self.scope)
        self.message = SimpleNamespace(
            from_user=SimpleNamespace(id=999), text="/start", answer=AsyncMock(),
        )

    async def test_start_returns_real_web_app_launch_button_and_count(self):
        await self.scope["cmd_start"](self.message)
        self.message.answer.assert_awaited_once()
        call = self.message.answer.call_args
        self.assertIn("Total Runners: <b>7</b>", call.args[0])
        self.assertEqual(call.kwargs["parse_mode"], "HTML")
        self.assertEqual(call.kwargs["reply_markup"].inline_keyboard[0][0].web_app.url,
                         self.scope["APP_URL"])

    async def test_start_keeps_button_when_recipient_file_cannot_be_read(self):
        self.scope["save_user"].side_effect = ValueError("synthetic corrupt file")
        await self.scope["cmd_start"](self.message)
        self.assertIn("statistics are temporarily unavailable", self.message.answer.call_args.args[0])
        self.assertIsNotNone(self.message.answer.call_args.kwargs["reply_markup"])
        self.scope["logging"].warning.assert_called_once()

    async def test_start_keeps_referral_handoff(self):
        self.message.text = "/start ref_abcdef1234"
        await self.scope["cmd_start"](self.message)
        url = self.message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0].web_app.url
        self.assertEqual(url, self.scope["APP_URL"] + "&ref=ABCDEF1234")

    async def test_slow_statistics_are_bounded_and_do_not_hide_launch_button(self):
        async def unavailable_count(_):
            raise asyncio.TimeoutError()
        self.scope["asyncio"] = SimpleNamespace(
            to_thread=lambda *args: None,
            wait_for=lambda *args, **kwargs: unavailable_count(None),
        )
        await self.scope["cmd_start"](self.message)
        self.message.answer.assert_awaited_once()
        self.assertIn("statistics are temporarily unavailable", self.message.answer.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
