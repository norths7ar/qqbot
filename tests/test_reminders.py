import unittest
from datetime import datetime

from qqbot.reminders import LOCAL_TIMEZONE, parse_reminder


class ReminderParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 31, 10, 0, tzinfo=LOCAL_TIMEZONE)

    def test_relative_reminder(self) -> None:
        due_at, content = parse_reminder("30分钟后 关火", now=self.now)

        self.assertEqual(due_at, datetime(2026, 7, 31, 10, 30, tzinfo=LOCAL_TIMEZONE))
        self.assertEqual(content, "关火")

    def test_tomorrow_reminder(self) -> None:
        due_at, content = parse_reminder("明天 20:15 开黑", now=self.now)

        self.assertEqual(due_at, datetime(2026, 8, 1, 20, 15, tzinfo=LOCAL_TIMEZONE))
        self.assertEqual(content, "开黑")

    def test_rejects_unknown_format(self) -> None:
        with self.assertRaises(ValueError):
            parse_reminder("过会儿提醒我", now=self.now)
