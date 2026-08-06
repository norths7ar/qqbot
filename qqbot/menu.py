from __future__ import annotations


def build_menu(*, is_superuser: bool, history_today_enabled: bool = True) -> str:
    lines = [
        "使用方式",
        "",
        "平时直接 @我。要稳定调用功能，请在功能名后加空格和参数，例如：",
        "@我 总结 50",
        "",
        "功能",
        "菜单",
        "搜索 关键词",
        "总结 [消息条数]",
        "清空对话",
        "我的记忆",
        "忘记我 确认",
        "",
        "以上功能也可使用 / 前缀，例如：/搜索 关键词。",
        "发送 B 站链接或 BV 号会自动解析。",
    ]
    if history_today_enabled:
        history_position = lines.index("总结 [消息条数]")
        lines.insert(history_position, "历史上的今天")
    if is_superuser:
        lines.extend(
            [
                "",
                "管理员命令（仅使用 / 前缀）",
                "/记住 @群友 内容",
                "/查看记忆 @群友",
                "/删除记忆 编号",
                "/清空本群对话  清除LLM短期对话上下文",
                "/清空群聊记录 确认  删除总结用持久记录",
            ]
        )
    return "\n".join(lines)
