"""Explicit opt-in subscriptions and one-time ZRL pre-sale Telegram announcement."""
import json
import os
import threading
from pathlib import Path

class PresaleSubscribers:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Invalid pre-sale subscriber file")
        ids = data.get("subscribers")
        if not isinstance(ids, list) or any(not isinstance(x, str) or not x.isdigit() for x in ids):
            raise ValueError("Invalid pre-sale subscriber list")
        return set(ids)

    def list(self):
        with self.lock:
            return self._load()

    def set(self, user_id, enabled):
        uid = str(user_id)
        if not uid.isdigit():
            raise ValueError("Invalid Telegram user ID")
        with self.lock:
            ids = self._load()
            if enabled:
                ids.add(uid)
            else:
                ids.discard(uid)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"version": 1, "subscribers": sorted(ids)}), encoding="utf-8")
            os.replace(tmp, self.path)
            return len(ids)
