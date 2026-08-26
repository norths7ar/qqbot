"""Read-only, isolated reports for the V2 shadow-memory batches."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

from qqbot.memory_v2 import ClaimStore, parse_operations


@dataclass(frozen=True, slots=True)
class ShadowReplayReport:
    source_database: str
    output_database: str
    schema_version: int
    batch_count: int
    completed_batch_count: int
    failed_batch_count: int
    parsed_batch_count: int
    parse_error_count: int
    operation_count: int
    operation_types: dict[str, int]
    rejection_count: int
    duplicate_rejection_count: int
    conflict_claim_count: int
    claim_count: int
    claims_by_status: dict[str, int]
    evidence_count: int
    assertors: dict[str, int]
    ground_truth_available: bool = False
    notes: tuple[str, ...] = (
        "This is an auditable parse and storage summary, not precision/recall.",
        "No V1 serving path or production database was changed.",
    )


def copy_sqlite_readonly(source: Path, target: Path) -> None:
    """Backup a source opened with SQLite's mode=ro URI into a new target."""
    source_resolved = source.resolve()
    target_resolved = target.resolve()
    if source_resolved == target_resolved:
        raise ValueError("source and output database must be different paths")
    if not source_resolved.exists():
        raise FileNotFoundError(source_resolved)
    if target_resolved.exists():
        raise FileExistsError(
            f"refusing to overwrite existing replay output: {target_resolved}"
        )
    target_resolved.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{quote(source_resolved.as_posix(), safe='/:')}?mode=ro"
    source_connection = sqlite3.connect(source_uri, uri=True)
    try:
        target_connection = sqlite3.connect(target_resolved)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
    finally:
        source_connection.close()


def build_shadow_replay_report(source: Path, output: Path) -> ShadowReplayReport:
    """Copy source read-only, initialize only the copy, and summarize its batches."""
    copy_sqlite_readonly(source, output)
    store = ClaimStore(output)
    store.initialize()
    batches = store.list_shadow_batches(limit=200)
    operation_types: Counter[str] = Counter()
    parsed_batch_count = 0
    parse_error_count = 0
    operation_count = 0
    rejection_count = 0
    duplicate_rejection_count = 0
    for batch in batches:
        rejection_count += len(batch.rejection_reasons)
        duplicate_rejection_count += sum(
            "duplicate" in reason.casefold() for reason in batch.rejection_reasons
        )
        if not batch.raw_response.strip():
            continue
        try:
            operations = parse_operations(batch.raw_response)
        except (TypeError, ValueError, json.JSONDecodeError):
            parse_error_count += 1
            continue
        parsed_batch_count += 1
        operation_count += len(operations)
        operation_types.update(str(item.get("operation", "")) for item in operations)

    claims = store.list_claims(limit=1000)
    claims_by_status = Counter(claim.status for claim in claims)
    assertors: Counter[str] = Counter()
    evidence_count = 0
    for claim in claims:
        if claim.asserted_by_person_id:
            assertors[claim.asserted_by_person_id] += 1
        evidence = store.evidence_for_claim(claim.claim_id)
        evidence_count += len(evidence)
        for item in evidence:
            if item.asserted_by_person_id:
                assertors[item.asserted_by_person_id] += 1

    return ShadowReplayReport(
        source_database=str(source.resolve()),
        output_database=str(output.resolve()),
        schema_version=store.schema_version(),
        batch_count=len(batches),
        completed_batch_count=sum(batch.status == "completed" for batch in batches),
        failed_batch_count=sum(batch.status == "failed" for batch in batches),
        parsed_batch_count=parsed_batch_count,
        parse_error_count=parse_error_count,
        operation_count=operation_count,
        operation_types=dict(sorted(operation_types.items())),
        rejection_count=rejection_count,
        duplicate_rejection_count=duplicate_rejection_count,
        conflict_claim_count=len(store.conflicts(limit=1000)),
        claim_count=len(claims),
        claims_by_status=dict(sorted(claims_by_status.items())),
        evidence_count=evidence_count,
        assertors=dict(sorted(assertors.items())),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy a V2 shadow DB read-only and produce an auditable report"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    report = build_shadow_replay_report(arguments.source, arguments.output)
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
