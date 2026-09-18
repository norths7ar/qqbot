import unittest

from qqbot.integrations.web import find_bilibili_reference


class BilibiliReferenceTests(unittest.TestCase):
    def test_extracts_bvid(self) -> None:
        self.assertEqual(
            find_bilibili_reference("看看 BV1xx411c7mD"),
            "BV1xx411c7mD",
        )

    def test_extracts_short_url(self) -> None:
        self.assertEqual(
            find_bilibili_reference("https://b23.tv/AbCd12"),
            "https://b23.tv/AbCd12",
        )

    def test_ignores_unrelated_text(self) -> None:
        self.assertIsNone(find_bilibili_reference("今天吃什么"))
