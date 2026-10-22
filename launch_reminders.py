"""Finite, opt-out-enabled reminders for the Zer0Life AI Trader launch."""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LAUNCH_REMINDER_TIMEZONE = ZoneInfo("Europe/Warsaw")
LAUNCH_REMINDER_CHANNEL_URL = "https://t.me/zer0life_official"
LAUNCH_DATE = date(2026, 11, 1)
LAUNCH_REMINDER_HOUR = 18
LAUNCH_REMINDER_MINUTE = 0
LAST_REMINDER_DATE = LAUNCH_DATE - timedelta(days=1)

LAUNCH_REMINDER_TEXT = (
    "🚀 <b>Zer0Life AI Trader launches on November 1, 2026!</b>\n\n"
    "Follow our official Telegram channel to get the launch link and not miss "
    f"the release: {LAUNCH_REMINDER_CHANNEL_URL}\n\n"
    "Send /stopreminders to stop these launch reminders."
)


def next_launch_reminder_at(now: datetime) -> datetime | None:
    """Return the next 18:00 Warsaw reminder, or None after the campaign ends."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    local_now = now.astimezone(LAUNCH_REMINDER_TIMEZONE)
    if local_now.date() > LAST_REMINDER_DATE:
        return None

    next_run = datetime.combine(
        local_now.date(),
        time(LAUNCH_REMINDER_HOUR, LAUNCH_REMINDER_MINUTE),
        tzinfo=LAUNCH_REMINDER_TIMEZONE,
    )
    if next_run <= local_now:
        next_run += timedelta(days=1)
    if next_run.date() > LAST_REMINDER_DATE:
        return None
    return next_run


def last_launch_reminder_at() -> datetime:
    """Return the final scheduled reminder time, before launch day."""
    return datetime.combine(
        LAST_REMINDER_DATE,
        time(LAUNCH_REMINDER_HOUR, LAUNCH_REMINDER_MINUTE),
        tzinfo=LAUNCH_REMINDER_TIMEZONE,
    )


class LaunchReminderPreferences:
    """Persist per-user launch reminder opt-outs in the Render data directory."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = threading.RLock()

    def disabled_user_ids(self) -> set[str]:
        with self._lock:
            try:
                with self.path.open("r", encoding="utf-8") as preference_file:
                    data = json.load(preference_file)
            except FileNotFoundError:
                return set()

            if (
                not isinstance(data, dict)
                or type(data.get("version")) is not int
                or data["version"] != 1
            ):
                raise ValueError("Unsupported launch reminder preferences format")

            disabled_user_ids = data.get("disabled_user_ids")
            if not isinstance(disabled_user_ids, list) or any(
                not isinstance(user_id, str) or not user_id.isdigit()
                for user_id in disabled_user_ids
            ):
                raise ValueError("Invalid launch reminder opt-out list")
            return set(disabled_user_ids)

    def set_enabled(self, user_id: int | str, enabled: bool) -> None:
        normalized_user_id = str(user_id)
        if not normalized_user_id.isdigit():
            raise ValueError("Telegram user ID must contain digits only")

        with self._lock:
            disabled_user_ids = self.disabled_user_ids()
            if enabled:
                disabled_user_ids.discard(normalized_user_id)
            else:
                disabled_user_ids.add(normalized_user_id)

            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_name(
                f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                with temporary_path.open("w", encoding="utf-8") as preference_file:
                    json.dump(
                        {
                            "version": 1,
                            "disabled_user_ids": sorted(disabled_user_ids),
                        },
                        preference_file,
                        separators=(",", ":"),
                    )
                    preference_file.flush()
                    os.fsync(preference_file.fileno())
                os.replace(temporary_path, self.path)
            finally:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
