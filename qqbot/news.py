from __future__ import annotations

import re

_DETAIL_HINT = re.compile(
    r"(?:\r?\n)+"
    r"提示[:：]\s*回复数字可查看对应新闻的网页截图\s*"
    r"\r?\n"
    r"例如[:：]\s*回复\s*1\s*查看第一条新闻\s*$"
)


def strip_unavailable_detail_hint(text: str) -> str:
    """Remove the image-only follow-up hint from text-format news."""
    return _DETAIL_HINT.sub("", text).rstrip()
