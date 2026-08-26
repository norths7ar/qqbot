import unittest

import httpx

from qqbot.integrations.vision import ImageContentLoader, detect_image_mime


class ImageTypeTests(unittest.TestCase):
    def test_detects_supported_image_signatures(self) -> None:
        cases = {
            b"\xff\xd8\xffrest": "image/jpeg",
            b"\x89PNG\r\n\x1a\nrest": "image/png",
            b"GIF89arest": "image/gif",
            b"RIFF\x00\x00\x00\x00WEBPrest": "image/webp",
            b"BMrest": "image/bmp",
        }
        for content, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(detect_image_mime(content), expected)

    def test_rejects_non_image_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持"):
            detect_image_mime(b"<html>not an image</html>")


class ImageContentLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_bounded_images_as_model_content_parts(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nimage-data",
                request=request,
            )

        loader = ImageContentLoader(
            timeout_seconds=10,
            max_images=2,
            max_image_bytes=1024,
            transport=httpx.MockTransport(handler),
        )
        parts = await loader.content_parts(
            [
                "https://cdn.example/1.png",
                "https://cdn.example/2.png",
                "https://cdn.example/3.png",
            ]
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/png"))

    async def test_rejects_declared_oversized_image(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Length": "2048"},
                content=b"\x89PNG\r\n\x1a\n",
                request=request,
            )

        loader = ImageContentLoader(
            timeout_seconds=10,
            max_images=1,
            max_image_bytes=1024,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(ValueError, "超过"):
            await loader.content_parts(["https://cdn.example/large.png"])
