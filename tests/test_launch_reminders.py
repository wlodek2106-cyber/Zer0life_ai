import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from launch_reminders import (
    LAUNCH_DATE,
    LAUNCH_REMINDER_CHANNEL_URL,
    LAUNCH_REMINDER_TEXT,
    LAUNCH_REMINDER_TIMEZONE,
    LaunchReminderPreferences,
    last_launch_reminder_at,
    next_launch_reminder_at,
)


class LaunchReminderScheduleTests(unittest.TestCase):
    def test_schedules_today_when_before_six_pm_in_warsaw(self):
        now = datetime(2026, 10, 22, 17, 59, tzinfo=LAUNCH_REMINDER_TIMEZONE)

        self.assertEqual(
            datetime(2026, 10, 22, 18, 0, tzinfo=LAUNCH_REMINDER_TIMEZONE),
            next_launch_reminder_at(now),
        )

    def test_schedules_tomorrow_after_six_pm_in_warsaw(self):
        now = datetime(2026, 10, 22, 18, 1, tzinfo=LAUNCH_REMINDER_TIMEZONE)

        self.assertEqual(
            datetime(2026, 10, 23, 18, 0, tzinfo=LAUNCH_REMINDER_TIMEZONE),
            next_launch_reminder_at(now),
        )

    def test_final_reminder_is_on_the_day_before_launch(self):
        now = datetime(2026, 10, 31, 17, 59, tzinfo=LAUNCH_REMINDER_TIMEZONE)

        self.assertEqual(
            datetime(2026, 10, 31, 18, 0, tzinfo=LAUNCH_REMINDER_TIMEZONE),
            next_launch_reminder_at(now),
        )
        self.assertEqual(
            datetime(2026, 10, 31, 18, 0, tzinfo=LAUNCH_REMINDER_TIMEZONE),
            last_launch_reminder_at(),
        )

    def test_campaign_does_not_send_after_final_reminder_or_on_launch_day(self):
        after_final_reminder = datetime(
            2026, 10, 31, 18, 1, tzinfo=LAUNCH_REMINDER_TIMEZONE
        )
        on_launch_day = datetime(2026, 11, 1, 9, 0, tzinfo=LAUNCH_REMINDER_TIMEZONE)

        self.assertIsNone(next_launch_reminder_at(after_final_reminder))
        self.assertIsNone(next_launch_reminder_at(on_launch_day))
        self.assertEqual(datetime(2026, 11, 1).date(), LAUNCH_DATE)

    def test_rejects_naive_datetime(self):
        with self.assertRaises(ValueError):
            next_launch_reminder_at(datetime(2026, 10, 22, 17, 0))

    def test_message_is_english_and_includes_the_official_channel(self):
        self.assertIn("November 1, 2026", LAUNCH_REMINDER_TEXT)
        self.assertIn(LAUNCH_REMINDER_CHANNEL_URL, LAUNCH_REMINDER_TEXT)
        self.assertIn("/stopreminders", LAUNCH_REMINDER_TEXT)

    def test_timezone_is_the_user_selected_warsaw_timezone(self):
        self.assertEqual(
            "Europe/Warsaw",
            getattr(LAUNCH_REMINDER_TIMEZONE, "key", None),
        )


class LaunchReminderPreferenceTests(unittest.TestCase):
    def test_opt_out_persists_and_can_be_reversed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launch_reminder_preferences.json"
            preferences = LaunchReminderPreferences(path)

            self.assertEqual(set(), preferences.disabled_user_ids())
            preferences.set_enabled(12345, False)
            self.assertEqual({"12345"}, LaunchReminderPreferences(path).disabled_user_ids())

            preferences.set_enabled("12345", True)
            self.assertEqual(set(), LaunchReminderPreferences(path).disabled_user_ids())

    def test_corrupt_preferences_fail_closed_instead_of_clearing_opt_outs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launch_reminder_preferences.json"
            path.write_text("{broken", encoding="utf-8")
            preferences = LaunchReminderPreferences(path)

            with self.assertRaises(json.JSONDecodeError):
                preferences.disabled_user_ids()
            with self.assertRaises(json.JSONDecodeError):
                preferences.set_enabled(12345, True)

            self.assertEqual("{broken", path.read_text(encoding="utf-8"))

    def test_invalid_preferences_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launch_reminder_preferences.json"
            path.write_text(
                json.dumps({"version": 1, "disabled_user_ids": [12345]}),
                encoding="utf-8",
            )
            preferences = LaunchReminderPreferences(path)

            with self.assertRaises(ValueError):
                preferences.disabled_user_ids()


if __name__ == "__main__":
    unittest.main()
