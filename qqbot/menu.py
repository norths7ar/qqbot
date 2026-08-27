from __future__ import annotations


def build_menu(*, is_superuser: bool, history_today_enabled: bool = True) -> str:
    lines = [
        "使用方式",
        "",
        "平时直接 @我。要稳定调用功能，请在功能名后加空格和参数，例如：",
        "@我 总结 50",
        "",
        "功能",
        "发送 B 站链接或 BV 号会自动解析。",
    ]
    if is_superuser:
        lines.extend(
            [
                "",
                "管理员命令（仅使用 / 前缀）",
                "/记住 @群友 内容",
                "/查看记忆 @群友",
                "/删除记忆 编号",
            ]
        )
    return "\n".join(lines)
