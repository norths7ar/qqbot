import json
import tempfile
import unittest
from pathlib import Path

from qqbot.memory_replay import build_shadow_replay_report, copy_sqlite_readonly
from qqbot.memory_v2 import ClaimStore


class ShadowReplayReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source.db"
        self.output = self.root / "replay.db"
        store = ClaimStore(self.source)
        store.initialize()
        batch_id = store.start_shadow_batch(1, 10, 11)
        store.finish_shadow_batch(
            batch_id,
            status="completed",
            operation_count=1,
            applied_count=0,
            raw_response=json.dumps({"operations": [{"operation": "ignore"}]}),
            rejection_reasons=("operation[0]: duplicate claim",),
        )

    def test_report_copies_source_and_summarizes_without_source_write(self) -> None:
        source_before = self.source.read_bytes()
        report = build_shadow_replay_report(self.source, self.output)

        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertEqual(report.source_database, str(self.source.resolve()))
        self.assertEqual(report.batch_count, 1)
        self.assertEqual(report.parsed_batch_count, 1)
        self.assertEqual(report.operation_count, 1)
        self.assertEqual(report.operation_types, {"ignore": 1})
        self.assertEqual(report.rejection_count, 1)
        self.assertEqual(report.duplicate_rejection_count, 1)
        self.assertFalse(report.ground_truth_available)

    def test_copy_rejects_same_source_and_existing_target(self) -> None:
        with self.assertRaises(ValueError):
            copy_sqlite_readonly(self.source, self.source)
        self.output.touch()
        with self.assertRaises(FileExistsError):
            copy_sqlite_readonly(self.source, self.output)


if __name__ == "__main__":
    unittest.main()
