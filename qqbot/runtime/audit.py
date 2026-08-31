from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret",
)


class AuditLog:
    """Write bounded, redacted JSONL events for local request tracing."""

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 3,
        text_limit: int = 4000,
    ) -> None:
        self.path = path
        self.enabled = enabled
        self.text_limit = max(100, text_limit)
        self._handler: RotatingFileHandler | None = None
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handler = RotatingFileHandler(
                path,
                maxBytes=max(1024, max_bytes),
                backupCount=max(1, backup_count),
                encoding="utf-8",
            )
            self._handler.setFormatter(logging.Formatter("%(message)s"))

    def record(
        self,
        event: str,
        *,
        trace_id: str | None = None,
        **fields: object,
    ) -> None:
        if self._handler is None:
            return
        try:
            payload: dict[str, object] = {
                "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "event": event,
                "process_id": os.getpid(),
            }
            if trace_id:
                payload["trace_id"] = trace_id
            payload.update(
                {
                    key: _sanitize(value, key=key, text_limit=self.text_limit)
                    for key, value in fields.items()
                }
            )
            message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            record = logging.LogRecord(
                name="qqbot.audit",
                level=logging.INFO,
                pathname=str(self.path),
                lineno=0,
                msg=message,
                args=(),
                exc_info=None,
            )
            self._handler.handle(record)
        except Exception:
            logging.getLogger(__name__).exception("Failed to write qqbot audit event")

    def close(self) -> None:
        if self._handler is not None:
            self._handler.close()
            self._handler = None


def _sanitize(value: object, *, key: str, text_limit: int) -> Any:
    normalized_key = key.casefold()
    if (
        any(part in normalized_key for part in _SENSITIVE_KEY_PARTS)
        or normalized_key == "token"
        or normalized_key.endswith("_token")
    ):
        return "[REDACTED]"
    if isinstance(value, bytes):
        return f"[BYTES:{len(value)}]"
    if isinstance(value, str):
        if value.startswith("data:image/") or "base64," in value[:100]:
            return "[REDACTED_IMAGE_DATA]"
        return value[:text_limit]
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize(
                item_value,
                key=str(item_key),
                text_limit=text_limit,
            )
            for item_key, item_value in list(value.items())[:50]
        }
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_sanitize(item, key=key, text_limit=text_limit) for item in value[:50]]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:text_limit]
