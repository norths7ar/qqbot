import json
import tempfile
import unittest
from pathlib import Path

from qqbot.process_runtime import BotAlreadyRunningError, BotProcessGuard


class BotProcessGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.entrypoint = self.root / "bot.py"
        self.entrypoint.touch()

    def test_guard_publishes_and_removes_owned_process_state(self) -> None:
        guard = BotProcessGuard(self.root / "runtime", self.entrypoint)

        guard.acquire()
        state = json.loads(guard.state_path.read_text(encoding="utf-8"))

        self.assertEqual(state["entrypoint"], str(self.entrypoint.resolve()))
        self.assertEqual(state["instance_id"], guard.instance_id)

        guard.release()

        self.assertFalse(guard.state_path.exists())

    def test_second_guard_cannot_acquire_same_project_lock(self) -> None:
        first = BotProcessGuard(self.root / "runtime", self.entrypoint)
        second = BotProcessGuard(self.root / "runtime", self.entrypoint)
        first.acquire()
        self.addCleanup(first.release)

        with self.assertRaises(BotAlreadyRunningError):
            second.acquire()

    def test_stale_state_is_replaced_when_no_process_holds_lock(self) -> None:
        runtime_directory = self.root / "runtime"
        runtime_directory.mkdir()
        state_path = runtime_directory / "qqbot.json"
        state_path.write_text('{"pid": 999999}', encoding="utf-8")
        guard = BotProcessGuard(runtime_directory, self.entrypoint)

        guard.acquire()
        self.addCleanup(guard.release)
        state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertNotEqual(state["pid"], 999999)
        self.assertEqual(state["instance_id"], guard.instance_id)

    def test_different_projects_use_independent_locks(self) -> None:
        first = BotProcessGuard(self.root / "runtime-a", self.entrypoint)
        second = BotProcessGuard(self.root / "runtime-b", self.entrypoint)

        first.acquire()
        second.acquire()
        self.addCleanup(first.release)
        self.addCleanup(second.release)

        self.assertTrue(first.state_path.exists())
        self.assertTrue(second.state_path.exists())

    def test_release_does_not_remove_state_owned_by_another_instance(self) -> None:
        guard = BotProcessGuard(self.root / "runtime", self.entrypoint)
        guard.acquire()
        replacement = {"instance_id": "another-instance", "pid": 12345}
        guard.state_path.write_text(json.dumps(replacement), encoding="utf-8")

        guard.release()

        self.assertEqual(
            json.loads(guard.state_path.read_text(encoding="utf-8")),
            replacement,
        )
