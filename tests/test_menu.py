import unittest

from qqbot.menu import build_menu


class MenuTests(unittest.TestCase):
    def test_regular_menu_is_stable(self) -> None:
        first = build_menu(is_superuser=False)
        second = build_menu(is_superuser=False)

        self.assertEqual(first, second)
        self.assertIn("@我 总结 50", first)
        self.assertNotIn("提醒", first)
        self.assertNotIn("天气", first)
        self.assertNotIn("运势", first)
        self.assertNotIn("新闻", first)
        self.assertIn("菜单", first)
        self.assertNotIn("管理员命令", first)

    def test_superuser_menu_adds_admin_commands(self) -> None:
        menu = build_menu(is_superuser=True)

        self.assertIn("管理员命令（仅使用 / 前缀）", menu)
        self.assertIn("/清空群聊记录 确认", menu)

    def test_history_can_be_hidden(self) -> None:
        menu = build_menu(is_superuser=False, history_today_enabled=False)

        self.assertNotIn("历史上的今天", menu)
