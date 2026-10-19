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

    def test_profiles_follow_counts_and_following_feed(self):
        post = self.create_post(user="100", content="First lap complete.")
        target_public_id = post["authorPublicId"]
        followed = self.post(
            "/wallet/community/follow",
            {"targetPublicId": target_public_id, "follow": True},
            user="200",
        )
        self.assertEqual(followed.status_code, 200, followed.json)
        self.assertEqual(followed.json["followerCount"], 1)
        self.assertTrue(followed.json["isFollowing"])

        profile = self.post(
            "/wallet/community/profile",
            {"publicId": target_public_id},
            user="200",
        )
        self.assertEqual(profile.status_code, 200, profile.json)
        self.assertEqual(profile.json["profile"]["followerCount"], 1)
        self.assertTrue(profile.json["profile"]["isFollowing"])
        self.assertEqual(profile.json["profile"]["postCount"], 1)
        self.assertEqual(profile.json["posts"][0]["id"], post["id"])
        self.assertEqual(profile.json["posts"][0]["timelineType"], "post")
        self.assertNotIn("telegram_id", profile.json["profile"])

        viewer = self.post(
            "/wallet/social/state",
            {"displayName": "Runner Two"},
            user="200",
        ).json["profile"]
        followers = self.post(
            "/wallet/community/follows/list",
            {"publicId": target_public_id, "direction": "followers"},
            user="200",
        )
        self.assertEqual(followers.status_code, 200, followers.json)
        self.assertEqual(followers.json["players"][0]["publicId"], viewer["publicId"])

        following_feed = self.post(
            "/wallet/community/feed",
            {"scope": "following"},
            user="200",
        )
        self.assertEqual(
            [item["id"] for item in following_feed.json["posts"]],
            [post["id"]],
        )
        self.assertEqual(following_feed.json["posts"][0]["authorPublicId"], target_public_id)

        repeated_follow = self.post(
            "/wallet/community/follow",
            {"targetPublicId": target_public_id, "follow": True},
            user="200",
        )
        self.assertEqual(repeated_follow.json["followerCount"], 1)
        unfollowed = self.post(
            "/wallet/community/follow",
            {"targetPublicId": target_public_id, "follow": False},
            user="200",
        )
        self.assertEqual(unfollowed.json["followerCount"], 0)
        self.assertFalse(unfollowed.json["isFollowing"])
        self.assertEqual(
            self.post("/wallet/community/feed", {"scope": "following"}, user="200").json["posts"],
            [],
        )

        self_follow = self.post(
            "/wallet/community/follow",
            {"targetPublicId": viewer["publicId"], "follow": True},
            user="200",
        )
        self.assertEqual(self_follow.status_code, 400)

    def test_reposts_toggle_and_appear_in_the_reposter_profile(self):
        original = self.create_post(user="100", content="Strong finish today.")
        repeated = self.post(
            "/wallet/community/posts/repost",
            {"postId": original["id"]},
            user="200",
        )
        self.assertEqual(repeated.status_code, 200, repeated.json)
        self.assertTrue(repeated.json["repostedByMe"])
        self.assertEqual(repeated.json["repostCount"], 1)

        repost_feed = self.post("/wallet/community/feed", user="200")
        self.assertEqual(repost_feed.json["posts"][0]["repostCount"], 1)
        self.assertTrue(repost_feed.json["posts"][0]["repostedByMe"])

        reposter = self.post(
            "/wallet/social/state",
            {"displayName": "Runner Two"},
            user="200",
        ).json["profile"]
        timeline = self.post(
            "/wallet/community/profile",
            {"publicId": reposter["publicId"], "view": "reposts"},
            user="200",
        )
        self.assertEqual(timeline.status_code, 200, timeline.json)
        self.assertEqual(timeline.json["profile"]["repostCount"], 1)
        self.assertEqual(timeline.json["posts"][0]["id"], original["id"])
        self.assertEqual(timeline.json["posts"][0]["timelineType"], "repost")
        self.assertEqual(timeline.json["posts"][0]["reposter"]["publicId"], reposter["publicId"])

        own_repost = self.post(
            "/wallet/community/posts/repost",
            {"postId": original["id"]},
            user="100",
        )
        self.assertEqual(own_repost.status_code, 400)
        removed = self.post(
            "/wallet/community/posts/repost",
            {"postId": original["id"]},
            user="200",
        )
        self.assertFalse(removed.json["repostedByMe"])
        self.assertEqual(removed.json["repostCount"], 0)
        self.assertEqual(
            self.post(
                "/wallet/community/profile",
                {"publicId": reposter["publicId"], "view": "reposts"},
                user="200",
            ).json["posts"],
            [],
        )

    def test_profile_replies_and_media_timeline_use_opaque_author_ids(self):
        original = self.create_post(
            user="100",
            content="A photo from the finish line.",
            image="data:image/png;base64," + base64.b64encode(PNG_BYTES).decode(),
        )
        reply = self.post(
            "/wallet/community/comments",
            {
                "postId": original["id"],
                "displayName": "Reply Runner",
                "content": "Well done!",
            },
            user="200",
        )
        self.assertEqual(reply.status_code, 200, reply.json)
        reply_profile = self.post(
            "/wallet/social/state",
            {"displayName": "Reply Runner"},
            user="200",
        ).json["profile"]

        replies = self.post(
            "/wallet/community/profile",
            {"publicId": reply_profile["publicId"], "view": "replies"},
            user="200",
        )
        self.assertEqual(replies.status_code, 200, replies.json)
        self.assertEqual(replies.json["posts"][0]["timelineType"], "reply")
        self.assertEqual(replies.json["posts"][0]["reply"]["content"], "Well done!")
        self.assertEqual(replies.json["posts"][0]["reply"]["authorPublicId"], reply_profile["publicId"])

        media = self.post(
            "/wallet/community/profile",
            {"publicId": original["authorPublicId"], "view": "media"},
            user="200",
        )
        self.assertEqual(media.status_code, 200, media.json)
        self.assertEqual(media.json["posts"][0]["timelineType"], "media")
        self.assertTrue(media.json["posts"][0]["mediaUrl"].endswith(".png"))

        invalid = self.post(
            "/wallet/community/profile",
            {"publicId": original["authorPublicId"], "view": "unknown"},
            user="200",
        )
        self.assertEqual(invalid.status_code, 400)

    def test_zeronet_posts_earn_ten_zrl_each_and_claim_exactly_once(self):
        with service._connect(self.db_path) as db:
            balance_before = service._balance_units(db, "100")
        first = self.post(
            "/wallet/community/posts",
            {"displayName": "Runner 100", "content": "First Zer0Net post"},
            user="100",
        )
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json["rewardZrl"], 10)
        self.assertEqual(first.json["pendingZrl"], 10)

        second = self.post(
            "/wallet/community/posts",
            {"displayName": "Runner 100", "content": "Another Zer0Net post"},
            user="100",
        )
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(second.json["pendingZrl"], 20)
        feed = self.post("/wallet/community/feed", user="100")
        self.assertEqual(feed.status_code, 200, feed.json)
        self.assertEqual(feed.json["pendingZrl"], 20)
        self.assertEqual(feed.json["rewardPerPostZrl"], 10)

        claimed = self.post("/wallet/community/rewards/claim", user="100")
        self.assertEqual(claimed.status_code, 200, claimed.json)
        self.assertEqual(claimed.json["claimedZrl"], 20)
        self.assertEqual(claimed.json["pendingZrl"], 0)
        self.assertEqual(claimed.json["gameBalanceZrl"], (balance_before + 20 * service.ZRL_SCALE) / service.ZRL_SCALE)
        wallet_state = self.post("/wallet/state", user="100")
        self.assertEqual(wallet_state.status_code, 200, wallet_state.json)
        self.assertEqual(wallet_state.json["availableZrl"], claimed.json["gameBalanceZrl"])
        with service._connect(self.db_path) as db:
            self.assertEqual(
                service._balance_units(db, "100"),
                balance_before + 20 * service.ZRL_SCALE,
            )

        repeated = self.post("/wallet/community/rewards/claim", user="100")
        self.assertEqual(repeated.status_code, 200, repeated.json)
        self.assertEqual(repeated.json["claimedZrl"], 0)
        with service._connect(self.db_path) as db:
            self.assertEqual(
                service._balance_units(db, "100"),
                balance_before + 20 * service.ZRL_SCALE,
            )
        other_user = self.post("/wallet/community/rewards/claim", user="200")
        self.assertEqual(other_user.status_code, 200, other_user.json)
        self.assertEqual(other_user.json["claimedZrl"], 0)
        other_post = self.post(
            "/wallet/community/posts",
            {"displayName": "Runner 200", "content": "A post from another player"},
            user="200",
        )
        self.assertEqual(other_post.status_code, 200, other_post.json)
        other_claim = self.post("/wallet/community/rewards/claim", user="200")
        self.assertEqual(other_claim.status_code, 200, other_claim.json)
        self.assertEqual(other_claim.json["claimedZrl"], 10)
        self.assertEqual(other_claim.json["gameBalanceZrl"], 10)
        with service._connect(self.db_path) as db:
            self.assertEqual(service._balance_units(db, "200"), 10 * service.ZRL_SCALE)

        with service._connect(self.db_path) as db:
            entries = db.execute(
                """SELECT source, event_id, delta_units FROM wallet_ledger
                   WHERE telegram_id='100' AND source='zeronet_post_claim'
                   ORDER BY event_id"""
            ).fetchall()
            self.assertEqual(len(entries), 2)
            self.assertEqual(
                {row["event_id"] for row in entries},
                {
                    f"zeronet-post-claim:{first.json['post']['id']}",
                    f"zeronet-post-claim:{second.json['post']['id']}",
                },
            )
            self.assertTrue(all(int(row["delta_units"]) == 10 * service.ZRL_SCALE for row in entries))

    def test_profile_avatar_and_banner_are_private_uploads_served_by_opaque_urls(self):
        profile = self.post(
            "/wallet/social/state",
            {"displayName": "Runner 100"},
            user="100",
        ).json["profile"]
        image = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()

        avatar = self.post(
            "/wallet/community/profile/image",
            {"field": "avatar", "image": image},
            user="100",
        )
        self.assertEqual(avatar.status_code, 200, avatar.json)
        avatar_url = avatar.json["profile"]["avatarUrl"]
        self.assertRegex(avatar_url, r"^/wallet/community/media/[a-f0-9]{40}\.png$")
        avatar_response = self.client.get(avatar_url)
        self.assertEqual(avatar_response.status_code, 200)
        self.assertEqual(avatar_response.mimetype, "image/png")
        avatar_response.close()

        banner = self.post(
            "/wallet/community/profile/image",
            {"field": "banner", "image": image},
            user="100",
        )
        self.assertEqual(banner.status_code, 200, banner.json)
        self.assertIsNotNone(banner.json["profile"]["bannerUrl"])

        public_profile = self.post(
            "/wallet/community/profile",
            {"publicId": profile["publicId"]},
            user="200",
        )
        self.assertEqual(public_profile.status_code, 200, public_profile.json)
        self.assertEqual(public_profile.json["profile"]["avatarUrl"], avatar_url)
        self.assertEqual(public_profile.json["profile"]["bannerUrl"], banner.json["profile"]["bannerUrl"])
        feed = self.post("/wallet/community/feed", user="100")
        self.assertEqual(feed.json["viewerAvatarUrl"], avatar_url)

        invalid_field = self.post(
            "/wallet/community/profile/image",
            {"field": "cover", "image": image},
            user="100",
        )
        self.assertEqual(invalid_field.status_code, 400)
        unauthorized = self.post(
            "/wallet/community/profile/image",
            {"field": "avatar", "image": image},
            signed=False,
        )
        self.assertEqual(unauthorized.status_code, 401)


if __name__ == "__main__":
    unittest.main()
