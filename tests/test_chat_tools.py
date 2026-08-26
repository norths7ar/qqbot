import unittest

from pydantic import SecretStr

from qqbot.chat.config import Config
from qqbot.chat.tools import build_chat_tools


def _tool_names(tools: list[dict[str, object]]) -> set[str]:
    return {
        function["name"]
        for tool in tools
        if isinstance(function := tool.get("function"), dict)
        and isinstance(function.get("name"), str)
    }


class ChatToolAssemblyTests(unittest.TestCase):
    def _config(self, *, history_today_enabled: bool) -> Config:
        return Config(
            llm_api_key=SecretStr("test"),
            history_today_enabled=history_today_enabled,
        )

    def test_history_today_is_absent_when_disabled(self) -> None:
        tools, direct_result_tools = build_chat_tools(
            self._config(history_today_enabled=False)
        )

        names = _tool_names(tools)
        self.assertNotIn("get_history_today", names)
        self.assertNotIn("get_history_today", direct_result_tools)
        self.assertIn("web_search", names)

    def test_history_today_is_enabled_as_a_direct_result_tool(self) -> None:
        tools, direct_result_tools = build_chat_tools(
            self._config(history_today_enabled=True)
        )

        names = _tool_names(tools)
        self.assertIn("get_history_today", names)
        self.assertIn("get_history_today", direct_result_tools)
        self.assertIn("recall_memory", names)
