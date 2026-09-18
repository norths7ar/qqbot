import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qqbot.memory.admin import main
from qqbot.memory.claims import ClaimStore


class MemoryAdminTests(unittest.TestCase):
    def test_status_excludes_rejected_and_expired_claims_from_serving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            store = ClaimStore(database)
            store.initialize()
            for status, valid_to in (
                ("active", None),
                ("rejected", None),
                (
                    "candidate",
                    (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                ),
            ):
                store.add_claim(
                    scope="person",
                    group_id=1,
                    subject_person_id="alice",
                    predicate="test",
                    object_text=status,
                    asserted_by_person_id="alice",
                    kind="profile",
                    status=status,
                    source_type="self_statement",
                    confidence=1,
                    importance=1,
                    valid_to=valid_to,
                    origin="extracted",
                )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(["--database", str(database), "status"])

        self.assertEqual(exit_code, 0)
        self.assertIn("claims=3 serving=1 candidates=1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
