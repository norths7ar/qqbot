"""Initialize the current database format without upgrading existing databases."""

import sqlite3

SCHEMA_VERSION = 1


def initialize_schema(connection: sqlite3.Connection, schema: str) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    has_tables = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
    ).fetchone()
    if version != 0 or has_tables:
        raise ValueError("Unsupported database format; automatic migration is disabled")
    connection.executescript(
        f"BEGIN;\n{schema}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
    )
