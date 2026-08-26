from __future__ import annotations

import json
import logging
import msvcrt
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO


logger = logging.getLogger(__name__)


class BotAlreadyRunningError(RuntimeError):
    pass


class BotProcessGuard:
    """Hold a Windows file lock and publish the current bot process identity."""

    def __init__(self, runtime_directory: Path, entrypoint: Path) -> None:
        self.runtime_directory = runtime_directory.resolve()
        self.entrypoint = entrypoint.resolve()
        self.lock_path = self.runtime_directory / "qqbot.lock"
        self.state_path = self.runtime_directory / "qqbot.json"
        self.instance_id = uuid.uuid4().hex
        self.started_at = datetime.now(UTC).isoformat()
        self._lock_file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._lock_file is not None:
            raise RuntimeError("bot process guard is already acquired")

        self.runtime_directory.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_path.open("a+b")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
            os.fsync(lock_file.fileno())
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            lock_file.close()
            raise BotAlreadyRunningError(
                "another qqbot process already holds the project runtime lock"
            ) from error

        self._lock_file = lock_file
        try:
            self._write_state()
        except Exception:
            self._unlock()
            raise

    def release(self) -> None:
        if self._lock_file is None:
            return
        self._remove_owned_state()
        self._unlock()

    def mark_ready(self, *, memory_schema_version: int | str) -> dict[str, object]:
        """Publish readiness only after NoneBot and the memory stores initialize."""
        if self._lock_file is None:
            raise RuntimeError("bot process guard must be acquired before ready")
        state = self._read_state()
        if state.get("instance_id") != self.instance_id:
            raise RuntimeError("bot process state is not owned by this instance")
        state["ready"] = True
        state["status"] = "ready"
        state["ready_at"] = datetime.now(UTC).isoformat()
        state["memory_schema_version"] = str(memory_schema_version)
        self._write_state_payload(state)
        return state

    def __enter__(self) -> BotProcessGuard:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def _write_state(self) -> None:
        self._write_state_payload(
            {
                "state_version": 2,
                "status": "starting",
                "ready": False,
                "ready_at": None,
                "pid": os.getpid(),
                "started_at": self.started_at,
                "git_commit": self._git_commit(),
                "python": str(Path(sys.executable).resolve()),
                "python_prefix": str(Path(sys.prefix).resolve()),
                "python_base_prefix": str(Path(sys.base_prefix).resolve()),
                "entrypoint": str(self.entrypoint),
                "memory_schema_version": "unknown",
                "instance_id": self.instance_id,
                "stdout": os.environ.get("QQBOT_STDOUT_PATH", ""),
                "stderr": os.environ.get("QQBOT_STDERR_PATH", ""),
            }
        )

    def _write_state_payload(self, state: dict[str, object]) -> None:
        temporary_path = self.state_path.with_name(
            f"{self.state_path.name}.{os.getpid()}.tmp"
        )
        temporary_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.state_path)

    def _read_state(self) -> dict[str, object]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
            raise RuntimeError("bot process state is unavailable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("bot process state is not an object")
        return payload

    def _git_commit(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.entrypoint.parent,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning("Unable to determine qqbot git commit: %s", error)
            return "unknown"
        commit = result.stdout.strip() if result.returncode == 0 else ""
        if not commit:
            logger.warning(
                "Unable to determine qqbot git commit (exit code %s)",
                result.returncode,
            )
            return "unknown"
        return commit

    def _remove_owned_state(self) -> None:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if state.get("instance_id") == self.instance_id:
            self.state_path.unlink(missing_ok=True)

    def _unlock(self) -> None:
        lock_file = self._lock_file
        if lock_file is None:
            return
        try:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            lock_file.close()
            self._lock_file = None
