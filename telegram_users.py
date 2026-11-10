"""Preserve the existing Telegram recipient list on failures and concurrent starts."""

import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading


class TelegramUsers:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                rows = json.load(source)
        except FileNotFoundError:
            return set()
        if not isinstance(rows, list) or any(
            isinstance(row, bool) or not isinstance(row, (str, int))
            or not str(row).isascii() or not str(row).isdigit() for row in rows
        ):
            raise ValueError("Invalid Telegram recipient file; refusing to overwrite it")
        return {str(row) for row in rows}

    def add(self, user_id):
        user_id = str(user_id)
        if not user_id.isascii() or not user_id.isdigit():
            raise ValueError("Invalid Telegram user identifier")
        # flock also protects overlapping processes using this updated writer.
        with self.lock, open(str(self.path) + ".lock", "a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            users = self.read()  # Corrupt/unreadable data must never become an empty list.
            if user_id in users:
                return len(users)
            users.add(user_id)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.path.parent,
                    prefix="." + self.path.name + ".", delete=False,
                ) as output:
                    temporary = output.name
                    json.dump(sorted(users), output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
                temporary = None
            finally:
                if temporary is not None:
                    os.unlink(temporary)
            return len(users)
