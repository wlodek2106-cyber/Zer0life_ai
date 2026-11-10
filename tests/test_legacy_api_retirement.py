"""Import and run the real retired entrypoint without disk/network/worker side effects."""

import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ENTRYPOINT = Path(__file__).resolve().parents[1] / "payout_server.py"
LEGACY_PATHS = [
    "/register", "/update-stats", "/online-stats", "/casino-action",
    "/leaderboard", "/friend/list", "/friend/request", "/friend/accept",
    "/chat/messages", "/chat/send", "/check-deposit", "/verify-deposit",
    "/withdraw", "/buy-booster-7days", "/fortune/spin",
]


class LegacyRetirementTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(ENTRYPOINT))
        self.app = self.module["app"]
        self.client = self.app.test_client()

    def test_all_business_routes_and_aliases_fail_closed(self):
        for prefix in ["", "/api"]:
            for path in LEGACY_PATHS:
                for method in ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]:
                    with self.subTest(prefix=prefix, path=path, method=method):
                        response = self.client.open(prefix + path, method=method, json={
                            "telegramId": "synthetic-victim", "isAdmin": True,
                            "senderId": "synthetic-victim", "amount": -10,
                        })
                        self.assertEqual(response.status_code, 410)
                        self.assertFalse(response.json["success"])
                        self.assertEqual(response.json["status"], "retired")
                        self.assertNotIn("txHash", response.json)
                        self.assertNotIn("messages", response.json)
                        self.assertEqual(response.headers["X-Zer0Life-Legacy-API"],
                                         "retired-no-data-access-1")

    def test_invalid_amounts_bodies_and_unknown_paths_never_reach_business_logic(self):
        for raw in ['{"amount": -10}', '{"amount": NaN}', '{"amount": 1e999}', "{", "not-json"]:
            self.assertEqual(self.client.post("/withdraw", data=raw).status_code, 410)
        for path in ["/", "/static/database.json", "/database.json", "/unrecognized-operation"]:
            self.assertEqual(self.client.get(path).status_code, 410)

    def test_health_is_explicitly_retired_not_successful_game_api(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["success"])
        self.assertEqual(response.json["status"], "retired")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_import_and_requests_preserve_existing_files_and_start_no_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "database.json"
            original = json.dumps({"USER_STATS": {"synthetic": {"balance": 123}}, "CHAT_DB": []}).encode()
            target.write_bytes(original)
            with patch.dict(os.environ, {"ZEROLIFE_DATA_DIR": folder}), \
                    patch("os.makedirs") as mkdir, \
                    patch("shutil.copy2") as copy, \
                    patch("threading.Thread.start") as start, \
                    patch("requests.sessions.Session.request", side_effect=AssertionError("No network")), \
                    patch("builtins.open", side_effect=AssertionError("No database access")):
                module = runpy.run_path(str(ENTRYPOINT))
                client = module["app"].test_client()
                self.assertEqual(client.post("/api/withdraw", json={"amount": -10}).status_code, 410)
                self.assertEqual(client.get("/chat/messages?user1=1&user2=2").status_code, 410)
                mkdir.assert_not_called()
                copy.assert_not_called()
                start.assert_not_called()
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["database.json"])

    def test_direct_launch_exposes_only_the_retired_app(self):
        with patch.dict(os.environ, {"PORT": "8765"}), patch("flask.Flask.run") as run:
            module = runpy.run_path(str(ENTRYPOINT), run_name="__main__")
            run.assert_called_once_with(host="0.0.0.0", port=8765)
            self.assertFalse(hasattr(module["app"], "solana_client"))
            self.assertEqual(module["app"].test_client().post("/api/chat/send").status_code, 410)


if __name__ == "__main__":
    unittest.main()
