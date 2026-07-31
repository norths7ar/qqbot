import unittest

from qqbot.plain_text import to_qq_plain_text


class PlainTextFormattingTests(unittest.TestCase):
    def test_removes_common_markdown_syntax(self) -> None:
        markdown = (
            "# 标题\n\n"
            "- **重点**\n"
            "> 引用\n"
            "```python\n"
            "print(`hello`)\n"
            "```\n"
            "[来源](https://example.com)"
        )

        self.assertEqual(
            to_qq_plain_text(markdown),
            ("标题\n\n· 重点\n引用\n\nprint(hello)\n\n来源（https://example.com）"),
        )

    def test_preserves_normal_asterisks_in_math(self) -> None:
        self.assertEqual(to_qq_plain_text("2 * 3 = 6"), "2 * 3 = 6")
