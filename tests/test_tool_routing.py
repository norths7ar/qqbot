import unittest

from qqbot.tool_routing import FunctionCall, is_explicit_command, parse_function_call


class ToolRoutingTests(unittest.TestCase):
    def test_recognizes_explicit_command_after_mention_whitespace(self) -> None:
        self.assertTrue(is_explicit_command("  /天气 北京"))
        self.assertFalse(is_explicit_command("天气 北京"))

    def test_parses_registered_function_and_preserves_arguments(self) -> None:
        self.assertEqual(
            parse_function_call("天气 北京"),
            FunctionCall(name="weather", arguments="北京"),
        )
        self.assertEqual(
            parse_function_call("提醒  30分钟后 关火"),
            FunctionCall(name="create_reminder", arguments="30分钟后 关火"),
        )
        self.assertEqual(
            parse_function_call("查询天气 上海"),
            FunctionCall(name="weather", arguments="上海"),
        )

    def test_parses_no_argument_function(self) -> None:
        self.assertEqual(
            parse_function_call("菜单"),
            FunctionCall(name="menu", arguments=""),
        )
        self.assertEqual(
            parse_function_call("历史上的今天"),
            FunctionCall(name="history_today", arguments=""),
        )

    def test_does_not_guess_natural_language_intent(self) -> None:
        self.assertIsNone(parse_function_call("北京天气怎么样"))
        self.assertIsNone(parse_function_call("天气之子好看吗"))
        self.assertIsNone(parse_function_call("提醒我30分钟后关火"))

    def test_unknown_function_name_falls_through_to_ai(self) -> None:
        self.assertIsNone(parse_function_call("天气之子 北京"))
