import json
import unittest

import httpx

from qqbot.integrations.vision import (
    MiMoResponseError,
    MiMoVisionClient,
    detect_image_mime,
)


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

    def test_rejects_file_content_that_is_not_a_supported_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持"):
            detect_image_mime(b"<html>not an image</html>")


class MiMoVisionClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_images_and_sends_bounded_multimodal_payload(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    content=b"\x89PNG\r\n\x1a\nimage-data",
                    request=request,
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": " 看见两张测试图 "}}]},
                request=request,
            )

        client = MiMoVisionClient(
            "secret",
            "https://api.example/v1",
            "mimo-v2.5",
            timeout_seconds=10,
            max_images=2,
            max_image_bytes=1024,
            transport=httpx.MockTransport(handler),
        )

        result = await client.observe(
            [
                "https://cdn.example/1.png",
                "https://cdn.example/2.png",
                "https://cdn.example/3.png",
            ],
            "图里有什么？",
        )

        self.assertEqual(result, "看见两张测试图")
        get_requests = [request for request in requests if request.method == "GET"]
        self.assertEqual(len(get_requests), 2)
        post_request = next(request for request in requests if request.method == "POST")
        self.assertEqual(post_request.headers["api-key"], "secret")
        payload = json.loads(post_request.content)
        user_content = payload["messages"][1]["content"]
        image_parts = [part for part in user_content if part["type"] == "image_url"]
        self.assertEqual(len(image_parts), 2)
        self.assertTrue(
            image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    async def test_rejects_declared_oversized_image_before_api_request(self) -> None:
        methods: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return httpx.Response(
                200,
                headers={"Content-Length": "2048"},
                content=b"\x89PNG\r\n\x1a\n",
                request=request,
            )

        client = MiMoVisionClient(
            "secret",
            "https://api.example/v1",
            "mimo-v2.5",
            timeout_seconds=10,
            max_images=1,
            max_image_bytes=1024,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaisesRegex(ValueError, "超过"):
            await client.observe(["https://cdn.example/large.png"], "看看")
        self.assertEqual(methods, ["GET"])

    async def test_reports_refusal_and_finish_reason_for_empty_content(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    content=b"\x89PNG\r\n\x1a\nimage-data",
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "content_filter",
                            "message": {
                                "content": None,
                                "refusal": "safety policy",
                            },
                        }
                    ]
                },
                request=request,
            )

        client = MiMoVisionClient(
            "secret",
            "https://api.example/v1",
            "mimo-v2.5",
            timeout_seconds=10,
            max_images=1,
            max_image_bytes=1024,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(MiMoResponseError) as raised:
            await client.observe(["https://cdn.example/image.png"], "看看")

        diagnostic = str(raised.exception)
        self.assertIn("content_filter", diagnostic)
        self.assertIn("safety policy", diagnostic)
        self.assertNotIn("secret", diagnostic)

    async def test_reports_bounded_http_error_response(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    content=b"\x89PNG\r\n\x1a\nimage-data",
                    request=request,
                )
            return httpx.Response(
                400,
                json={"error": {"code": "invalid_image", "message": "bad image"}},
                request=request,
            )

        client = MiMoVisionClient(
            "secret",
            "https://api.example/v1",
            "mimo-v2.5",
            timeout_seconds=10,
            max_images=1,
            max_image_bytes=1024,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(MiMoResponseError) as raised:
            await client.observe(["https://cdn.example/image.png"], "看看")

        diagnostic = str(raised.exception)
        self.assertIn("MiMo HTTP 400", diagnostic)
        self.assertIn("invalid_image", diagnostic)
        self.assertNotIn("secret", diagnostic)


if __name__ == "__main__":
    unittest.main()
