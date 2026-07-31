from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FunctionCall:
    name: str
    arguments: str


FUNCTION_NAMES = {
    "菜单": "menu",
    "帮助": "menu",
    "天气": "weather",
    "查询天气": "weather",
    "查天气": "weather",
    "运势": "fortune",
    "新闻": "news",
    "新闻详情": "news_detail",
    "搜索": "search",
    "历史上的今天": "history_today",
    "提醒": "create_reminder",
    "提醒列表": "list_reminders",
    "取消提醒": "cancel_reminder",
    "总结": "summarize_group",
    "清空对话": "clear_chat",
    "我的记忆": "my_memories",
    "忘记我": "forget_me",
}


def is_explicit_command(text: str) -> bool:
    return text.lstrip().startswith("/")


def parse_function_call(text: str) -> FunctionCall | None:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    name = FUNCTION_NAMES.get(parts[0])
    if name is None:
        return None
    arguments = parts[1].strip() if len(parts) == 2 else ""
    return FunctionCall(name=name, arguments=arguments)
