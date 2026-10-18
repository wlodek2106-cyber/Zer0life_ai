"""Community posts require a valid Telegram session and persist on the server."""

import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from flask import Flask

import wallet_service as service


TOKEN = "synthetic-community-test-token"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV0AAAAAASUVORK5CYII="
)


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


class CommunityFeedTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env_patch = patch.dict(os.environ, {"BOT_TOKEN": TOKEN})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.app = Flask(__name__)
        service.register_wallet_routes(
            self.app,
            self.folder.name,
            admin_telegram_id="999",
        )
        self.client = self.app.test_client()
        self.db_path = os.path.join(self.folder.name, "wallet_ledger.sqlite3")

    def post(self, route, body=None, user="100", signed=True):
        payload = dict(body or {})
        if signed:
            payload["initData"] = init_data(user)
        return self.client.post(route, json=payload)

    def create_post(self, user="100", content="A good run today", **extra):
        response = self.post(
            "/wallet/community/posts",
            {"displayName": f"Runner {user}", "content": content, **extra},
            user=user,
        )
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["post"]

    def test_authentication_and_signed_identity_control_authorship(self):
        denied = self.post(
            "/wallet/community/feed",
            {"telegramId": "100"},
            signed=False,
        )
        self.assertEqual(denied.status_code, 401)

        post = self.create_post(
            user="100",
            content="<script>not html</script>",
            telegramId="999",
            displayName="Chosen Runner",
        )
        own_feed = self.post("/wallet/community/feed", user="100")
        other_feed = self.post("/wallet/community/feed", user="200")
        self.assertEqual(own_feed.status_code, 200)
        self.assertEqual(other_feed.status_code, 200)
        self.assertEqual(own_feed.json["posts"][0]["id"], post["id"])
        self.assertTrue(own_feed.json["posts"][0]["isMine"])
        self.assertFalse(other_feed.json["posts"][0]["isMine"])
        self.assertEqual(other_feed.json["posts"][0]["author"], "Chosen Runner")
        self.assertEqual(other_feed.json["posts"][0]["content"], "<script>not html</script>")
        with service._connect(self.db_path) as db:
            row = db.execute(
                "SELECT telegram_id FROM community_posts WHERE id=?",
                (post["id"],),
            ).fetchone()
        self.assertEqual(row["telegram_id"], "100")

    def test_likes_comments_and_owner_only_delete(self):
        post = self.create_post()
        liked = self.post(
            "/wallet/community/posts/like",
            {"postId": post["id"]},
            user="200",
        )
        self.assertEqual(liked.json, {"success": True, "likedByMe": True, "likeCount": 1})
        unliked = self.post(
            "/wallet/community/posts/like",
            {"postId": post["id"]},
            user="200",
        )
        self.assertFalse(unliked.json["likedByMe"])
        self.assertEqual(unliked.json["likeCount"], 0)

        comment = self.post(
            "/wallet/community/comments",
            {"postId": post["id"], "displayName": "Second Runner", "content": "Nice work."},
            user="200",
        )
        self.assertEqual(comment.status_code, 200, comment.json)
        self.assertEqual(comment.json["comment"]["content"], "Nice work.")
        self.assertEqual(comment.json["commentCount"], 1)

        forbidden = self.post(
            "/wallet/community/posts/delete",
            {"postId": post["id"]},
            user="200",
        )
        self.assertEqual(forbidden.status_code, 403)
        removed = self.post(
            "/wallet/community/posts/delete",
            {"postId": post["id"]},
            user="100",
        )
        self.assertEqual(removed.status_code, 200, removed.json)
        refreshed = self.post("/wallet/community/feed", user="200")
        self.assertEqual(refreshed.json["posts"], [])

    def test_moderator_can_remove_posts_and_deleted_media_is_not_public(self):
        post = self.create_post(
            content="A meme",
            image="data:image/png;base64," + base64.b64encode(PNG_BYTES).decode(),
        )
        media_url = post["mediaUrl"].replace("https://localhost", "")
        image = self.client.get(media_url)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.mimetype, "image/png")
        self.assertEqual(image.headers["X-Content-Type-Options"], "nosniff")
        image.close()

        moderator_feed = self.post("/wallet/community/feed", user="999")
        self.assertTrue(moderator_feed.json["isModerator"])
        removed = self.post(
            "/wallet/community/posts/delete",
            {"postId": post["id"]},
            user="999",
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get(media_url).status_code, 404)

    def test_unsafe_or_mismatched_images_are_rejected(self):
        svg = self.post(
            "/wallet/community/posts",
            {
                "displayName": "Runner",
                "content": "",
                "image": "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode(),
            },
        )
        self.assertEqual(svg.status_code, 400)

        mismatch = self.post(
            "/wallet/community/posts",
            {
                "displayName": "Runner",
                "content": "",
                "image": "data:image/png;base64," + base64.b64encode(b"<html>bad</html>").decode(),
            },
        )
        self.assertEqual(mismatch.status_code, 400)

    def test_write_limits_are_enforced(self):
        for index in range(3):
            self.create_post(content=f"Post {index}")
        limited = self.post(
            "/wallet/community/posts",
            {"displayName": "Runner 100", "content": "Too soon"},
            user="100",
        )
        self.assertEqual(limited.status_code, 429, limited.json)

        valid_post = self.create_post(user="200")
        for index in range(8):
            response = self.post(
                "/wallet/community/comments",
                {
                    "postId": valid_post["id"],
                    "displayName": "Runner",
                    "content": f"Comment {index}",
                },
                user="300",
            )
            self.assertEqual(response.status_code, 200, response.json)
        limited_comment = self.post(
            "/wallet/community/comments",
            {"postId": valid_post["id"], "displayName": "Runner", "content": "Too soon"},
            user="300",
        )
        self.assertEqual(limited_comment.status_code, 429, limited_comment.json)

    def test_posts_are_paginated_newest_first(self):
        now = int(time.time())
        with service._connect(self.db_path) as db:
            db.executemany(
                """INSERT INTO community_posts
                   (telegram_id,display_name,content,created_at)
                   VALUES('100','Runner',?,?)""",
                [(f"Post {index}", now + index) for index in range(21)],
            )

        first_page = self.post("/wallet/community/feed", user="100")
        self.assertEqual(len(first_page.json["posts"]), 20)
        self.assertEqual(first_page.json["posts"][0]["content"], "Post 20")
        self.assertIsNotNone(first_page.json["nextCursor"])
        second_page = self.post(
            "/wallet/community/feed",
            {"cursor": first_page.json["nextCursor"]},
            user="100",
        )
        self.assertEqual(len(second_page.json["posts"]), 1)
        self.assertEqual(second_page.json["posts"][0]["content"], "Post 0")
        self.assertIsNone(second_page.json["nextCursor"])


if __name__ == "__main__":
    unittest.main()
