from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True, slots=True)
class ImageData:
    mime_type: str
    content: bytes


def detect_image_mime(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"BM"):
        return "image/bmp"
    raise ValueError("不支持的图片格式")


class ImageContentLoader:
    """Download bounded OneBot images into model-ready data URL content parts."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_images: int,
        max_image_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_images = max_images
        self._max_image_bytes = max_image_bytes
        self._transport = transport

    async def content_parts(
        self,
        image_urls: Sequence[str],
    ) -> list[dict[str, object]]:
        urls = tuple(dict.fromkeys(image_urls))[: self._max_images]
        if not urls:
            raise ValueError("没有可处理的图片")

        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        ) as http_client:
            images = await asyncio.gather(
                *(self._download_image(http_client, url) for url in urls)
            )
            return [
                {
                    "type": "image_url",
                    "image_url": {"url": self._as_data_url(image)},
                }
                for image in images
            ]

    async def _download_image(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> ImageData:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("图片地址无效")

        content = bytearray()
        async with client.stream(
            "GET",
            url,
            follow_redirects=True,
            headers={"User-Agent": "qqbot/1.0 image-fetcher"},
        ) as response:
            response.raise_for_status()
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > self._max_image_bytes:
                raise ValueError("图片超过大小限制")
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > self._max_image_bytes:
                    raise ValueError("图片超过大小限制")

        raw = bytes(content)
        return ImageData(mime_type=detect_image_mime(raw), content=raw)

    @staticmethod
    def _as_data_url(image: ImageData) -> str:
        encoded = base64.b64encode(image.content).decode("ascii")
        return f"data:{image.mime_type};base64,{encoded}"
