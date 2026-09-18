import sqlite3
import unittest

from qqbot.storage.sqlite import SCHEMA_VERSION, initialize_schema


class DatabaseSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)

    def test_new_database_initializes_once_and_preserves_records(self) -> None:
        schema = "CREATE TABLE records (record_id INTEGER PRIMARY KEY);"
        initialize_schema(self.connection, schema)
        with self.connection:
            self.connection.execute("INSERT INTO records VALUES (7)")
        initialize_schema(self.connection, schema)

        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(
            self.connection.execute("SELECT * FROM records").fetchall(), [(7,)]
        )

    def test_existing_unrecognized_database_is_rejected_without_changes(self) -> None:
        with self.connection:
            self.connection.execute("CREATE TABLE records (record_id INTEGER)")
            self.connection.execute("INSERT INTO records VALUES (7)")
        before = list(self.connection.iterdump())

        with self.assertRaisesRegex(ValueError, "Unsupported database format"):
            initialize_schema(self.connection, "CREATE TABLE extra (value TEXT);")

        self.assertEqual(list(self.connection.iterdump()), before)
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0], 0
        )

    def test_unknown_version_is_rejected(self) -> None:
        self.connection.execute("PRAGMA user_version = 999")

        with self.assertRaisesRegex(ValueError, "Unsupported database format"):
            initialize_schema(self.connection, "CREATE TABLE records (value TEXT);")

        self.assertEqual(
            self.connection.execute("SELECT name FROM sqlite_master").fetchall(), []
        )


if __name__ == "__main__":
    unittest.main()
