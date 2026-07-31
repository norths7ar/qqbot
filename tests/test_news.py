import unittest

from qqbot.news import strip_unavailable_detail_hint


class NewsFormattingTests(unittest.TestCase):
    def test_strip_unavailable_detail_hint(self) -> None:
        text = (
            "【知乎热榜】\n\n"
            "1. 一条新闻\n"
            "   链接: https://example.com\n\n"
            "提示: 回复数字可查看对应新闻的网页截图\n"
            "例如: 回复 1 查看第一条新闻\n"
        )

        self.assertEqual(
            strip_unavailable_detail_hint(text),
            "【知乎热榜】\n\n1. 一条新闻\n   链接: https://example.com",
        )

    def test_keep_unrelated_news_text(self) -> None:
        text = "【60秒读懂世界】\n1. 一条新闻"

        self.assertEqual(strip_unavailable_detail_hint(text), text)
