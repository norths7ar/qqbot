from __future__ import annotations

import re

_MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_STAR = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_ITALIC_UNDERSCORE = re.compile(r"(?<!_)_([^_\n]+)_(?!_)")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE)
_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_FENCE_LINE = re.compile(r"^[ \t]*```[^\n]*$", re.MULTILINE)


def to_qq_plain_text(text: str) -> str:
    """Remove common Markdown syntax while preserving readable content."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _MARKDOWN_LINK.sub(r"\1（\2）", cleaned)
    cleaned = _FENCE_LINE.sub("", cleaned)
    cleaned = _HEADING.sub("", cleaned)
    cleaned = _QUOTE.sub("", cleaned)
    cleaned = _BULLET.sub("· ", cleaned)
    cleaned = _BOLD.sub(r"\2", cleaned)
    cleaned = _ITALIC_STAR.sub(r"\1", cleaned)
    cleaned = _ITALIC_UNDERSCORE.sub(r"\1", cleaned)
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
