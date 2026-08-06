from __future__ import annotations

import re
import sqlite3

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DECLARATION = re.compile(
    r"(?:TEXT|INTEGER)(?: NOT NULL)?(?: DEFAULT (?:'[A-Za-z0-9_]*'|[0-9]+))?\Z"
)


def ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    """Add a migration column using a deliberately narrow SQL grammar."""
    if not _IDENTIFIER.fullmatch(table) or not _IDENTIFIER.fullmatch(column):
        raise ValueError("invalid SQLite schema identifier")
    if not _DECLARATION.fullmatch(declaration):
        raise ValueError("unsupported SQLite column declaration")

    columns = {
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    if column not in columns:
        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')
