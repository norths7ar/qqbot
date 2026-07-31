from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

_RELATIVE_PATTERN = re.compile(
    r"^(?P<amount>\d{1,4})\s*(?P<unit>分钟|小时|天)后\s*(?P<content>.+)$"
)
_DAY_PATTERN = re.compile(
    r"^(?P<day>今天|明天)\s*(?P<hour>\d{1,2})[:：点时]"
    r"(?P<minute>\d{1,2})?\s*(?P<content>.+)$"
)
_ABSOLUTE_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{1,2}-\d{1,2})\s+"
    r"(?P<hour>\d{1,2})[:：](?P<minute>\d{1,2})\s+(?P<content>.+)$"
)


def parse_reminder(
    text: str,
    *,
    now: datetime | None = None,
) -> tuple[datetime, str]:
    current = now or datetime.now(LOCAL_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=LOCAL_TIMEZONE)
    normalized = text.strip()

    relative = _RELATIVE_PATTERN.fullmatch(normalized)
    if relative:
        amount = int(relative.group("amount"))
        unit = relative.group("unit")
        delta = {
            "分钟": timedelta(minutes=amount),
            "小时": timedelta(hours=amount),
            "天": timedelta(days=amount),
        }[unit]
        return current + delta, relative.group("content").strip()

    day_match = _DAY_PATTERN.fullmatch(normalized)
    if day_match:
        day_offset = 1 if day_match.group("day") == "明天" else 0
        due_date = (current + timedelta(days=day_offset)).date()
        due_at = datetime(
            due_date.year,
            due_date.month,
            due_date.day,
            int(day_match.group("hour")),
            int(day_match.group("minute") or 0),
            tzinfo=LOCAL_TIMEZONE,
        )
        return due_at, day_match.group("content").strip()

    absolute = _ABSOLUTE_PATTERN.fullmatch(normalized)
    if absolute:
        due_date = datetime.strptime(absolute.group("date"), "%Y-%m-%d").date()
        due_at = datetime(
            due_date.year,
            due_date.month,
            due_date.day,
            int(absolute.group("hour")),
            int(absolute.group("minute")),
            tzinfo=LOCAL_TIMEZONE,
        )
        return due_at, absolute.group("content").strip()

    raise ValueError(
        "时间格式没看懂。示例：/提醒 30分钟后 关火；"
        "/提醒 明天 20:00 开黑；/提醒 2026-08-01 09:30 起床"
    )
