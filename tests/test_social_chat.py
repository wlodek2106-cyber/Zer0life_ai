"""Telegram-authenticated friend requests and private chats."""

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from flask import Flask

import wallet_service as service


TOKEN = "synthetic-social-test-token"


def init_data(user_id):
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": int(user_id)}),
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret,
        "\n".join(f"{key}={value}" for key, value in sorted(fields.items())).encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


class SocialChatTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env_patch = patch.dict(os.environ, {"BOT_TOKEN": TOKEN})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.app = Flask(__name__)
        service.register_wallet_routes(self.app, self.folder.name, admin_telegram_id="999")
        self.client = self.app.test_client()

    def post(self, route, body=None, user="100", signed=True):
        payload = dict(body or {})
        if signed:
            payload["initData"] = init_data(user)
        return self.client.post(route, json=payload)

    def profile(self, user, name):
        response = self.post(
            "/wallet/social/state",
            {"displayName": name},
            user=user,
        )
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["profile"]

    def accepted_pair(self):
        first = self.profile("100", "Runner Alpha")
        second = self.profile("200", "Runner Beta")
        created = self.post(
            "/wallet/social/requests",
            {
                "targetPublicId": second["publicId"],
                "initialMessage": "Hello from my first run.",
                "clientRequestId": "request-once",
            },
            user="100",
        )
        self.assertEqual(created.status_code, 201, created.json)
        accepted = self.post(
            "/wallet/social/requests/respond",
            {"requestId": created.json["requestId"], "accept": True},
            user="200",
        )
        self.assertEqual(accepted.status_code, 200, accepted.json)
        return first, second, created.json["requestId"]

    def test_profile_bio_is_persistent_public_and_clearable(self):
        profile = self.profile("100", "Runner Alpha")
        self.assertEqual(profile["bio"], "")
        bio = "Road miles and fresh air.\n<script>alert(1)</script>"
        saved = self.post(
            "/wallet/social/profile/bio",
            {"bio": bio, "telegramId": "200"},
            user="100",
        )
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["profile"]["bio"], bio)

        refreshed = self.post("/wallet/social/state", {"displayName": "Runner Alpha"}, user="100")
        self.assertEqual(refreshed.status_code, 200, refreshed.json)
        self.assertEqual(refreshed.json["profile"]["bio"], bio)

        public = self.post(
            "/wallet/community/profile",
            {"publicId": profile["publicId"]},
            user="200",
        )
        self.assertEqual(public.status_code, 200, public.json)
        self.assertEqual(public.json["profile"]["bio"], bio)

        too_long = self.post(
            "/wallet/social/profile/bio",
            {"bio": "x" * 161},
            user="100",
        )
        self.assertEqual(too_long.status_code, 400)
        invalid_type = self.post(
            "/wallet/social/profile/bio",
            {"bio": ["not", "text"]},
            user="100",
        )
        self.assertEqual(invalid_type.status_code, 400)
        missing = self.post("/wallet/social/profile/bio", {}, user="100")
        self.assertEqual(missing.status_code, 400)
        unauthorized = self.post(
            "/wallet/social/profile/bio",
            {"bio": "not signed"},
            signed=False,
        )
        self.assertEqual(unauthorized.status_code, 401)

        cleared = self.post("/wallet/social/profile/bio", {"bio": ""}, user="100")
        self.assertEqual(cleared.status_code, 200, cleared.json)
        self.assertEqual(cleared.json["profile"]["bio"], "")

    def test_existing_social_profile_schema_adds_bio_without_losing_profile(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db_path = os.path.join(folder.name, "wallet_ledger.sqlite3")
        with sqlite3.connect(db_path) as db:
            db.execute(
                """CREATE TABLE social_profiles (
                    telegram_id TEXT PRIMARY KEY,
                    public_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    display_name_key TEXT NOT NULL,
                    created_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    avatar_key TEXT,
                    avatar_mime TEXT,
                    banner_key TEXT,
                    banner_mime TEXT
                )"""
            )
            db.execute(
                """INSERT INTO social_profiles
                   (telegram_id, public_id, display_name, display_name_key,
                    created_at, updated_at)
                   VALUES('100', 'legacy-public-id', 'Legacy Runner',
                          'legacy runner', 1, 1)"""
            )

        app = Flask(__name__)
        service.register_wallet_routes(app, folder.name, admin_telegram_id="999")
        response = app.test_client().post(
            "/wallet/social/state",
            json={"initData": init_data("100"), "displayName": "Legacy Runner"},
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["profile"]["publicId"], "legacy-public-id")
        self.assertEqual(response.json["profile"]["bio"], "")

    def test_signed_identity_controls_profiles_and_requests(self):
        denied = self.post("/wallet/social/state", {"displayName": "Spoof"}, signed=False)
        self.assertEqual(denied.status_code, 401)

        first = self.profile("100", "Runner Alpha")
        second = self.profile("200", "Runner Beta")
        search = self.post(
            "/wallet/social/search",
            {"query": "runner beta", "telegramId": "999"},
            user="100",
        )
        self.assertEqual(search.status_code, 200, search.json)
        self.assertEqual([row["publicId"] for row in search.json["players"]], [second["publicId"]])
        self.assertNotIn("telegramId", search.json["players"][0])

        by_public_id = self.post(
            "/wallet/social/search",
            {"query": second["publicId"][:5]},
            user="100",
        )
        self.assertEqual(by_public_id.status_code, 200, by_public_id.json)
        self.assertEqual(by_public_id.json["players"][0]["publicId"], second["publicId"])

        created = self.post(
            "/wallet/social/requests",
            {
                "targetPublicId": second["publicId"],
                "initialMessage": "Let's connect.",
                "senderId": "999",
                "clientRequestId": "tamper-resistant-request",
            },
            user="100",
        )
        self.assertEqual(created.status_code, 201, created.json)
        with service._connect(os.path.join(self.folder.name, "wallet_ledger.sqlite3")) as db:
            row = db.execute(
                "SELECT sender_id, receiver_id FROM social_requests WHERE request_id=?",
                (created.json["requestId"],),
            ).fetchone()
        self.assertEqual(row["sender_id"], "100")
        self.assertEqual(row["receiver_id"], "200")
        self.assertEqual(first["displayName"], "Runner Alpha")

    def test_accepting_request_creates_friendship_and_first_message(self):
        first = self.profile("100", "Runner Alpha")
        second = self.profile("200", "Runner Beta")
        created = self.post(
            "/wallet/social/requests",
            {
                "targetPublicId": second["publicId"],
                "initialMessage": "Hi, nice to meet you.",
                "clientRequestId": "intro-1",
            },
        )
        self.assertEqual(created.status_code, 201, created.json)

        incoming = self.post("/wallet/social/state", {"displayName": "Runner Beta"}, user="200")
        self.assertEqual(incoming.status_code, 200)
        self.assertEqual(len(incoming.json["incomingRequests"]), 1)
        self.assertEqual(incoming.json["incomingRequests"][0]["message"], "Hi, nice to meet you.")

        not_yet_friends = self.post(
            "/wallet/social/messages",
            {"peerPublicId": first["publicId"]},
            user="200",
        )
        self.assertEqual(not_yet_friends.status_code, 403)

        forbidden_accept = self.post(
            "/wallet/social/requests/respond",
            {"requestId": created.json["requestId"], "accept": True},
            user="300",
        )
        self.assertEqual(forbidden_accept.status_code, 404)

        accepted = self.post(
            "/wallet/social/requests/respond",
            {"requestId": created.json["requestId"], "accept": True},
            user="200",
        )
        self.assertEqual(accepted.status_code, 200, accepted.json)
        self.assertEqual(accepted.json["status"], "accepted")

        first_state = self.post("/wallet/social/state", {"displayName": "Runner Alpha"}, user="100")
        second_state = self.post("/wallet/social/state", {"displayName": "Runner Beta"}, user="200")
        self.assertEqual(first_state.json["friends"][0]["publicId"], second["publicId"])
        self.assertEqual(second_state.json["friends"][0]["publicId"], first["publicId"])
        self.assertEqual(second_state.json["friends"][0]["unreadCount"], 1)

        thread = self.post(
            "/wallet/social/messages",
            {"peerPublicId": first["publicId"]},
            user="200",
        )
        self.assertEqual(thread.status_code, 200, thread.json)
        self.assertEqual(len(thread.json["messages"]), 1)
        self.assertEqual(thread.json["messages"][0]["content"], "Hi, nice to meet you.")
        self.assertFalse(thread.json["messages"][0]["fromMe"])
        self.assertTrue(thread.json["messages"][0]["clientMessageId"])

        read_state = self.post("/wallet/social/state", {"displayName": "Runner Beta"}, user="200")
        self.assertEqual(read_state.json["friends"][0]["unreadCount"], 0)

    def test_private_messages_are_friend_only_and_idempotent(self):
        first, second, _ = self.accepted_pair()
        sent = self.post(
            "/wallet/social/messages/send",
            {
                "peerPublicId": first["publicId"],
                "content": "See you on the road.",
                "clientMessageId": "message-one",
            },
            user="200",
        )
        self.assertEqual(sent.status_code, 201, sent.json)
        repeated = self.post(
            "/wallet/social/messages/send",
            {
                "peerPublicId": first["publicId"],
                "content": "See you on the road.",
                "clientMessageId": "message-one",
            },
            user="200",
        )
        self.assertEqual(repeated.status_code, 200, repeated.json)
        self.assertTrue(repeated.json["duplicate"])

        thread = self.post(
            "/wallet/social/messages",
            {"peerPublicId": second["publicId"]},
            user="100",
        )
        self.assertEqual(thread.status_code, 200, thread.json)
        self.assertEqual(
            [message["content"] for message in thread.json["messages"]],
            ["Hello from my first run.", "See you on the road."],
        )
        self.assertTrue(thread.json["messages"][0]["fromMe"])
        self.assertFalse(thread.json["messages"][1]["fromMe"])

        stranger = self.profile("300", "Runner Gamma")
        forbidden = self.post(
            "/wallet/social/messages/send",
            {
                "peerPublicId": second["publicId"],
                "content": "Not allowed",
                "clientMessageId": "stranger-message",
            },
            user="300",
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertNotEqual(stranger["publicId"], second["publicId"])

    def test_decline_does_not_create_friendship_and_applies_cooldown(self):
        first = self.profile("100", "Runner Alpha")
        second = self.profile("200", "Runner Beta")
        created = self.post(
            "/wallet/social/requests",
            {"targetPublicId": second["publicId"], "clientRequestId": "decline-me"},
        )
        self.assertEqual(created.status_code, 201, created.json)
        declined = self.post(
            "/wallet/social/requests/respond",
            {"requestId": created.json["requestId"], "accept": False},
            user="200",
        )
        self.assertEqual(declined.status_code, 200, declined.json)

        retry = self.post(
            "/wallet/social/requests",
            {"targetPublicId": second["publicId"], "clientRequestId": "try-again"},
        )
        self.assertEqual(retry.status_code, 429, retry.json)
        thread = self.post(
            "/wallet/social/messages",
            {"peerPublicId": first["publicId"]},
            user="200",
        )
        self.assertEqual(thread.status_code, 403)
        self.assertEqual(self.post("/wallet/social/state", user="200").json["friends"], [])

    def test_invalid_and_oversized_messages_are_rejected(self):
        first, second, _ = self.accepted_pair()
        too_long = self.post(
            "/wallet/social/messages/send",
            {
                "peerPublicId": first["publicId"],
                "content": "x" * 1001,
                "clientMessageId": "too-long",
            },
            user="200",
        )
        self.assertEqual(too_long.status_code, 400)
        missing_id = self.post(
            "/wallet/social/messages/send",
            {"peerPublicId": first["publicId"], "content": "No id"},
            user="200",
        )
        self.assertEqual(missing_id.status_code, 400)


if __name__ == "__main__":
    unittest.main()
