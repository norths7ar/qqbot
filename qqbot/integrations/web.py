from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

USER_AGENT = "qqbot/0.1 (private NoneBot2 bot)"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
_BVID_PATTERN = re.compile(r"\b(BV[0-9A-Za-z]{10})\b", re.IGNORECASE)
_B23_PATTERN = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+")


class TavilyClient:
    def __init__(self, api_key: str, *, timeout_seconds: float = 20) -> None:
        self.api_key = api_key.strip()
        self.timeout = httpx.Timeout(timeout_seconds)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str) -> str:
        if not self.available:
            return (
                "联网搜索尚未配置。请让管理员在 .env 中填写 "
                "TAVILY_API_KEY 后重启机器人。"
            )
        payload = {
            "query": query[:500],
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
        }
        response = await self._post("/search", payload)
        results = response.get("results")
        if not isinstance(results, list) or not results:
            return "没有搜到可靠结果。"

        lines = [
            "联网检索材料（网页片段可能不完整或互相冲突；"
            "请核对来源后回答，证据不足时明确说明）："
        ]
        for index, item in enumerate(results, start=1):
            if not isinstance(item, Mapping):
                continue
            title = _clean_text(item.get("title"))
            content = _clean_text(item.get("content"))
            url = _safe_http_url(item.get("url"))
            lines.append(f"{index}. {title or '未命名结果'}")
            if content:
                lines.append(f"摘要：{content[:500]}")
            if url:
                lines.append(f"链接：{url}")
        return "\n".join(lines)[:7000]

    async def _post(self, path: str, payload: Mapping[str, object]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"https://api.tavily.com{path}",
                headers=headers,
                json=dict(payload),
            )
            response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Tavily response is invalid")
        return data


async def history_today(*, now: datetime | None = None, limit: int = 8) -> str:
    local_now = (now or datetime.now(LOCAL_TIMEZONE)).astimezone(LOCAL_TIMEZONE)
    endpoint = (
        "https://api.wikimedia.org/feed/v1/wikipedia/zh/onthisday/all/"
        f"{local_now.month:02d}/{local_now.day:02d}"
    )
    headers = {"User-Agent": USER_AGENT, "Api-User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        response = await client.get(endpoint)
        response.raise_for_status()
    payload = response.json()
    events = payload.get("events") if isinstance(payload, Mapping) else None
    if not isinstance(events, list) or not events:
        return "今天暂时没有查到历史事件。"

    selected = sorted(
        (event for event in events if isinstance(event, Mapping)),
        key=lambda event: int(event.get("year", 0)),
        reverse=True,
    )[: max(1, min(limit, 12))]
    lines = [f"历史上的今天（{local_now.month}月{local_now.day}日）"]
    for event in selected:
        year = event.get("year", "未知年份")
        text = _clean_text(event.get("text"))
        if text:
            lines.append(f"{year}年：{text}")
    lines.append("来源：维基媒体 On this day")
    return "\n".join(lines)


def find_bilibili_reference(text: str) -> str | None:
    short_url = _B23_PATTERN.search(text)
    if short_url:
        return short_url.group(0)
    bvid = _BVID_PATTERN.search(text)
    return bvid.group(1) if bvid else None


async def fetch_bilibili_video(reference: str) -> str:
    bvid = reference
    if reference.startswith(("http://", "https://")):
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(reference)
            response.raise_for_status()
        final_url = str(response.url)
        match = _BVID_PATTERN.search(final_url)
        if not match:
            raise ValueError("短链接没有解析到 B 站视频")
        bvid = match.group(1)

    match = _BVID_PATTERN.fullmatch(bvid)
    if not match:
        raise ValueError("没有识别到有效 BV 号")
    normalized_bvid = match.group(1)
    async with httpx.AsyncClient(
        timeout=15,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bilibili.com/",
        },
    ) as client:
        response = await client.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": normalized_bvid},
        )
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping) or payload.get("code") != 0:
        message = (
            _clean_text(payload.get("message")) if isinstance(payload, Mapping) else ""
        )
        raise ValueError(message or "B 站接口未返回视频信息")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("B 站视频信息格式异常")

    owner = data.get("owner")
    stats = data.get("stat")
    author = _clean_text(owner.get("name")) if isinstance(owner, Mapping) else ""
    view = _compact_number(stats.get("view")) if isinstance(stats, Mapping) else "未知"
    danmaku = (
        _compact_number(stats.get("danmaku")) if isinstance(stats, Mapping) else "未知"
    )
    title = _clean_text(data.get("title")) or normalized_bvid
    description = _clean_text(data.get("desc"))
    duration = _format_duration(data.get("duration"))
    return "\n".join(
        [
            f"【B站】{title}",
            f"UP：{author or '未知'}　时长：{duration}",
            f"播放：{view}　弹幕：{danmaku}",
            description[:300] if description else "没有简介。",
            f"https://www.bilibili.com/video/{normalized_bvid}",
        ]
    )


def _safe_http_url(value: object) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _compact_number(value: object) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "未知"
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.1f}万"
    return str(number)


def _format_duration(value: object) -> str:
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return "未知"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
