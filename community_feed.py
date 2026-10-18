"""Authenticated public community feed for the Zer0Life Run Telegram Mini App."""

import base64
import binascii
import os
import re
import secrets
import time
import unicodedata

from flask import Blueprint, current_app, jsonify, request, send_file


MAX_POST_CHARS = 1000
MAX_COMMENT_CHARS = 500
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 3 * 1024 * 1024
PAGE_SIZE = 20
POSTS_PER_MINUTE = 3
COMMENTS_PER_MINUTE = 8
POST_REWARD_ZRL = 10


class RateLimitExceeded(Exception):
    """The authenticated player has reached a community write limit."""
ALLOWED_IMAGES = {
    "image/jpeg": (".jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": (".png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (".webp", lambda data: (
        len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    )),
    "image/gif": (".gif", lambda data: data.startswith((b"GIF87a", b"GIF89a"))),
}


def _display_name(value):
    if not isinstance(value, str):
        return "Runner"
    normalized = unicodedata.normalize("NFC", value)
    name = "".join(character for character in normalized if character.isprintable()).strip()
    return name[:48] or "Runner"


def _profile_media_url(media_key):
    if not isinstance(media_key, str) or not re.fullmatch(
        r"[a-f0-9]{40}\.(?:jpg|png|webp|gif)", media_key
    ):
        return None
    return f"/wallet/community/media/{media_key}"


def _text(value, limit, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(
        character for character in normalized
        if character in "\n\t" or character.isprintable()
    ).strip()
    if len(cleaned) > limit:
        raise ValueError(f"{field} can be up to {limit} characters")
    return cleaned


def _image_payload(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 256:
        raise ValueError("Image must be no larger than 2 MiB")
    match = re.fullmatch(
        r"data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/]+={0,2})",
        value,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("Choose a JPEG, PNG, WebP, or GIF image")
    mime = match.group(1).lower()
    try:
        content = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("The image data is invalid")
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be no larger than 2 MiB")
    extension, signature_check = ALLOWED_IMAGES[mime]
    if not signature_check(content):
        raise ValueError("The image type does not match its file contents")
    return content, mime, extension


def _rate_check(db, table, telegram_id, now):
    if table == "community_posts":
        limit = POSTS_PER_MINUTE
    elif table == "community_comments":
        limit = COMMENTS_PER_MINUTE
    else:
        raise ValueError("Invalid community rate-limit target")
    count = db.execute(
        f"SELECT COUNT(*) FROM {table} WHERE telegram_id=? AND created_at>?",
        (str(telegram_id), now - 60),
    ).fetchone()[0]
    if int(count) >= limit:
        raise RateLimitExceeded("You are posting too quickly. Please wait a minute.")


def _ensure_social_profile(db, telegram_id, display_name, now):
    current = str(telegram_id)
    existing = db.execute(
        """SELECT telegram_id, public_id, display_name, display_name_key,
                  created_at, updated_at, avatar_key, banner_key
           FROM social_profiles WHERE telegram_id=?""",
        (current,),
    ).fetchone()
    if existing:
        return existing

    name = _display_name(display_name)
    for _ in range(8):
        public_id = secrets.token_urlsafe(12)
        db.execute(
            """INSERT OR IGNORE INTO social_profiles
               (telegram_id, public_id, display_name, display_name_key,
                created_at, updated_at)
               VALUES(?,?,?,?,?,?)""",
            (current, public_id, name, name.casefold(), now, now),
        )
        profile = db.execute(
            """SELECT telegram_id, public_id, display_name, display_name_key,
                      created_at, updated_at, avatar_key, banner_key
               FROM social_profiles WHERE telegram_id=?""",
            (current,),
        ).fetchone()
        if profile:
            return profile
    raise RuntimeError("Could not allocate a social profile for the community post")


def _community_profile_payload(db, row, viewer_id):
    telegram_id = str(row["telegram_id"])
    public_id = row["public_id"]
    is_me = telegram_id == str(viewer_id)
    return {
        "publicId": public_id,
        "displayName": row["display_name"],
        "tag": "#" + public_id[:5].upper(),
        "avatarUrl": _profile_media_url(row["avatar_key"]),
        "bannerUrl": _profile_media_url(row["banner_key"]),
        "followerCount": int(db.execute(
            "SELECT COUNT(*) FROM community_follows WHERE followee_id=?",
            (telegram_id,),
        ).fetchone()[0]),
        "followingCount": int(db.execute(
            "SELECT COUNT(*) FROM community_follows WHERE follower_id=?",
            (telegram_id,),
        ).fetchone()[0]),
        "postCount": int(db.execute(
            """SELECT COUNT(*) FROM community_posts
               WHERE telegram_id=? AND deleted_at IS NULL""",
            (telegram_id,),
        ).fetchone()[0]),
        "repostCount": int(db.execute(
            "SELECT COUNT(*) FROM community_post_reposts WHERE telegram_id=?",
            (telegram_id,),
        ).fetchone()[0]),
        "isMe": is_me,
        "isFollowing": False if is_me else db.execute(
            """SELECT 1 FROM community_follows
               WHERE follower_id=? AND followee_id=?""",
            (str(viewer_id), telegram_id),
        ).fetchone() is not None,
        "joinedAt": int(row["created_at"] or row["updated_at"]) * 1000,
    }


def _post_payload(db, row, telegram_id, media_url):
    post_id = int(row["id"])
    author_profile = _ensure_social_profile(
        db, row["telegram_id"], row["display_name"], int(row["created_at"]),
    )
    comments = db.execute(
        """SELECT id, telegram_id, display_name, content, created_at
           FROM community_comments
           WHERE post_id=? AND deleted_at IS NULL
           ORDER BY id DESC LIMIT 3""",
        (post_id,),
    ).fetchall()
    comments.reverse()
    return {
        "id": post_id,
        "author": row["display_name"],
        "authorPublicId": author_profile["public_id"],
        "authorAvatarUrl": _profile_media_url(author_profile["avatar_key"]),
        "content": row["content"],
        "mediaUrl": f"{media_url}/{row['media_key']}" if row["media_key"] else None,
        "createdAt": int(row["created_at"]) * 1000,
        "likeCount": int(db.execute(
            "SELECT COUNT(*) FROM community_post_likes WHERE post_id=?",
            (post_id,),
        ).fetchone()[0]),
        "likedByMe": db.execute(
            "SELECT 1 FROM community_post_likes WHERE post_id=? AND telegram_id=?",
            (post_id, str(telegram_id)),
        ).fetchone() is not None,
        "commentCount": int(db.execute(
            "SELECT COUNT(*) FROM community_comments WHERE post_id=? AND deleted_at IS NULL",
            (post_id,),
        ).fetchone()[0]),
        "repostCount": int(db.execute(
            "SELECT COUNT(*) FROM community_post_reposts WHERE post_id=?",
            (post_id,),
        ).fetchone()[0]),
        "repostedByMe": db.execute(
            "SELECT 1 FROM community_post_reposts WHERE post_id=? AND telegram_id=?",
            (post_id, str(telegram_id)),
        ).fetchone() is not None,
        "comments": [
            {
                "id": int(comment["id"]),
                "author": comment["display_name"],
                "authorPublicId": _ensure_social_profile(
                    db,
                    comment["telegram_id"],
                    comment["display_name"],
                    int(comment["created_at"]),
                )["public_id"],
                "content": comment["content"],
                "createdAt": int(comment["created_at"]) * 1000,
            }
            for comment in comments
        ],
        "isMine": str(row["telegram_id"]) == str(telegram_id),
    }


def register_community_routes(
    app,
    db_path,
    connect,
    authenticate,
    admin_telegram_id=None,
    ledger_entry=None,
    balance_units=None,
    zrl_scale=1_000_000,
):
    """Register feed routes using the wallet service's verified Telegram session."""
    if not callable(ledger_entry) or not callable(balance_units) or int(zrl_scale) <= 0:
        raise ValueError("Community rewards require the wallet ledger callbacks")
    post_reward_units = POST_REWARD_ZRL * int(zrl_scale)
    media_dir = os.path.join(os.path.dirname(db_path), "community-media")
    os.makedirs(media_dir, mode=0o750, exist_ok=True)
    with connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS community_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                media_key TEXT UNIQUE,
                media_mime TEXT,
                created_at INTEGER NOT NULL,
                deleted_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS community_posts_feed_idx
                ON community_posts(deleted_at, id DESC);
            CREATE INDEX IF NOT EXISTS community_posts_rate_idx
                ON community_posts(telegram_id, created_at);
            CREATE TABLE IF NOT EXISTS community_post_rewards (
                post_id INTEGER PRIMARY KEY REFERENCES community_posts(id),
                telegram_id TEXT NOT NULL,
                amount_units INTEGER NOT NULL CHECK(amount_units > 0),
                created_at INTEGER NOT NULL,
                claimed_at INTEGER,
                claim_event_id TEXT UNIQUE,
                voided_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS community_post_rewards_pending_idx
                ON community_post_rewards(telegram_id, claimed_at, voided_at);
            CREATE TABLE IF NOT EXISTS community_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL REFERENCES community_posts(id),
                telegram_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                deleted_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS community_comments_post_idx
                ON community_comments(post_id, id);
            CREATE INDEX IF NOT EXISTS community_comments_rate_idx
                ON community_comments(telegram_id, created_at);
            CREATE TABLE IF NOT EXISTS community_post_likes (
                post_id INTEGER NOT NULL REFERENCES community_posts(id),
                telegram_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (post_id, telegram_id)
            );
            CREATE TABLE IF NOT EXISTS community_follows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                follower_id TEXT NOT NULL,
                followee_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(follower_id, followee_id),
                CHECK(follower_id <> followee_id)
            );
            CREATE INDEX IF NOT EXISTS community_follows_follower_idx
                ON community_follows(follower_id, id DESC);
            CREATE INDEX IF NOT EXISTS community_follows_followee_idx
                ON community_follows(followee_id, id DESC);
            CREATE TABLE IF NOT EXISTS community_post_reposts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL REFERENCES community_posts(id),
                telegram_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(post_id, telegram_id)
            );
            CREATE INDEX IF NOT EXISTS community_post_reposts_user_idx
                ON community_post_reposts(telegram_id, id DESC);
            CREATE INDEX IF NOT EXISTS community_post_reposts_post_idx
                ON community_post_reposts(post_id);
            """
        )

    bp = Blueprint("zerolife_community", __name__)

    def body_and_user():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("A JSON request body is required")
        return data, authenticate(data)

    def post_id_from(data):
        try:
            post_id = int(data.get("postId"))
        except (TypeError, ValueError):
            raise ValueError("A valid post ID is required")
        if post_id <= 0:
            raise ValueError("A valid post ID is required")
        return post_id

    def get_post(db, post_id):
        return db.execute(
            "SELECT * FROM community_posts WHERE id=? AND deleted_at IS NULL",
            (post_id,),
        ).fetchone()

    def profile_by_public_id(db, public_id):
        if not isinstance(public_id, str) or not public_id or len(public_id) > 64:
            return None
        return db.execute(
        """SELECT telegram_id, public_id, display_name, display_name_key,
                  created_at, updated_at, avatar_key, banner_key
               FROM social_profiles WHERE public_id=?""",
            (public_id,),
        ).fetchone()

    def profile_timeline(db, profile, viewer_id, view, cursor, media_url):
        target_id = str(profile["telegram_id"])
        if view in {"posts", "media"}:
            media_filter = " AND media_key IS NOT NULL" if view == "media" else ""
            cursor_filter = " AND id < ?" if cursor is not None else ""
            params = (target_id, cursor, PAGE_SIZE + 1) if cursor is not None else (target_id, PAGE_SIZE + 1)
            rows = db.execute(
                """SELECT * FROM community_posts
                   WHERE telegram_id=? AND deleted_at IS NULL"""
                + media_filter
                + cursor_filter
                + " ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
            timeline_type = "media" if view == "media" else "post"
            entry_id = lambda row: int(row["id"])
        elif view == "reposts":
            cursor_filter = " AND r.id < ?" if cursor is not None else ""
            params = (target_id, cursor, PAGE_SIZE + 1) if cursor is not None else (target_id, PAGE_SIZE + 1)
            rows = db.execute(
                """SELECT p.*, r.id AS profile_entry_id,
                          r.created_at AS reposted_at
                   FROM community_post_reposts r
                   JOIN community_posts p ON p.id=r.post_id
                   WHERE r.telegram_id=? AND p.deleted_at IS NULL"""
                + cursor_filter
                + " ORDER BY r.id DESC LIMIT ?",
                params,
            ).fetchall()
            timeline_type = "repost"
            entry_id = lambda row: int(row["profile_entry_id"])
        else:
            cursor_filter = " AND c.id < ?" if cursor is not None else ""
            params = (target_id, cursor, PAGE_SIZE + 1) if cursor is not None else (target_id, PAGE_SIZE + 1)
            rows = db.execute(
                """SELECT p.*, c.id AS reply_entry_id,
                          c.telegram_id AS reply_telegram_id,
                          c.display_name AS reply_display_name,
                          c.content AS reply_content,
                          c.created_at AS reply_created_at
                   FROM community_comments c
                   JOIN community_posts p ON p.id=c.post_id
                   WHERE c.telegram_id=? AND c.deleted_at IS NULL
                     AND p.deleted_at IS NULL"""
                + cursor_filter
                + " ORDER BY c.id DESC LIMIT ?",
                params,
            ).fetchall()
            timeline_type = "reply"
            entry_id = lambda row: int(row["reply_entry_id"])

        has_more = len(rows) > PAGE_SIZE
        rows = rows[:PAGE_SIZE]
        posts = []
        for row in rows:
            post = _post_payload(db, row, viewer_id, media_url)
            post["timelineType"] = timeline_type
            if view == "reposts":
                post["repostedAt"] = int(row["reposted_at"]) * 1000
                post["reposter"] = {
                    "publicId": profile["public_id"],
                    "displayName": profile["display_name"],
                    "tag": "#" + profile["public_id"][:5].upper(),
                }
            elif view == "replies":
                reply_profile = _ensure_social_profile(
                    db,
                    row["reply_telegram_id"],
                    row["reply_display_name"],
                    int(row["reply_created_at"]),
                )
                post["reply"] = {
                    "id": int(row["reply_entry_id"]),
                    "author": row["reply_display_name"],
                    "authorPublicId": reply_profile["public_id"],
                    "content": row["reply_content"],
                    "createdAt": int(row["reply_created_at"]) * 1000,
                }
            posts.append(post)
        next_cursor = entry_id(rows[-1]) if has_more and rows else None
        return posts, next_cursor

    @bp.post("/wallet/community/feed")
    def community_feed():
        try:
            data, user = body_and_user()
            scope = data.get("scope", "latest")
            if scope not in {"latest", "following"}:
                raise ValueError("Invalid community feed scope")
            cursor = data.get("cursor")
            if cursor is not None:
                try:
                    cursor = int(cursor)
                except (TypeError, ValueError):
                    raise ValueError("Invalid feed cursor")
                if cursor <= 0:
                    raise ValueError("Invalid feed cursor")

            with connect(db_path) as db:
                viewer_profile = _ensure_social_profile(
                    db,
                    user["id"],
                    user.get("username") or user.get("first_name") or "Runner",
                    int(time.time()),
                )
                followed_filter = ""
                followed_params = ()
                if scope == "following":
                    followed_filter = (
                        " AND (telegram_id=? OR telegram_id IN "
                        "(SELECT followee_id FROM community_follows WHERE follower_id=?))"
                    )
                    followed_params = (str(user["id"]), str(user["id"]))
                if cursor is None:
                    rows = db.execute(
                        """SELECT * FROM community_posts WHERE deleted_at IS NULL"""
                        + followed_filter
                        + " ORDER BY id DESC LIMIT ?",
                        (*followed_params, PAGE_SIZE + 1),
                    ).fetchall()
                else:
                    rows = db.execute(
                        """SELECT * FROM community_posts
                           WHERE deleted_at IS NULL AND id < ?"""
                        + followed_filter
                        + " ORDER BY id DESC LIMIT ?",
                        (cursor, *followed_params, PAGE_SIZE + 1),
                    ).fetchall()
                has_more = len(rows) > PAGE_SIZE
                rows = rows[:PAGE_SIZE]
                base = request.url_root.rstrip("/")
                media_url = base + "/wallet/community/media"
                posts = [_post_payload(db, row, user["id"], media_url) for row in rows]
                next_cursor = int(rows[-1]["id"]) if has_more and rows else None
                pending_units = int(db.execute(
                    """SELECT COALESCE(SUM(amount_units), 0)
                       FROM community_post_rewards
                       WHERE telegram_id=? AND claimed_at IS NULL AND voided_at IS NULL""",
                    (str(user["id"]),),
                ).fetchone()[0])
            return jsonify({
                "success": True,
                "posts": posts,
                "nextCursor": next_cursor,
                "pendingZrl": round(pending_units / int(zrl_scale), 6),
                "rewardPerPostZrl": POST_REWARD_ZRL,
                "viewerAvatarUrl": _profile_media_url(viewer_profile["avatar_key"]),
                "isModerator": bool(admin_telegram_id and str(user["id"]) == str(admin_telegram_id)),
            })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community feed read failed")
            return jsonify({"success": False, "error": "The Zer0Net feed is temporarily unavailable"}), 503

    @bp.post("/wallet/community/posts")
    def create_post():
        if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
            return jsonify({"success": False, "error": "Image must be no larger than 2 MiB"}), 413
        media_path = None
        try:
            data, user = body_and_user()
            content = _text(data.get("content", ""), MAX_POST_CHARS, "Post")
            image = _image_payload(data.get("image"))
            if not content and not image:
                raise ValueError("Add text or an image before posting")
            display_name = _display_name(data.get("displayName"))
            now = int(time.time())
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                _rate_check(db, "community_posts", user["id"], now)
                media_key = None
                media_mime = None
                if image:
                    image_bytes, media_mime, extension = image
                    media_key = secrets.token_hex(20) + extension
                    media_path = os.path.join(media_dir, media_key)
                    temporary_path = media_path + ".tmp"
                    with open(temporary_path, "xb") as image_file:
                        image_file.write(image_bytes)
                        image_file.flush()
                        os.fsync(image_file.fileno())
                    os.replace(temporary_path, media_path)
                cursor = db.execute(
                    """INSERT INTO community_posts
                       (telegram_id,display_name,content,media_key,media_mime,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (str(user["id"]), display_name, content, media_key, media_mime, now),
                )
                post_id = int(cursor.lastrowid)
                db.execute(
                    """INSERT INTO community_post_rewards
                       (post_id, telegram_id, amount_units, created_at)
                       VALUES(?,?,?,?)""",
                    (post_id, str(user["id"]), post_reward_units, now),
                )
                row = db.execute(
                    "SELECT * FROM community_posts WHERE id=?",
                    (post_id,),
                ).fetchone()
                pending_units = int(db.execute(
                    """SELECT COALESCE(SUM(amount_units), 0)
                       FROM community_post_rewards
                       WHERE telegram_id=? AND claimed_at IS NULL AND voided_at IS NULL""",
                    (str(user["id"]),),
                ).fetchone()[0])
                db.commit()
                base = request.url_root.rstrip("/")
                result = _post_payload(
                    db, row, user["id"], base + "/wallet/community/media",
                )
            return jsonify({
                "success": True,
                "post": result,
                "rewardZrl": POST_REWARD_ZRL,
                "pendingZrl": round(pending_units / int(zrl_scale), 6),
            })
        except PermissionError as error:
            if media_path:
                try:
                    os.remove(media_path)
                except OSError:
                    pass
            return jsonify({"success": False, "error": str(error)}), 401
        except RateLimitExceeded as error:
            if media_path:
                try:
                    os.remove(media_path)
                except OSError:
                    pass
            return jsonify({"success": False, "error": str(error)}), 429
        except ValueError as error:
            if media_path:
                try:
                    os.remove(media_path)
                except OSError:
                    pass
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            if media_path:
                try:
                    os.remove(media_path)
                except OSError:
                    pass
            current_app.logger.exception("Community post creation failed")
            return jsonify({"success": False, "error": "Your post could not be sent"}), 503

    @bp.post("/wallet/community/rewards/claim")
    def claim_post_rewards():
        try:
            data, user = body_and_user()
            telegram_id = str(user["id"])
            claimed_units = 0
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                rewards = db.execute(
                    """SELECT post_id, amount_units
                       FROM community_post_rewards
                       WHERE telegram_id=? AND claimed_at IS NULL AND voided_at IS NULL
                       ORDER BY post_id""",
                    (telegram_id,),
                ).fetchall()
                claimed_at = int(time.time())
                for reward in rewards:
                    post_id = int(reward["post_id"])
                    amount = int(reward["amount_units"])
                    event_id = f"zeronet-post-claim:{post_id}"
                    inserted = ledger_entry(
                        db,
                        telegram_id,
                        "zeronet_post_claim",
                        amount,
                        event_id,
                    )
                    if not inserted:
                        existing = db.execute(
                            """SELECT telegram_id, source, delta_units
                               FROM wallet_ledger WHERE event_id=?""",
                            (event_id,),
                        ).fetchone()
                        if not existing or (
                            str(existing["telegram_id"]) != telegram_id
                            or existing["source"] != "zeronet_post_claim"
                            or int(existing["delta_units"]) != amount
                        ):
                            raise RuntimeError("A Zer0Net reward ledger entry could not be verified")
                    db.execute(
                        """UPDATE community_post_rewards
                           SET claimed_at=?, claim_event_id=?
                           WHERE post_id=? AND telegram_id=?
                             AND claimed_at IS NULL AND voided_at IS NULL""",
                        (claimed_at, event_id, post_id, telegram_id),
                    )
                    claimed_units += amount
                pending_units = int(db.execute(
                    """SELECT COALESCE(SUM(amount_units), 0)
                       FROM community_post_rewards
                       WHERE telegram_id=? AND claimed_at IS NULL AND voided_at IS NULL""",
                    (telegram_id,),
                ).fetchone()[0])
                game_balance = int(balance_units(db, telegram_id))
                db.commit()
            return jsonify({
                "success": True,
                "claimedZrl": round(claimed_units / int(zrl_scale), 6),
                "pendingZrl": round(pending_units / int(zrl_scale), 6),
                "gameBalanceZrl": round(game_balance / int(zrl_scale), 6),
            })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Zer0Net reward claim failed")
            return jsonify({"success": False, "error": "Your Zer0Net rewards could not be claimed"}), 503

    @bp.post("/wallet/community/posts/like")
    def toggle_like():
        try:
            data, user = body_and_user()
            post_id = post_id_from(data)
            now = int(time.time())
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                if not get_post(db, post_id):
                    db.rollback()
                    return jsonify({"success": False, "error": "Post not found"}), 404
                existing = db.execute(
                    "SELECT 1 FROM community_post_likes WHERE post_id=? AND telegram_id=?",
                    (post_id, str(user["id"])),
                ).fetchone()
                if existing:
                    db.execute(
                        "DELETE FROM community_post_likes WHERE post_id=? AND telegram_id=?",
                        (post_id, str(user["id"])),
                    )
                    liked = False
                else:
                    db.execute(
                        """INSERT INTO community_post_likes(post_id,telegram_id,created_at)
                           VALUES(?,?,?)""",
                        (post_id, str(user["id"]), now),
                    )
                    liked = True
                count = int(db.execute(
                    "SELECT COUNT(*) FROM community_post_likes WHERE post_id=?",
                    (post_id,),
                ).fetchone()[0])
                db.commit()
            return jsonify({"success": True, "likedByMe": liked, "likeCount": count})
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except RateLimitExceeded as error:
            return jsonify({"success": False, "error": str(error)}), 429
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community like update failed")
            return jsonify({"success": False, "error": "The like could not be updated"}), 503

    @bp.post("/wallet/community/profile")
    def community_profile():
        try:
            data, user = body_and_user()
            public_id = data.get("publicId")
            view = data.get("view", "posts")
            if view not in {"posts", "replies", "reposts", "media"}:
                raise ValueError("Invalid profile timeline")
            cursor = data.get("cursor")
            if cursor is not None:
                try:
                    cursor = int(cursor)
                except (TypeError, ValueError):
                    raise ValueError("Invalid profile cursor")
                if cursor <= 0:
                    raise ValueError("Invalid profile cursor")

            with connect(db_path) as db:
                profile = profile_by_public_id(db, public_id)
                if not profile:
                    return jsonify({"success": False, "error": "Runner profile not found"}), 404
                profile_payload = _community_profile_payload(db, profile, user["id"])
                posts, next_cursor = profile_timeline(
                    db,
                    profile,
                    user["id"],
                    view,
                    cursor,
                    request.url_root.rstrip("/") + "/wallet/community/media",
                )
            return jsonify({
                "success": True,
                "profile": profile_payload,
                "view": view,
                "posts": posts,
                "nextCursor": next_cursor,
            })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community profile read failed")
            return jsonify({"success": False, "error": "The runner profile is temporarily unavailable"}), 503

    @bp.post("/wallet/community/profile/image")
    def update_profile_image():
        if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
            return jsonify({"success": False, "error": "Image must be no larger than 2 MiB"}), 413
        media_path = None
        temporary_path = None
        committed = False
        old_media_key = None
        try:
            data, user = body_and_user()
            field = data.get("field")
            columns = {
                "avatar": ("avatar_key", "avatar_mime"),
                "banner": ("banner_key", "banner_mime"),
            }
            if field not in columns:
                raise ValueError("Choose an avatar or profile banner")
            image = _image_payload(data.get("image"))
            if not image:
                raise ValueError("Choose an image to upload")

            image_bytes, mime, extension = image
            media_key = secrets.token_hex(20) + extension
            media_path = os.path.join(media_dir, media_key)
            temporary_path = media_path + ".tmp"
            with open(temporary_path, "xb") as image_file:
                image_file.write(image_bytes)
                image_file.flush()
                os.fsync(image_file.fileno())
            os.replace(temporary_path, media_path)
            temporary_path = None

            key_column, mime_column = columns[field]
            now = int(time.time())
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                profile = db.execute(
                    """SELECT telegram_id, public_id, display_name, display_name_key,
                              created_at, updated_at, avatar_key, banner_key
                       FROM social_profiles WHERE telegram_id=?""",
                    (str(user["id"]),),
                ).fetchone()
                if not profile:
                    profile = _ensure_social_profile(
                        db,
                        user["id"],
                        user.get("username") or user.get("first_name") or "Runner",
                        now,
                    )
                old_media_key = profile[key_column]
                db.execute(
                    f"""UPDATE social_profiles
                        SET {key_column}=?, {mime_column}=?, updated_at=?
                        WHERE telegram_id=?""",
                    (media_key, mime, now, str(user["id"])),
                )
                updated = db.execute(
                    """SELECT telegram_id, public_id, display_name, display_name_key,
                              created_at, updated_at, avatar_key, banner_key
                       FROM social_profiles WHERE telegram_id=?""",
                    (str(user["id"]),),
                ).fetchone()
                profile_payload = _community_profile_payload(db, updated, user["id"])
                referenced = False
                if old_media_key and old_media_key != media_key:
                    referenced = db.execute(
                        """SELECT 1 FROM community_posts WHERE media_key=?
                           UNION ALL
                           SELECT 1 FROM social_profiles
                           WHERE avatar_key=? OR banner_key=? LIMIT 1""",
                        (old_media_key, old_media_key, old_media_key),
                    ).fetchone() is not None
                db.commit()
                committed = True

            if old_media_key and old_media_key != media_key and not referenced:
                try:
                    os.remove(os.path.join(media_dir, old_media_key))
                except OSError:
                    pass
            return jsonify({"success": True, "profile": profile_payload})
        except PermissionError as error:
            for path in (media_path, temporary_path):
                if path and not committed:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            for path in (media_path, temporary_path):
                if path and not committed:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            for path in (media_path, temporary_path):
                if path and not committed:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            current_app.logger.exception("Zer0Net profile image update failed")
            return jsonify({"success": False, "error": "The profile image could not be updated"}), 503

    @bp.post("/wallet/community/follow")
    def toggle_follow():
        try:
            data, user = body_and_user()
            target_public_id = data.get("targetPublicId")
            should_follow = data.get("follow")
            if not isinstance(should_follow, bool):
                raise ValueError("Choose whether to follow or unfollow this runner")
            now = int(time.time())
            with connect(db_path) as db:
                target = profile_by_public_id(db, target_public_id)
                if not target:
                    return jsonify({"success": False, "error": "Runner profile not found"}), 404
                viewer_id = str(user["id"])
                target_id = str(target["telegram_id"])
                if viewer_id == target_id:
                    raise ValueError("You cannot follow your own profile")
                db.execute("BEGIN IMMEDIATE")
                _ensure_social_profile(
                    db,
                    viewer_id,
                    user.get("username") or "Runner",
                    now,
                )
                if should_follow:
                    db.execute(
                        """INSERT OR IGNORE INTO community_follows
                           (follower_id, followee_id, created_at) VALUES(?,?,?)""",
                        (viewer_id, target_id, now),
                    )
                else:
                    db.execute(
                        "DELETE FROM community_follows WHERE follower_id=? AND followee_id=?",
                        (viewer_id, target_id),
                    )
                following = db.execute(
                    """SELECT 1 FROM community_follows
                       WHERE follower_id=? AND followee_id=?""",
                    (viewer_id, target_id),
                ).fetchone() is not None
                follower_count = int(db.execute(
                    "SELECT COUNT(*) FROM community_follows WHERE followee_id=?",
                    (target_id,),
                ).fetchone()[0])
                db.commit()
            return jsonify({
                "success": True,
                "isFollowing": following,
                "followerCount": follower_count,
            })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community follow update failed")
            return jsonify({"success": False, "error": "The follow could not be updated"}), 503

    @bp.post("/wallet/community/follows/list")
    def list_follows():
        try:
            data, user = body_and_user()
            public_id = data.get("publicId")
            direction = data.get("direction")
            if direction not in {"followers", "following"}:
                raise ValueError("Choose followers or following")
            cursor = data.get("cursor")
            if cursor is not None:
                try:
                    cursor = int(cursor)
                except (TypeError, ValueError):
                    raise ValueError("Invalid followers cursor")
                if cursor <= 0:
                    raise ValueError("Invalid followers cursor")

            with connect(db_path) as db:
                target = profile_by_public_id(db, public_id)
                if not target:
                    return jsonify({"success": False, "error": "Runner profile not found"}), 404
                target_id = str(target["telegram_id"])
                if direction == "followers":
                    related_column = "follower_id"
                    owner_column = "followee_id"
                else:
                    related_column = "followee_id"
                    owner_column = "follower_id"
                cursor_filter = " AND f.id < ?" if cursor is not None else ""
                params = (target_id, cursor, PAGE_SIZE + 1) if cursor is not None else (target_id, PAGE_SIZE + 1)
                rows = db.execute(
                    f"""SELECT f.id AS follow_id, p.telegram_id, p.public_id,
                               p.display_name, p.display_name_key,
                                p.created_at, p.updated_at,
                                p.avatar_key, p.banner_key
                        FROM community_follows f
                        JOIN social_profiles p ON p.telegram_id=f.{related_column}
                        WHERE f.{owner_column}=?"""
                    + cursor_filter
                    + " ORDER BY f.id DESC LIMIT ?",
                    params,
                ).fetchall()
                has_more = len(rows) > PAGE_SIZE
                rows = rows[:PAGE_SIZE]
                players = [
                    _community_profile_payload(db, row, user["id"])
                    for row in rows
                ]
                next_cursor = int(rows[-1]["follow_id"]) if has_more and rows else None
            return jsonify({
                "success": True,
                "direction": direction,
                "players": players,
                "nextCursor": next_cursor,
            })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community follow list read failed")
            return jsonify({"success": False, "error": "This runner list is temporarily unavailable"}), 503

    @bp.post("/wallet/community/posts/repost")
    def toggle_repost():
        try:
            data, user = body_and_user()
            post_id = post_id_from(data)
            now = int(time.time())
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                post = get_post(db, post_id)
                if not post:
                    db.rollback()
                    return jsonify({"success": False, "error": "Post not found"}), 404
                viewer_id = str(user["id"])
                if str(post["telegram_id"]) == viewer_id:
                    db.rollback()
                    return jsonify({"success": False, "error": "You cannot repost your own post"}), 400
                existing = db.execute(
                    """SELECT 1 FROM community_post_reposts
                       WHERE post_id=? AND telegram_id=?""",
                    (post_id, viewer_id),
                ).fetchone()
                if existing:
                    db.execute(
                        """DELETE FROM community_post_reposts
                           WHERE post_id=? AND telegram_id=?""",
                        (post_id, viewer_id),
                    )
                    reposted = False
                else:
                    _ensure_social_profile(
                        db,
                        viewer_id,
                        user.get("username") or "Runner",
                        now,
                    )
                    db.execute(
                        """INSERT INTO community_post_reposts
                           (post_id, telegram_id, created_at) VALUES(?,?,?)""",
                        (post_id, viewer_id, now),
                    )
                    reposted = True
                repost_count = int(db.execute(
                    "SELECT COUNT(*) FROM community_post_reposts WHERE post_id=?",
                    (post_id,),
                ).fetchone()[0])
                db.commit()
            return jsonify({
                "success": True,
                "repostedByMe": reposted,
                "repostCount": repost_count,
            })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community repost update failed")
            return jsonify({"success": False, "error": "The repost could not be updated"}), 503

    @bp.post("/wallet/community/comments")
    def create_comment():
        try:
            data, user = body_and_user()
            post_id = post_id_from(data)
            content = _text(data.get("content", ""), MAX_COMMENT_CHARS, "Comment")
            if not content:
                raise ValueError("Write a comment before sending")
            display_name = _display_name(data.get("displayName"))
            now = int(time.time())
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                if not get_post(db, post_id):
                    db.rollback()
                    return jsonify({"success": False, "error": "Post not found"}), 404
                _rate_check(db, "community_comments", user["id"], now)
                cursor = db.execute(
                    """INSERT INTO community_comments
                       (post_id,telegram_id,display_name,content,created_at)
                       VALUES(?,?,?,?,?)""",
                    (post_id, str(user["id"]), display_name, content, now),
                )
                author_profile = _ensure_social_profile(
                    db, user["id"], display_name, now,
                )
                comment = {
                    "id": int(cursor.lastrowid),
                    "author": display_name,
                    "authorPublicId": author_profile["public_id"],
                    "content": content,
                    "createdAt": now * 1000,
                }
                count = int(db.execute(
                    """SELECT COUNT(*) FROM community_comments
                       WHERE post_id=? AND deleted_at IS NULL""",
                    (post_id,),
                ).fetchone()[0])
                db.commit()
            return jsonify({"success": True, "comment": comment, "commentCount": count})
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except RateLimitExceeded as error:
            return jsonify({"success": False, "error": str(error)}), 429
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community comment creation failed")
            return jsonify({"success": False, "error": "Your comment could not be sent"}), 503

    @bp.post("/wallet/community/posts/delete")
    def delete_post():
        image_path = None
        try:
            data, user = body_and_user()
            post_id = post_id_from(data)
            is_moderator = bool(
                admin_telegram_id and str(user["id"]) == str(admin_telegram_id)
            )
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_post(db, post_id)
                if not row:
                    db.rollback()
                    return jsonify({"success": False, "error": "Post not found"}), 404
                if str(row["telegram_id"]) != str(user["id"]) and not is_moderator:
                    db.rollback()
                    return jsonify({"success": False, "error": "You can only delete your own posts"}), 403
                db.execute(
                    "UPDATE community_posts SET deleted_at=? WHERE id=?",
                    (int(time.time()), post_id),
                )
                db.execute(
                    """UPDATE community_post_rewards SET voided_at=?
                       WHERE post_id=? AND claimed_at IS NULL AND voided_at IS NULL""",
                    (int(time.time()), post_id),
                )
                if row["media_key"]:
                    image_path = os.path.join(media_dir, row["media_key"])
                db.commit()
            if image_path:
                try:
                    os.remove(image_path)
                except FileNotFoundError:
                    pass
            return jsonify({"success": True})
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community post deletion failed")
            return jsonify({"success": False, "error": "The post could not be deleted"}), 503

    @bp.get("/wallet/community/media/<media_key>")
    def get_media(media_key):
        if not re.fullmatch(r"[a-f0-9]{40}\.(?:jpg|png|webp|gif)", media_key):
            return jsonify({"success": False, "error": "Image not found"}), 404
        with connect(db_path) as db:
            row = db.execute(
                """SELECT media_mime FROM community_posts
                   WHERE media_key=? AND deleted_at IS NULL""",
                (media_key,),
            ).fetchone()
            if not row:
                row = db.execute(
                    "SELECT avatar_mime AS media_mime FROM social_profiles WHERE avatar_key=?",
                    (media_key,),
                ).fetchone()
            if not row:
                row = db.execute(
                    "SELECT banner_mime AS media_mime FROM social_profiles WHERE banner_key=?",
                    (media_key,),
                ).fetchone()
        if not row or row["media_mime"] not in ALLOWED_IMAGES:
            return jsonify({"success": False, "error": "Image not found"}), 404
        path = os.path.join(media_dir, media_key)
        if not os.path.isfile(path):
            return jsonify({"success": False, "error": "Image not found"}), 404
        response = send_file(
            path,
            mimetype=row["media_mime"],
            as_attachment=False,
            conditional=True,
            max_age=300,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response

    app.register_blueprint(bp)
