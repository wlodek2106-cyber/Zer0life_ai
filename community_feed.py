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


def _post_payload(db, row, telegram_id, media_url):
    post_id = int(row["id"])
    comments = db.execute(
        """SELECT id, display_name, content, created_at
           FROM community_comments
           WHERE post_id=? AND deleted_at IS NULL
           ORDER BY id DESC LIMIT 3""",
        (post_id,),
    ).fetchall()
    comments.reverse()
    return {
        "id": post_id,
        "author": row["display_name"],
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
        "comments": [
            {
                "id": int(comment["id"]),
                "author": comment["display_name"],
                "content": comment["content"],
                "createdAt": int(comment["created_at"]) * 1000,
            }
            for comment in comments
        ],
        "isMine": str(row["telegram_id"]) == str(telegram_id),
    }


def register_community_routes(app, db_path, connect, authenticate, admin_telegram_id=None):
    """Register feed routes using the wallet service's verified Telegram session."""
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

    @bp.post("/wallet/community/feed")
    def community_feed():
        try:
            data, user = body_and_user()
            cursor = data.get("cursor")
            if cursor is not None:
                try:
                    cursor = int(cursor)
                except (TypeError, ValueError):
                    raise ValueError("Invalid feed cursor")
                if cursor <= 0:
                    raise ValueError("Invalid feed cursor")

            with connect(db_path) as db:
                if cursor is None:
                    rows = db.execute(
                        """SELECT * FROM community_posts WHERE deleted_at IS NULL
                           ORDER BY id DESC LIMIT ?""",
                        (PAGE_SIZE + 1,),
                    ).fetchall()
                else:
                    rows = db.execute(
                        """SELECT * FROM community_posts
                           WHERE deleted_at IS NULL AND id < ?
                           ORDER BY id DESC LIMIT ?""",
                        (cursor, PAGE_SIZE + 1),
                    ).fetchall()
                has_more = len(rows) > PAGE_SIZE
                rows = rows[:PAGE_SIZE]
                base = request.url_root.rstrip("/")
                media_url = base + "/wallet/community/media"
                posts = [_post_payload(db, row, user["id"], media_url) for row in rows]
                next_cursor = int(rows[-1]["id"]) if has_more and rows else None
            return jsonify({
                "success": True,
                "posts": posts,
                "nextCursor": next_cursor,
                "isModerator": bool(admin_telegram_id and str(user["id"]) == str(admin_telegram_id)),
            })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        except Exception:
            current_app.logger.exception("Community feed read failed")
            return jsonify({"success": False, "error": "The community feed is temporarily unavailable"}), 503

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
                row = db.execute(
                    "SELECT * FROM community_posts WHERE id=?",
                    (cursor.lastrowid,),
                ).fetchone()
                db.commit()
                base = request.url_root.rstrip("/")
                result = _post_payload(
                    db, row, user["id"], base + "/wallet/community/media",
                )
            return jsonify({"success": True, "post": result})
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
                comment = {
                    "id": int(cursor.lastrowid),
                    "author": display_name,
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
