import unittest
from unittest.mock import AsyncMock

from qqbot.web_tools import TavilyClient, find_bilibili_reference


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


class TavilySearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_search_formats_results_for_explicit_command(self) -> None:
        client = TavilyClient("test")
        client._post = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "results": [
                    {
                        "title": "逻辑谜题",
                        "content": "这是一段用于说明谜题答案的网页摘要。",
                        "url": "https://example.com/puzzle",
                    }
                ]
            }
        )

        result = await client.search("三颗子弹", compact=True)

        self.assertEqual(
            result,
            (
                "搜索结果：\n"
                "1. 逻辑谜题\n"
                "摘要：这是一段用于说明谜题答案的网页摘要。\n"
                "链接：https://example.com/puzzle"
            ),
        )

    async def test_search_material_warns_model_about_evidence_quality(self) -> None:
        client = TavilyClient("test")
        client._post = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "results": [
                    {
                        "title": "可能相关的页面",
                        "content": "内容并不完整。",
                        "url": "https://example.com/result",
                    }
                ]
            }
        )

        result = await client.search("含糊的问题")

        self.assertIn("网页片段可能不完整或互相冲突", result)
        self.assertIn("证据不足时明确说明", result)
