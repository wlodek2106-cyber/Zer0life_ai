"""Private, read-only SQLite snapshots and authenticated streaming encryption.

No restore-to-live operation is provided. Only synthetic data belongs in tests.
"""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import time

from nacl import bindings as sodium


REQUIRED_TABLES = frozenset({
    "wallet_accounts", "wallet_ledger", "wallet_challenges",
    "wallet_withdrawals", "wallet_deposits", "wallet_deposit_references",
    "wallet_game_cooldowns", "wallet_farms", "wallet_blackjack_sessions",
    "wallet_spend_events", "wallet_recovery_requests",
    "wallet_legacy_farm_returns", "wallet_mobile_sessions",
    "wallet_withdrawal_approvals", "wallet_merchant_purchases",
})
HISTORICAL_JSON = ("users.json", "leaderboard_stats.json")
MAGIC = b"ZRL-BACKUP-1\n"
CHUNK = 1024 * 1024
ABYTES = sodium.crypto_secretstream_xchacha20poly1305_ABYTES
HEADERBYTES = sodium.crypto_secretstream_xchacha20poly1305_HEADERBYTES
FINAL = sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL
MESSAGE = sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE


def private_write(path, data):
    with open(path, "xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data)


def digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            result.update(chunk)
    return result.hexdigest()


def validate_sqlite(path):
    # mode=ro also prevents a typo from creating an empty "backup".
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not REQUIRED_TABLES <= tables:
            raise ValueError("Expected accounting tables are missing")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("SQLite foreign key check failed")
    finally:
        db.close()


def snapshot_sqlite(source, destination, timeout=120):
    deadline = time.monotonic() + timeout

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("SQLite online backup exceeded deadline")

    source = Path(source).resolve(strict=True)
    private_write(destination, b"")
    original = sqlite3.connect(source.as_uri() + "?mode=ro", timeout=5, uri=True)
    target = sqlite3.connect(destination)
    try:
        # Captures committed WAL pages, without copying raw DB/WAL files or
        # checkpointing/modifying the live ledger.
        original.backup(target, pages=256, progress=progress, sleep=0.05)
    finally:
        target.close()
        original.close()
    validate_sqlite(destination)


def copy_historical(source, destination):
    """Copy the same validated bytes; files are separate from the DB snapshot."""
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Historical JSON must be a regular file")
    before = source.stat()
    raw = source.read_bytes()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size, after.st_mtime_ns, after.st_ino
    ):
        raise ValueError("Historical JSON changed while reading")
    if not isinstance(json.loads(raw), (dict, list)):
        raise ValueError("Historical JSON must contain an object or array")
    private_write(destination, raw)


def encrypt_file(source, destination, key):
    state = sodium.crypto_secretstream_xchacha20poly1305_state()
    header = sodium.crypto_secretstream_xchacha20poly1305_init_push(state, key)
    private_write(destination, b"")
    with open(source, "rb") as src, open(destination, "wb") as dst:
        dst.write(MAGIC + header)
        while True:
            chunk = src.read(CHUNK)
            tag = MESSAGE if chunk else FINAL
            encrypted = sodium.crypto_secretstream_xchacha20poly1305_push(
                state, chunk, tag=tag
            )
            dst.write(struct.pack(">I", len(encrypted)))
            dst.write(encrypted)
            if tag == FINAL:
                return


def decrypt_file(source, destination, key):
    """Offline inspection only: destination must be a new, private file."""
    state = sodium.crypto_secretstream_xchacha20poly1305_state()
    private_write(destination, b"")
    try:
        with open(source, "rb") as src, open(destination, "wb") as dst:
            if src.read(len(MAGIC)) != MAGIC:
                raise ValueError("Unknown archive format")
            header = src.read(HEADERBYTES)
            sodium.crypto_secretstream_xchacha20poly1305_init_pull(state, header, key)
            while True:
                size = src.read(4)
                if len(size) != 4:
                    raise ValueError("Truncated archive")
                length = struct.unpack(">I", size)[0]
                if not ABYTES <= length <= CHUNK + ABYTES:
                    raise ValueError("Invalid archive frame")
                encrypted = src.read(length)
                if len(encrypted) != length:
                    raise ValueError("Truncated archive")
                chunk, tag = sodium.crypto_secretstream_xchacha20poly1305_pull(
                    state, encrypted
                )
                dst.write(chunk)
                if tag == FINAL:
                    if src.read(1):
                        raise ValueError("Trailing archive bytes")
                    return
                if tag != MESSAGE:
                    raise ValueError("Unexpected archive frame")
    except BaseException:
        Path(destination).unlink(missing_ok=True)
        raise
