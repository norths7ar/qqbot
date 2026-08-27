import unittest

from qqbot.chat.tools import build_chat_tools


def _tool_names(tools: list[dict[str, object]]) -> set[str]:
    return {
        function["name"]
        for tool in tools
        if isinstance(function := tool.get("function"), dict)
        and isinstance(function.get("name"), str)
    }


class ChatToolAssemblyTests(unittest.TestCase):
    def test_chat_tools_are_read_only_conversation_capabilities(self) -> None:
        tools = build_chat_tools()
        names = _tool_names(tools)
        self.assertNotIn("get_history_today", names)
        self.assertIn("web_search", names)
        self.assertIn("recall_memory", names)
        self.assertIn("get_recent_group_chat", names)
