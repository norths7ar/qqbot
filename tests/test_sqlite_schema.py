import sqlite3
import unittest

from qqbot.storage.sqlite import ensure_column


class EnsureColumnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("CREATE TABLE records (record_id INTEGER)")

    def tearDown(self) -> None:
        self.connection.close()

    def test_adds_allowed_column_declaration(self) -> None:
        ensure_column(
            self.connection,
            "records",
            "speaker_role",
            "TEXT NOT NULL DEFAULT 'human'",
        )

        columns = {
            row["name"]
            for row in self.connection.execute(
                'PRAGMA table_info("records")'
            ).fetchall()
        }
        self.assertIn("speaker_role", columns)

    def test_rejects_identifier_injection(self) -> None:
        with self.assertRaisesRegex(ValueError, "identifier"):
            ensure_column(
                self.connection,
                "records; DROP TABLE records",
                "extra",
                "TEXT",
            )

    def test_rejects_arbitrary_declaration(self) -> None:
        with self.assertRaisesRegex(ValueError, "declaration"):
            ensure_column(
                self.connection,
                "records",
                "extra",
                "TEXT; DROP TABLE records",
            )


if __name__ == "__main__":
    unittest.main()
