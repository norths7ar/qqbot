from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from qqbot.memory.claims import ClaimStore, MemoryClaim
from qqbot.runtime.paths import PROJECT_ROOT
from qqbot.storage.group_data import GroupDataStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and review qqbot memory")
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "data" / "bot_memory.db",
        help="memory SQLite path (default: project data/bot_memory.db)",
    )
    parser.add_argument(
        "--group-database",
        type=Path,
        default=PROJECT_ROOT / "data" / "group_tools.db",
        help="group message SQLite path (default: project data/group_tools.db)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    recent = commands.add_parser("recent", help="list recent claims")
    recent.add_argument("--limit", type=int, default=20)
    recent.add_argument("--group", type=int)

    candidates = commands.add_parser("candidates", help="list candidate claims")
    candidates.add_argument("--limit", type=int, default=50)
    candidates.add_argument("--group", type=int)

    show = commands.add_parser("show", help="show one claim and its evidence")
    show.add_argument("claim_id", type=int)

    for name, help_text in (
        ("confirm", "mark a candidate claim active"),
        ("reject", "reject a claim"),
        ("dispute", "mark a claim disputed"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("claim_id", type=int)

    conflicts = commands.add_parser("conflicts", help="list possible conflicts")
    conflicts.add_argument("--limit", type=int, default=50)

    batches = commands.add_parser("batches", help="list extraction batches")
    batches.add_argument("--limit", type=int, default=20)

    commands.add_parser("status", help="show memory schema and record counts")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    store = ClaimStore(arguments.database)
    store.initialize()

    if arguments.command == "status":
        claims = store.list_claims(limit=1000)
        candidates = sum(claim.status == "candidate" for claim in claims)
        serving = len(store.list_serving_claims(limit=1000))
        print(f"schema_version={store.schema_version()}")
        print(f"claims={len(claims)} serving={serving} candidates={candidates}")
        print(f"extraction_batches={len(store.list_extraction_batches(limit=200))}")
        return 0
    if arguments.command == "recent":
        _print_claims(
            store.list_claims(group_id=arguments.group, limit=arguments.limit)
        )
        return 0
    if arguments.command == "candidates":
        _print_claims(
            store.list_claims(
                status="candidate",
                group_id=arguments.group,
                limit=arguments.limit,
            )
        )
        return 0
    if arguments.command == "conflicts":
        _print_claims(store.conflicts(limit=arguments.limit))
        return 0
    if arguments.command == "batches":
        for batch in store.list_extraction_batches(limit=arguments.limit):
            error = f" error={batch.error}" if batch.error else ""
            rejected = (
                f" rejected={'; '.join(batch.rejection_reasons)}"
                if batch.rejection_reasons
                else ""
            )
            print(
                f"#{batch.batch_id} group={batch.group_id} "
                f"messages={batch.first_message_id}-{batch.last_message_id} "
                f"status={batch.status} operations={batch.operation_count} "
                f"applied={batch.applied_count}{rejected}{error}"
            )
        return 0
    if arguments.command == "show":
        claim = store.get_claim(arguments.claim_id)
        if claim is None:
            print("claim not found", file=sys.stderr)
            return 1
        _print_claims([claim])
        evidence = store.evidence_for_claim(claim.claim_id)
        if evidence:
            print("evidence:")
            group_store = GroupDataStore(arguments.group_database)
            messages = group_store.messages_by_ids(
                [item.group_message_id for item in evidence]
            )
            for item in evidence:
                message = messages.get(item.group_message_id)
                content = message.content if message else "[message unavailable]"
                print(
                    f"  message={item.group_message_id} "
                    f"assertor={item.asserted_by_person_id or '-'} "
                    f"type={item.evidence_type} content={content}"
                )
        else:
            print("evidence: none")
        return 0
    if arguments.command in {"confirm", "reject", "dispute"}:
        target_status = {
            "confirm": "active",
            "reject": "rejected",
            "dispute": "disputed",
        }[arguments.command]
        if not store.update_claim_status(arguments.claim_id, target_status):
            print("claim not found", file=sys.stderr)
            return 1
        print(f"claim {arguments.claim_id} -> {target_status}")
        return 0
    return 2


def _print_claims(claims: Sequence[MemoryClaim]) -> None:
    if not claims:
        print("no claims")
        return
    for claim in claims:
        subject = claim.subject_person_id or f"group:{claim.group_id}"
        assertor = claim.asserted_by_person_id or "-"
        validity = f" valid_to={claim.valid_to}" if claim.valid_to else ""
        print(
            f"#{claim.claim_id} [{claim.status}/{claim.kind}/{claim.origin}] "
            f"subject={subject} assertor={assertor} "
            f"confidence={claim.confidence:.2f} importance={claim.importance} "
            f"{claim.content}{validity}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
