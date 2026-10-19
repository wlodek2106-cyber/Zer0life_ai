"""Authenticated friend requests and private text messages for Zer0Life Run."""

import secrets
import sqlite3
import time
import unicodedata

from flask import Blueprint, jsonify, request


MAX_DISPLAY_NAME = 48
MAX_PROFILE_BIO = 160
MAX_REQUEST_MESSAGE = 500
MAX_MESSAGE_CHARS = 1000
PLAYER_SEARCH_LIMIT = 20
MESSAGE_PAGE_SIZE = 100
REQUESTS_PER_HOUR = 8
MESSAGES_PER_MINUTE = 30
DECLINED_REQUEST_COOLDOWN = 24 * 60 * 60


def _now():
    return int(time.time())


def _plain_text(value, limit, field, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    cleaned = "".join(character for character in normalized if character in "\n\t" or character.isprintable()).strip()
    if len(cleaned) > limit:
        raise ValueError(f"{field} can be up to {limit} characters")
    if not cleaned and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    return cleaned


def _profile_payload(row):
    public_id = row["public_id"]
    return {
        "publicId": public_id,
        "displayName": row["display_name"],
        "bio": row["bio"] or "",
        "tag": "#" + public_id[:5].upper(),
        "avatarUrl": f"/wallet/community/media/{row['avatar_key']}" if row["avatar_key"] else None,
        "bannerUrl": f"/wallet/community/media/{row['banner_key']}" if row["banner_key"] else None,
        "joinedAt": int(row["created_at"] or row["updated_at"]) * 1000,
    }


def _pair(first_id, second_id):
    return tuple(sorted((str(first_id), str(second_id))))


def _are_friends(db, first_id, second_id):
    low, high = _pair(first_id, second_id)
    return db.execute(
        "SELECT 1 FROM social_friendships WHERE user_low=? AND user_high=?",
        (low, high),
    ).fetchone() is not None


def _ensure_profile(db, user, display_name):
    telegram_id = str(user["id"])
    supplied = display_name if isinstance(display_name, str) and display_name.strip() else ""
    fallback = user.get("username") or "Runner"
    name = _plain_text(supplied or fallback, MAX_DISPLAY_NAME, "Display name")
    key = name.casefold()
    now = _now()
    existing = db.execute(
        "SELECT public_id FROM social_profiles WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()
    if existing:
        db.execute(
            """UPDATE social_profiles
               SET display_name=?, display_name_key=?, updated_at=?
               WHERE telegram_id=?""",
            (name, key, now, telegram_id),
        )
        return db.execute(
            """SELECT telegram_id, public_id, display_name, display_name_key,
                      created_at, updated_at, bio, avatar_key, banner_key
               FROM social_profiles WHERE telegram_id=?""",
            (telegram_id,),
        ).fetchone()

    for _ in range(8):
        public_id = secrets.token_urlsafe(12)
        try:
            db.execute(
                """INSERT INTO social_profiles
                       (telegram_id, public_id, display_name, display_name_key,
                        created_at, updated_at)
                       VALUES(?,?,?,?,?,?)""",
                    (telegram_id, public_id, name, key, now, now),
            )
            return db.execute(
                    """SELECT telegram_id, public_id, display_name, display_name_key,
                              created_at, updated_at, bio, avatar_key, banner_key
                       FROM social_profiles WHERE telegram_id=?""",
                (telegram_id,),
            ).fetchone()
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Could not allocate a social profile")


def _profile_by_public_id(db, public_id):
    if not isinstance(public_id, str) or not public_id or len(public_id) > 64:
        return None
    return db.execute(
        """SELECT telegram_id, public_id, display_name, display_name_key,
                  created_at, updated_at, bio, avatar_key, banner_key
           FROM social_profiles WHERE public_id=?""",
        (public_id,),
    ).fetchone()


def _request_payload(row):
    return {
        "requestId": row["request_id"],
        "publicId": row["public_id"],
        "displayName": row["display_name"],
        "message": row["initial_message"],
        "avatarUrl": f"/wallet/community/media/{row['avatar_key']}" if row["avatar_key"] else None,
        "bannerUrl": f"/wallet/community/media/{row['banner_key']}" if row["banner_key"] else None,
        "createdAt": int(row["created_at"]) * 1000,
    }


def _requests_for(db, telegram_id, direction):
    if direction == "incoming":
        other_column = "sender_id"
        owner_column = "receiver_id"
    else:
        other_column = "receiver_id"
        owner_column = "sender_id"
    rows = db.execute(
        f"""SELECT r.request_id, r.initial_message, r.created_at,
                   p.public_id, p.display_name, p.avatar_key, p.banner_key
            FROM social_requests r
            JOIN social_profiles p ON p.telegram_id=r.{other_column}
            WHERE r.{owner_column}=? AND r.status='pending'
            ORDER BY r.created_at DESC LIMIT 50""",
        (str(telegram_id),),
    ).fetchall()
    return [_request_payload(row) for row in rows]


def _friends_for(db, telegram_id):
    current = str(telegram_id)
    rows = db.execute(
        """SELECT CASE WHEN user_low=? THEN user_high ELSE user_low END AS friend_id
           FROM social_friendships WHERE user_low=? OR user_high=?
           ORDER BY created_at DESC LIMIT 200""",
        (current, current, current),
    ).fetchall()
    friends = []
    for row in rows:
        friend_id = row["friend_id"]
        profile = db.execute(
            """SELECT public_id, display_name, bio, avatar_key, banner_key
               FROM social_profiles WHERE telegram_id=?""",
            (friend_id,),
        ).fetchone()
        if not profile:
            continue
        latest = db.execute(
            """SELECT content, created_at FROM social_messages
               WHERE (sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)
               ORDER BY id DESC LIMIT 1""",
            (current, friend_id, friend_id, current),
        ).fetchone()
        unread = db.execute(
            """SELECT COUNT(*) FROM social_messages
               WHERE sender_id=? AND recipient_id=? AND read_at IS NULL""",
            (friend_id, current),
        ).fetchone()[0]
        friends.append({
            "publicId": profile["public_id"],
            "displayName": profile["display_name"],
            "bio": profile["bio"] or "",
            "avatarUrl": f"/wallet/community/media/{profile['avatar_key']}" if profile["avatar_key"] else None,
            "bannerUrl": f"/wallet/community/media/{profile['banner_key']}" if profile["banner_key"] else None,
            "lastMessage": latest["content"] if latest else "",
            "lastMessageAt": int(latest["created_at"]) * 1000 if latest else 0,
            "unreadCount": int(unread),
        })
    friends.sort(key=lambda friend: friend["lastMessageAt"], reverse=True)
    return friends


def _message_payload(row, current_id):
    return {
        "id": int(row["id"]),
        "fromMe": str(row["sender_id"]) == str(current_id),
        "content": row["content"],
        "clientMessageId": row["client_message_id"],
        "createdAt": int(row["created_at"]) * 1000,
    }


def register_social_routes(app, db_path, connect, authenticate):
    """Register private social routes using the wallet service's Telegram auth."""
    with connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS social_profiles (
                telegram_id TEXT PRIMARY KEY,
                public_id TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                display_name_key TEXT NOT NULL,
                bio TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                avatar_key TEXT,
                avatar_mime TEXT,
                banner_key TEXT,
                banner_mime TEXT
            );
            CREATE INDEX IF NOT EXISTS social_profiles_search_idx
                ON social_profiles(display_name_key);

            CREATE TABLE IF NOT EXISTS social_requests (
                request_id TEXT PRIMARY KEY,
                sender_id TEXT NOT NULL,
                receiver_id TEXT NOT NULL,
                initial_message TEXT NOT NULL DEFAULT '',
                client_request_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'accepted', 'declined')),
                created_at INTEGER NOT NULL,
                resolved_at INTEGER,
                UNIQUE(sender_id, client_request_id)
            );
            CREATE INDEX IF NOT EXISTS social_requests_incoming_idx
                ON social_requests(receiver_id, status, created_at);
            CREATE INDEX IF NOT EXISTS social_requests_outgoing_idx
                ON social_requests(sender_id, status, created_at);

            CREATE TABLE IF NOT EXISTS social_friendships (
                user_low TEXT NOT NULL,
                user_high TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(user_low, user_high),
                CHECK(user_low < user_high)
            );

            CREATE TABLE IF NOT EXISTS social_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                content TEXT NOT NULL,
                client_message_id TEXT,
                created_at INTEGER NOT NULL,
                read_at INTEGER,
                UNIQUE(sender_id, client_message_id)
            );
            CREATE INDEX IF NOT EXISTS social_messages_thread_idx
                ON social_messages(sender_id, recipient_id, id);
            CREATE INDEX IF NOT EXISTS social_messages_unread_idx
                ON social_messages(recipient_id, sender_id, read_at);
            """
        )
        profile_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(social_profiles)")
        }
        if "created_at" not in profile_columns:
            db.execute(
                "ALTER TABLE social_profiles ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0"
            )
        if "bio" not in profile_columns:
            db.execute(
                "ALTER TABLE social_profiles ADD COLUMN bio TEXT NOT NULL DEFAULT ''"
            )
        for column in ("avatar_key", "avatar_mime", "banner_key", "banner_mime"):
            if column not in profile_columns:
                db.execute(f"ALTER TABLE social_profiles ADD COLUMN {column} TEXT")
        db.execute(
            "UPDATE social_profiles SET created_at=updated_at WHERE created_at=0"
        )

    bp = Blueprint("zerolife_social", __name__)

    def body_and_user():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("A JSON request body is required")
        return data, authenticate(data)

    @bp.post("/wallet/social/state")
    def social_state():
        try:
            data, user = body_and_user()
            with connect(db_path) as db:
                profile = _ensure_profile(db, user, data.get("displayName"))
                return jsonify({
                    "success": True,
                    "profile": _profile_payload(profile),
                    "friends": _friends_for(db, user["id"]),
                    "incomingRequests": _requests_for(db, user["id"], "incoming"),
                    "outgoingRequests": _requests_for(db, user["id"], "outgoing"),
                })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400

    @bp.post("/wallet/social/profile/bio")
    def update_profile_bio():
        try:
            data, user = body_and_user()
            if "bio" not in data:
                raise ValueError("Profile bio is required")
            bio = _plain_text(data["bio"], MAX_PROFILE_BIO, "Profile bio", allow_empty=True)
            with connect(db_path) as db:
                profile = db.execute(
                    """SELECT telegram_id, public_id, display_name, display_name_key,
                              created_at, updated_at, bio, avatar_key, banner_key
                       FROM social_profiles WHERE telegram_id=?""",
                    (str(user["id"]),),
                ).fetchone()
                if not profile:
                    profile = _ensure_profile(db, user, data.get("displayName"))
                db.execute(
                    """UPDATE social_profiles
                       SET bio=?, updated_at=? WHERE telegram_id=?""",
                    (bio, _now(), str(user["id"])),
                )
                updated = db.execute(
                    """SELECT telegram_id, public_id, display_name, display_name_key,
                              created_at, updated_at, bio, avatar_key, banner_key
                       FROM social_profiles WHERE telegram_id=?""",
                    (str(user["id"]),),
                ).fetchone()
            return jsonify({"success": True, "profile": _profile_payload(updated)})
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400

    @bp.post("/wallet/social/search")
    def search_players():
        try:
            data, user = body_and_user()
            query = _plain_text(data.get("query", ""), MAX_DISPLAY_NAME, "Search query")
            if len(query) < 2:
                raise ValueError("Enter at least two characters to search")
            query_key = query.casefold().lstrip("#")
            with connect(db_path) as db:
                rows = db.execute(
                    """SELECT p.public_id, p.display_name, p.created_at, p.updated_at,
                              p.bio, p.avatar_key, p.banner_key,
                              EXISTS(
                                  SELECT 1 FROM community_follows f
                                  WHERE f.follower_id=? AND f.followee_id=p.telegram_id
                              ) AS is_following,
                              (SELECT COUNT(*) FROM community_follows f
                               WHERE f.followee_id=p.telegram_id) AS follower_count
                       FROM social_profiles p
                       WHERE p.telegram_id<>?
                         AND (instr(p.display_name_key, ?) > 0
                              OR instr(lower(p.public_id), ?) > 0)
                       ORDER BY p.display_name_key ASC, p.updated_at DESC LIMIT ?""",
                    (
                        str(user["id"]),
                        str(user["id"]),
                        query.casefold(),
                        query_key,
                        PLAYER_SEARCH_LIMIT,
                    ),
                ).fetchall()
            return jsonify({
                "success": True,
                "players": [
                    {
                        **_profile_payload(row),
                        "isFollowing": bool(row["is_following"]),
                        "followerCount": int(row["follower_count"]),
                    }
                    for row in rows
                ],
            })
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400

    @bp.post("/wallet/social/requests")
    def create_request():
        try:
            data, user = body_and_user()
            target_public_id = data.get("targetPublicId")
            initial_message = _plain_text(
                data.get("initialMessage", ""),
                MAX_REQUEST_MESSAGE,
                "First message",
                allow_empty=True,
            )
            client_request_id = data.get("clientRequestId")
            if client_request_id is not None and (
                not isinstance(client_request_id, str)
                or not client_request_id
                or len(client_request_id) > 64
            ):
                raise ValueError("Invalid request identifier")

            now = _now()
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                target = _profile_by_public_id(db, target_public_id)
                if not target:
                    return jsonify({"success": False, "error": "Player not found"}), 404
                sender_id = str(user["id"])
                receiver_id = str(target["telegram_id"])
                if sender_id == receiver_id:
                    raise ValueError("You cannot add yourself")

                if client_request_id:
                    existing = db.execute(
                        """SELECT request_id, status, receiver_id, initial_message FROM social_requests
                           WHERE sender_id=? AND client_request_id=?""",
                        (sender_id, client_request_id),
                    ).fetchone()
                    if existing:
                        if (
                            existing["status"] == "pending"
                            and str(existing["receiver_id"]) == receiver_id
                            and existing["initial_message"] == initial_message
                        ):
                            return jsonify({"success": True, "requestId": existing["request_id"], "duplicate": True})
                        return jsonify({"success": False, "error": "This request identifier was already used"}), 409

                if _are_friends(db, sender_id, receiver_id):
                    return jsonify({"success": False, "error": "You are already friends"}), 409
                incoming = db.execute(
                    """SELECT 1 FROM social_requests WHERE sender_id=? AND receiver_id=?
                       AND status='pending'""",
                    (receiver_id, sender_id),
                ).fetchone()
                if incoming:
                    return jsonify({"success": False, "error": "This player already sent you a request. Open incoming requests."}), 409
                outgoing = db.execute(
                    """SELECT 1 FROM social_requests WHERE sender_id=? AND receiver_id=?
                       AND status='pending'""",
                    (sender_id, receiver_id),
                ).fetchone()
                if outgoing:
                    return jsonify({"success": False, "error": "Your request is still waiting for a response"}), 409
                recently_declined = db.execute(
                    """SELECT resolved_at FROM social_requests
                       WHERE sender_id=? AND receiver_id=? AND status='declined'
                       ORDER BY resolved_at DESC LIMIT 1""",
                    (sender_id, receiver_id),
                ).fetchone()
                if recently_declined and recently_declined["resolved_at"] and now - int(recently_declined["resolved_at"]) < DECLINED_REQUEST_COOLDOWN:
                    return jsonify({"success": False, "error": "Wait before sending another request to this player"}), 429
                request_count = db.execute(
                    "SELECT COUNT(*) FROM social_requests WHERE sender_id=? AND created_at>?",
                    (sender_id, now - 3600),
                ).fetchone()[0]
                if int(request_count) >= REQUESTS_PER_HOUR:
                    return jsonify({"success": False, "error": "Too many requests. Try again later."}), 429

                request_id = secrets.token_urlsafe(18)
                db.execute(
                    """INSERT INTO social_requests
                       (request_id, sender_id, receiver_id, initial_message,
                        client_request_id, status, created_at)
                       VALUES(?,?,?,?,?,'pending',?)""",
                    (request_id, sender_id, receiver_id, initial_message, client_request_id, now),
                )
            return jsonify({"success": True, "requestId": request_id}), 201
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400

    @bp.post("/wallet/social/requests/respond")
    def respond_to_request():
        try:
            data, user = body_and_user()
            request_id = data.get("requestId")
            accept = data.get("accept")
            if not isinstance(request_id, str) or not request_id or len(request_id) > 64:
                raise ValueError("A valid request ID is required")
            if not isinstance(accept, bool):
                raise ValueError("Choose whether to accept or decline")
            now = _now()
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                incoming = db.execute(
                    """SELECT request_id, sender_id, receiver_id, initial_message, status
                       FROM social_requests WHERE request_id=? AND receiver_id=?""",
                    (request_id, str(user["id"])),
                ).fetchone()
                if not incoming:
                    return jsonify({"success": False, "error": "Request not found"}), 404
                if incoming["status"] != "pending":
                    return jsonify({"success": False, "error": "Request already processed"}), 409
                sender_id = str(incoming["sender_id"])
                receiver_id = str(user["id"])
                if accept:
                    low, high = _pair(sender_id, receiver_id)
                    db.execute(
                        """INSERT OR IGNORE INTO social_friendships
                           (user_low, user_high, created_at) VALUES(?,?,?)""",
                        (low, high, now),
                    )
                    if incoming["initial_message"]:
                        db.execute(
                            """INSERT OR IGNORE INTO social_messages
                               (sender_id, recipient_id, content, client_message_id, created_at)
                               VALUES(?,?,?,?,?)""",
                            (
                                sender_id,
                                receiver_id,
                                incoming["initial_message"],
                                "request:" + request_id,
                                now,
                            ),
                        )
                    status = "accepted"
                else:
                    status = "declined"
                db.execute(
                    "UPDATE social_requests SET status=?, resolved_at=? WHERE request_id=?",
                    (status, now, request_id),
                )
            return jsonify({"success": True, "status": status})
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400

    @bp.post("/wallet/social/messages")
    def list_messages():
        try:
            data, user = body_and_user()
            with connect(db_path) as db:
                peer = _profile_by_public_id(db, data.get("peerPublicId"))
                if not peer:
                    return jsonify({"success": False, "error": "Player not found"}), 404
                current_id = str(user["id"])
                peer_id = str(peer["telegram_id"])
                if not _are_friends(db, current_id, peer_id):
                    return jsonify({"success": False, "error": "Accept a friend request before opening this chat"}), 403
                rows = db.execute(
                    """SELECT id, sender_id, recipient_id, content, client_message_id, created_at
                       FROM social_messages
                       WHERE (sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)
                       ORDER BY id DESC LIMIT ?""",
                    (current_id, peer_id, peer_id, current_id, MESSAGE_PAGE_SIZE),
                ).fetchall()
                db.execute(
                    """UPDATE social_messages SET read_at=?
                       WHERE sender_id=? AND recipient_id=? AND read_at IS NULL""",
                    (_now(), peer_id, current_id),
                )
                messages = [_message_payload(row, current_id) for row in reversed(rows)]
                peer_payload = _profile_payload(peer)
            return jsonify({"success": True, "peer": peer_payload, "messages": messages})
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400

    @bp.post("/wallet/social/messages/send")
    def send_message():
        try:
            data, user = body_and_user()
            target_public_id = data.get("peerPublicId")
            content = _plain_text(data.get("content", ""), MAX_MESSAGE_CHARS, "Message")
            client_message_id = data.get("clientMessageId")
            if not isinstance(client_message_id, str) or not client_message_id or len(client_message_id) > 64:
                raise ValueError("A valid message identifier is required")
            now = _now()
            with connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                peer = _profile_by_public_id(db, target_public_id)
                if not peer:
                    return jsonify({"success": False, "error": "Player not found"}), 404
                sender_id = str(user["id"])
                recipient_id = str(peer["telegram_id"])
                if not _are_friends(db, sender_id, recipient_id):
                    return jsonify({"success": False, "error": "Accept a friend request before sending messages"}), 403
                existing = db.execute(
                    """SELECT id, sender_id, recipient_id, content, client_message_id, created_at
                       FROM social_messages WHERE sender_id=? AND client_message_id=?""",
                    (sender_id, client_message_id),
                ).fetchone()
                if existing:
                    if str(existing["recipient_id"]) != recipient_id or existing["content"] != content:
                        return jsonify({"success": False, "error": "Message identifier was already used"}), 409
                    return jsonify({
                        "success": True,
                        "message": _message_payload(existing, sender_id),
                        "duplicate": True,
                    })
                recent_count = db.execute(
                    "SELECT COUNT(*) FROM social_messages WHERE sender_id=? AND created_at>?",
                    (sender_id, now - 60),
                ).fetchone()[0]
                if int(recent_count) >= MESSAGES_PER_MINUTE:
                    return jsonify({"success": False, "error": "You are sending messages too quickly. Wait a moment."}), 429
                cursor = db.execute(
                    """INSERT INTO social_messages
                       (sender_id, recipient_id, content, client_message_id, created_at)
                       VALUES(?,?,?,?,?)""",
                    (sender_id, recipient_id, content, client_message_id, now),
                )
                row = db.execute(
                    """SELECT id, sender_id, recipient_id, content, client_message_id, created_at
                       FROM social_messages WHERE id=?""",
                    (cursor.lastrowid,),
                ).fetchone()
            return jsonify({"success": True, "message": _message_payload(row, sender_id)}), 201
        except PermissionError as error:
            return jsonify({"success": False, "error": str(error)}), 401
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400

    app.register_blueprint(bp)
