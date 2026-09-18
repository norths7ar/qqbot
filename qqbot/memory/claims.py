from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from qqbot.storage.sqlite import initialize_schema

CLAIM_SCOPES = frozenset({"person", "group"})
CLAIM_KINDS = frozenset({"profile", "preference", "relationship", "episode", "lore"})
CLAIM_STATUSES = frozenset(
    {"candidate", "active", "disputed", "superseded", "rejected"}
)
CLAIM_SOURCES = frozenset(
    {
        "admin_config",
        "self_statement",
        "third_party",
        "group_observation",
        "inferred",
    }
)
CLAIM_OPERATIONS = frozenset(
    {"insert", "confirm", "update", "dispute", "supersede", "ignore"}
)


@dataclass(frozen=True, slots=True)
class MemoryClaim:
    claim_id: int
    scope: str
    group_id: int | None
    subject_person_id: str | None
    predicate: str
    object_text: str
    asserted_by_person_id: str | None
    kind: str
    status: str
    source_type: str
    confidence: float
    importance: int
    valid_from: str | None
    valid_to: str | None
    observed_at: str
    created_at: str
    updated_at: str
    superseded_by_claim_id: int | None
    origin: str
    extraction_batch_id: int | None

    @property
    def content(self) -> str:
        if self.predicate == "admin_memory":
            return self.object_text
        return f"{self.predicate}：{self.object_text}"


@dataclass(frozen=True, slots=True)
class ClaimEvidence:
    group_message_id: int
    asserted_by_person_id: str | None
    evidence_type: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ExtractionBatch:
    batch_id: int
    group_id: int
    first_message_id: int
    last_message_id: int
    status: str
    operation_count: int
    applied_count: int
    rejection_reasons: tuple[str, ...]
    raw_response: str
    error: str | None
    created_at: str
    completed_at: str | None


class ClaimStore:
    """Authoritative long-term memory claim storage."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            initialize_schema(
                connection,
                """
                CREATE TABLE IF NOT EXISTS memory_claims (
                    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    group_id INTEGER,
                    subject_person_id TEXT,
                    predicate TEXT NOT NULL,
                    object_text TEXT NOT NULL,
                    asserted_by_person_id TEXT,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance INTEGER NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    superseded_by_claim_id INTEGER,
                    origin TEXT NOT NULL CHECK(origin IN ('admin', 'extracted')),
                    extraction_batch_id INTEGER,
                    CHECK(scope IN ('person', 'group')),
                    CHECK(status IN (
                        'candidate', 'active', 'disputed', 'superseded', 'rejected'
                    )),
                    CHECK(confidence >= 0 AND confidence <= 1),
                    CHECK(importance >= 1 AND importance <= 5)
                );

                CREATE INDEX IF NOT EXISTS idx_memory_claims_group_subject
                    ON memory_claims(group_id, subject_person_id, status, claim_id);
                CREATE INDEX IF NOT EXISTS idx_memory_claims_topic
                    ON memory_claims(group_id, predicate, status, claim_id);

                CREATE TABLE IF NOT EXISTS memory_claim_evidence (
                    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id INTEGER NOT NULL REFERENCES memory_claims(claim_id)
                        ON DELETE CASCADE,
                    group_message_id INTEGER NOT NULL,
                    asserted_by_person_id TEXT,
                    evidence_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    extraction_batch_id INTEGER,
                    UNIQUE(claim_id, group_message_id, evidence_type)
                );

                CREATE TABLE IF NOT EXISTS memory_extraction_batches (
                    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    first_message_id INTEGER NOT NULL,
                    last_message_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    operation_count INTEGER NOT NULL DEFAULT 0,
                    applied_count INTEGER NOT NULL DEFAULT 0,
                    rejection_reasons TEXT NOT NULL DEFAULT '[]',
                    raw_response TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(group_id, first_message_id, last_message_id)
                );
                """,
            )

    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def add_claim(
        self,
        *,
        scope: str,
        group_id: int | None,
        subject_person_id: str | None,
        predicate: str,
        object_text: str,
        asserted_by_person_id: str | None,
        kind: str,
        status: str,
        source_type: str,
        confidence: float,
        importance: int,
        valid_from: str | None = None,
        valid_to: str | None = None,
        observed_at: str | None = None,
        origin: str = "extracted",
        extraction_batch_id: int | None = None,
    ) -> MemoryClaim:
        self._validate_claim(
            scope=scope,
            subject_person_id=subject_person_id,
            predicate=predicate,
            object_text=object_text,
            kind=kind,
            status=status,
            source_type=source_type,
        )
        timestamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memory_claims(
                    scope, group_id, subject_person_id, predicate, object_text,
                    asserted_by_person_id, kind, status, source_type, confidence,
                    importance, valid_from, valid_to, observed_at, created_at,
                    updated_at, origin, extraction_batch_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    group_id,
                    subject_person_id,
                    _normalize_text(predicate, limit=80),
                    _normalize_text(object_text, limit=500),
                    asserted_by_person_id,
                    kind,
                    status,
                    source_type,
                    _bounded_confidence(confidence),
                    _bounded_importance(importance),
                    valid_from,
                    valid_to,
                    observed_at or timestamp,
                    timestamp,
                    timestamp,
                    origin,
                    extraction_batch_id,
                ),
            )
            claim_id = cursor.lastrowid
            if claim_id is None:
                raise RuntimeError("failed to insert memory claim")
            row = self._claim_row(connection, int(claim_id))
        return self._claim_from_row(row)

    def get_claim(self, claim_id: int) -> MemoryClaim | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_claims WHERE claim_id = ?", (claim_id,)
            ).fetchone()
        return self._claim_from_row(row) if row else None

    def list_claims(
        self,
        *,
        status: str | None = None,
        group_id: int | None = None,
        limit: int = 100,
    ) -> list[MemoryClaim]:
        clauses: list[str] = []
        parameters: list[object] = []
        if status is not None:
            if status not in CLAIM_STATUSES:
                raise ValueError(f"unsupported claim status: {status}")
            clauses.append("status = ?")
            parameters.append(status)
        if group_id is not None:
            clauses.append("group_id = ?")
            parameters.append(group_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(limit, 1000)))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memory_claims {where} "  # noqa: S608
                "ORDER BY claim_id DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._claim_from_row(row) for row in rows]

    def evidence_for_claim(self, claim_id: int) -> list[ClaimEvidence]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT group_message_id, asserted_by_person_id, evidence_type,
                       created_at
                FROM memory_claim_evidence
                WHERE claim_id = ?
                ORDER BY evidence_id
                """,
                (claim_id,),
            ).fetchall()
        return [
            ClaimEvidence(
                group_message_id=int(row["group_message_id"]),
                asserted_by_person_id=(
                    str(row["asserted_by_person_id"])
                    if row["asserted_by_person_id"]
                    else None
                ),
                evidence_type=str(row["evidence_type"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def add_evidence(
        self,
        claim_id: int,
        group_message_id: int,
        *,
        asserted_by_person_id: str | None,
        evidence_type: str,
        extraction_batch_id: int | None = None,
    ) -> bool:
        with self._connect() as connection:
            if self._claim_row(connection, claim_id) is None:
                return False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO memory_claim_evidence(
                    claim_id, group_message_id, asserted_by_person_id,
                    evidence_type, created_at, extraction_batch_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    group_message_id,
                    asserted_by_person_id,
                    _normalize_text(evidence_type, limit=40),
                    _now(),
                    extraction_batch_id,
                ),
            )
        return cursor.rowcount > 0

    def delete_claim(self, claim_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memory_claims WHERE claim_id = ?",
                (claim_id,),
            )
        return cursor.rowcount > 0

    def list_serving_claims(
        self,
        *,
        subject_person_id: str | None = None,
        group_id: int | None = None,
        include_global: bool = True,
        limit: int = 100,
        now: datetime | None = None,
    ) -> list[MemoryClaim]:
        current = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        clauses = [
            "status IN ('active', 'candidate', 'disputed')",
            "(valid_to IS NULL OR valid_to > ?)",
        ]
        parameters: list[object] = [current]
        if subject_person_id is not None:
            clauses.append("subject_person_id = ?")
            parameters.append(subject_person_id)
        if group_id is not None:
            if include_global:
                clauses.append("(group_id = ? OR group_id IS NULL)")
            else:
                clauses.append("group_id = ?")
            parameters.append(group_id)
        parameters.append(max(1, min(limit, 1000)))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memory_claims WHERE {' AND '.join(clauses)} "
                "ORDER BY claim_id LIMIT ?",  # noqa: S608
                parameters,
            ).fetchall()
        return [self._claim_from_row(row) for row in rows]

    def update_claim_status(
        self,
        claim_id: int,
        status: str,
        *,
        superseded_by_claim_id: int | None = None,
    ) -> bool:
        if status not in CLAIM_STATUSES:
            raise ValueError(f"unsupported claim status: {status}")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_claims
                SET status = ?, superseded_by_claim_id = ?, updated_at = ?
                WHERE claim_id = ?
                """,
                (status, superseded_by_claim_id, _now(), claim_id),
            )
        return cursor.rowcount > 0

    def update_claim_strength(
        self,
        claim_id: int,
        *,
        confidence: float,
        importance: int,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_claims
                SET confidence = MAX(confidence, ?),
                    importance = MAX(importance, ?), updated_at = ?
                WHERE claim_id = ?
                """,
                (
                    _bounded_confidence(confidence),
                    _bounded_importance(importance),
                    _now(),
                    claim_id,
                ),
            )
        return cursor.rowcount > 0

    def replace_claim(self, old_claim_id: int, new_claim_id: int) -> bool:
        if old_claim_id == new_claim_id:
            return False
        with self._connect() as connection:
            old = self._claim_row(connection, old_claim_id)
            new = self._claim_row(connection, new_claim_id)
            if (
                old is None
                or new is None
                or old["group_id"] != new["group_id"]
                or old["scope"] != new["scope"]
                or old["subject_person_id"] != new["subject_person_id"]
            ):
                return False
            cursor = connection.execute(
                """
                UPDATE memory_claims
                SET status = 'superseded', superseded_by_claim_id = ?,
                    updated_at = ?
                WHERE claim_id = ?
                  AND status IN ('active', 'candidate', 'disputed')
                """,
                (new_claim_id, _now(), old_claim_id),
            )
        return cursor.rowcount > 0

    def snapshot_claim(self, claim_id: int) -> dict[str, object] | None:
        with self._connect() as connection:
            row = self._claim_row(connection, claim_id)
        if row is None:
            return None
        return {
            "claim_id": int(row["claim_id"]),
            "status": str(row["status"]),
            "confidence": float(row["confidence"]),
            "importance": int(row["importance"]),
            "updated_at": str(row["updated_at"]),
            "superseded_by_claim_id": (
                int(row["superseded_by_claim_id"])
                if row["superseded_by_claim_id"] is not None
                else None
            ),
        }

    def related_claims(
        self,
        group_id: int,
        *,
        person_ids: Sequence[str],
        query_text: str,
        limit: int = 30,
    ) -> list[MemoryClaim]:
        normalized_people = tuple(dict.fromkeys(person_ids))
        current = _now()
        with self._connect() as connection:
            if normalized_people:
                placeholders = ",".join("?" for _ in normalized_people)
                rows = connection.execute(
                    f"""
                    SELECT * FROM memory_claims
                    WHERE (group_id = ? OR group_id IS NULL)
                      AND status IN ('active', 'candidate', 'disputed')
                      AND (valid_to IS NULL OR valid_to > ?)
                      AND (subject_person_id IN ({placeholders}) OR scope = 'group')
                    ORDER BY updated_at DESC
                    LIMIT 200
                    """,  # noqa: S608
                    (group_id, current, *normalized_people),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM memory_claims
                    WHERE (group_id = ? OR group_id IS NULL)
                      AND status IN ('active', 'candidate', 'disputed')
                      AND (valid_to IS NULL OR valid_to > ?)
                    ORDER BY updated_at DESC
                    LIMIT 200
                    """,
                    (group_id, current),
                ).fetchall()
        terms = _terms(query_text)
        ranked: list[tuple[int, MemoryClaim]] = []
        for row in rows:
            claim = self._claim_from_row(row)
            person_boost = 10 if claim.subject_person_id in normalized_people else 0
            score = person_boost + _text_score(
                f"{claim.predicate} {claim.object_text}", terms
            )
            if score > 0 or person_boost:
                ranked.append((score, claim))
        ranked.sort(key=lambda item: (item[0], item[1].claim_id), reverse=True)
        return [claim for _, claim in ranked[: max(1, min(limit, 100))]]

    def start_extraction_batch(
        self, group_id: int, first_message_id: int, last_message_id: int
    ) -> int:
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_extraction_batches(
                    group_id, first_message_id, last_message_id, status, created_at
                ) VALUES (?, ?, ?, 'running', ?)
                ON CONFLICT(group_id, first_message_id, last_message_id)
                DO UPDATE SET status = 'running', operation_count = 0,
                    applied_count = 0, rejection_reasons = '[]',
                    raw_response = '', error = NULL,
                    created_at = excluded.created_at, completed_at = NULL
                """,
                (group_id, first_message_id, last_message_id, timestamp),
            )
            row = connection.execute(
                """
                SELECT batch_id FROM memory_extraction_batches
                WHERE group_id = ? AND first_message_id = ? AND last_message_id = ?
                """,
                (group_id, first_message_id, last_message_id),
            ).fetchone()
            if row is not None:
                connection.execute(
                    "DELETE FROM memory_claims WHERE extraction_batch_id = ?",
                    (int(row["batch_id"]),),
                )
        if row is None:
            raise RuntimeError("failed to start extraction batch")
        return int(row["batch_id"])

    def finish_extraction_batch(
        self,
        batch_id: int,
        *,
        status: str,
        operation_count: int,
        applied_count: int,
        raw_response: str,
        error: str | None = None,
        rejection_reasons: Sequence[str] = (),
        restore_snapshots: Sequence[Mapping[str, object]] = (),
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("extraction batch status must be completed or failed")
        with self._connect() as connection:
            if status == "failed":
                connection.execute(
                    "DELETE FROM memory_claim_evidence WHERE extraction_batch_id = ?",
                    (batch_id,),
                )
                connection.execute(
                    "DELETE FROM memory_claims WHERE extraction_batch_id = ?",
                    (batch_id,),
                )
                for snapshot in restore_snapshots:
                    try:
                        claim_id = int(snapshot["claim_id"])
                        claim_status = str(snapshot["status"])
                        confidence = float(snapshot["confidence"])
                        importance = int(snapshot["importance"])
                        updated_at = str(snapshot["updated_at"])
                        replacement = snapshot.get("superseded_by_claim_id")
                        superseded_by = (
                            int(replacement) if replacement is not None else None
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                    connection.execute(
                        """
                        UPDATE memory_claims
                        SET status = ?, confidence = ?, importance = ?,
                            updated_at = ?, superseded_by_claim_id = ?
                        WHERE claim_id = ?
                        """,
                        (
                            claim_status,
                            confidence,
                            importance,
                            updated_at,
                            superseded_by,
                            claim_id,
                        ),
                    )
            connection.execute(
                """
                UPDATE memory_extraction_batches
                SET status = ?, operation_count = ?, applied_count = ?,
                    rejection_reasons = ?, raw_response = ?, error = ?,
                    completed_at = ?
                WHERE batch_id = ?
                """,
                (
                    status,
                    max(0, operation_count),
                    max(0, applied_count),
                    json.dumps(
                        [str(reason)[:500] for reason in rejection_reasons[:100]],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    raw_response[:20000],
                    error[:4000] if error else None,
                    _now(),
                    batch_id,
                ),
            )

    def list_extraction_batches(self, *, limit: int = 20) -> list[ExtractionBatch]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_extraction_batches
                ORDER BY batch_id DESC LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [self._batch_from_row(row) for row in rows]

    def conflicts(self, *, limit: int = 100) -> list[MemoryClaim]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT c.*
                FROM memory_claims c
                LEFT JOIN memory_claims other
                  ON other.claim_id != c.claim_id
                 AND other.group_id IS c.group_id
                 AND other.subject_person_id IS c.subject_person_id
                 AND other.predicate = c.predicate
                 AND other.status IN ('active', 'candidate', 'disputed')
                WHERE c.status = 'disputed'
                   OR (c.status IN ('active', 'candidate')
                       AND other.claim_id IS NOT NULL
                       AND other.object_text != c.object_text)
                ORDER BY c.claim_id DESC LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [self._claim_from_row(row) for row in rows]

    @staticmethod
    def _validate_claim(
        *,
        scope: str,
        subject_person_id: str | None,
        predicate: str,
        object_text: str,
        kind: str,
        status: str,
        source_type: str,
    ) -> None:
        if scope not in CLAIM_SCOPES:
            raise ValueError(f"unsupported claim scope: {scope}")
        if scope == "person" and not subject_person_id:
            raise ValueError("person claim requires subject_person_id")
        if scope == "group" and subject_person_id is not None:
            raise ValueError("group claim cannot have subject_person_id")
        if not _normalize_text(predicate, limit=80):
            raise ValueError("claim predicate cannot be empty")
        if not _normalize_text(object_text, limit=500):
            raise ValueError("claim object cannot be empty")
        if kind not in CLAIM_KINDS:
            raise ValueError(f"unsupported claim kind: {kind}")
        if status not in CLAIM_STATUSES:
            raise ValueError(f"unsupported claim status: {status}")
        if source_type not in CLAIM_SOURCES:
            raise ValueError(f"unsupported claim source: {source_type}")

    @staticmethod
    def _claim_row(connection: sqlite3.Connection, claim_id: int) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM memory_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> MemoryClaim:
        return MemoryClaim(
            claim_id=int(row["claim_id"]),
            scope=str(row["scope"]),
            group_id=int(row["group_id"]) if row["group_id"] is not None else None,
            subject_person_id=(
                str(row["subject_person_id"]) if row["subject_person_id"] else None
            ),
            predicate=str(row["predicate"]),
            object_text=str(row["object_text"]),
            asserted_by_person_id=(
                str(row["asserted_by_person_id"])
                if row["asserted_by_person_id"]
                else None
            ),
            kind=str(row["kind"]),
            status=str(row["status"]),
            source_type=str(row["source_type"]),
            confidence=float(row["confidence"]),
            importance=int(row["importance"]),
            valid_from=str(row["valid_from"]) if row["valid_from"] else None,
            valid_to=str(row["valid_to"]) if row["valid_to"] else None,
            observed_at=str(row["observed_at"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            superseded_by_claim_id=(
                int(row["superseded_by_claim_id"])
                if row["superseded_by_claim_id"] is not None
                else None
            ),
            origin=str(row["origin"]),
            extraction_batch_id=(
                int(row["extraction_batch_id"])
                if row["extraction_batch_id"] is not None
                else None
            ),
        )

    @staticmethod
    def _batch_from_row(row: sqlite3.Row) -> ExtractionBatch:
        return ExtractionBatch(
            batch_id=int(row["batch_id"]),
            group_id=int(row["group_id"]),
            first_message_id=int(row["first_message_id"]),
            last_message_id=int(row["last_message_id"]),
            status=str(row["status"]),
            operation_count=int(row["operation_count"]),
            applied_count=int(row["applied_count"]),
            rejection_reasons=_parse_rejection_reasons(row["rejection_reasons"]),
            raw_response=str(row["raw_response"]),
            error=str(row["error"]) if row["error"] else None,
            created_at=str(row["created_at"]),
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def parse_operations(text: str) -> list[Mapping[str, object]]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```")
        stripped = stripped.removesuffix("```").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("memory extractor did not return JSON")
    payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, Mapping):
        raise ValueError("memory extractor returned a non-object")
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise ValueError("memory extractor operations must be an array")
    return [
        _normalize_operation_fields(item)
        for item in operations
        if isinstance(item, Mapping)
    ]


_OPERATION_FIELDS = (
    "operation",
    "scope",
    "subject_person_id",
    "predicate",
    "object_text",
    "kind",
    "confidence",
    "importance",
    "source_message_ids",
    "valid_from",
    "valid_to",
    "target_claim_id",
)
_COMPACT_OPERATION_FIELDS = {
    "".join(character for character in field if character.isalnum()): field
    for field in _OPERATION_FIELDS
}


def _normalize_operation_fields(
    operation: Mapping[str, object],
) -> Mapping[str, object]:
    """Accept common JSON key casing while keeping the internal contract canonical."""
    normalized = dict(operation)
    canonical_present = {key for key in operation if key in _OPERATION_FIELDS}
    for key, value in operation.items():
        compact = "".join(
            character for character in str(key).casefold() if character.isalnum()
        )
        canonical = _COMPACT_OPERATION_FIELDS.get(compact)
        if canonical is not None and canonical not in canonical_present:
            normalized[canonical] = value
    return normalized


def _parse_rejection_reasons(value: object) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(reason) for reason in parsed)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normalize_text(value: object, *, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _bounded_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _bounded_importance(value: object) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def _terms(text: str) -> tuple[str, frozenset[str]]:
    normalized = "".join(text.casefold().split())
    trigrams = frozenset(
        normalized[index : index + 3] for index in range(max(0, len(normalized) - 2))
    )
    return normalized, trigrams


def _text_score(content: str, terms: tuple[str, frozenset[str]]) -> int:
    normalized = "".join(content.casefold().split())
    query, trigrams = terms
    if not query or not normalized:
        return 0
    if query == normalized:
        return 100
    if query in normalized or normalized in query:
        return 80
    return min(60, 3 * sum(term in normalized for term in trigrams))
