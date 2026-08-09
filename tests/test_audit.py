import json
import tempfile
import unittest
from pathlib import Path

from qqbot.audit import AuditLog


class AuditLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temporary_directory.name) / "audit.jsonl"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_writes_correlated_json_event(self) -> None:
        audit = AuditLog(self.log_path, text_limit=100)
        audit.record(
            "tool.completed",
            trace_id="g1-m2",
            group_id=1,
            name="web_search",
            result="搜索结果",
        )
        audit.close()

        payload = json.loads(self.log_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["event"], "tool.completed")
        self.assertEqual(payload["trace_id"], "g1-m2")
        self.assertEqual(payload["name"], "web_search")
        self.assertEqual(payload["result"], "搜索结果")
        self.assertIn("timestamp", payload)

    def test_redacts_secrets_and_image_data(self) -> None:
        audit = AuditLog(self.log_path)
        audit.record(
            "request",
            arguments={
                "query": "显卡价格",
                "api_key": "should-not-appear",
                "nested": {"Authorization": "Bearer secret-value"},
                "image": "data:image/png;base64,secret-image-data",
            },
        )
        audit.close()

        text = self.log_path.read_text(encoding="utf-8")
        payload = json.loads(text)

        self.assertNotIn("should-not-appear", text)
        self.assertNotIn("secret-value", text)
        self.assertNotIn("secret-image-data", text)
        self.assertEqual(payload["arguments"]["api_key"], "[REDACTED]")
        self.assertEqual(
            payload["arguments"]["image"],
            "[REDACTED_IMAGE_DATA]",
        )

    def test_bounds_logged_text(self) -> None:
        audit = AuditLog(self.log_path, text_limit=100)
        audit.record("answer", answer="x" * 200)
        audit.close()

        payload = json.loads(self.log_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["answer"], "x" * 100)

    def test_disabled_log_does_not_create_file(self) -> None:
        audit = AuditLog(self.log_path, enabled=False)
        audit.record("ignored", value="data")

        self.assertFalse(self.log_path.exists())

    def test_rotates_bounded_log_files(self) -> None:
        audit = AuditLog(
            self.log_path,
            max_bytes=1024,
            backup_count=1,
            text_limit=500,
        )
        for index in range(20):
            audit.record("event", index=index, text="x" * 300)
        audit.close()

        self.assertTrue(self.log_path.exists())
        self.assertTrue(self.log_path.with_name("audit.jsonl.1").exists())


if __name__ == "__main__":
    unittest.main()
