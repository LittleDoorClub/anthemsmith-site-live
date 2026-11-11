"""Pin the seed's analysis coverage check and the DDL contract, offline.

order_workflow.prepare() reads (object_path, source_sha256) rows and refuses the
order unless every ready upload's exact pair is present. A seed that matched on
path alone would write rows the gate still rejects, so the check is asserted
here rather than inferred from a green log. The table DDL is pinned the same
way: its UNIQUE constraint must be the writer's on_conflict target, or the
vision rows land as a duplicate-key error instead of a merge.
"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ensure_analysis_table as ensure  # noqa: E402  (import after the path insert)
import seed_test_order as seed  # noqa: E402

SHA_A = "a" * 64
SHA_B = "b" * 64
P1 = seed.OID + "/3711a22f-8e7d-414f-bd5f-3ab1a0387e2d.jpg"
P2 = seed.OID + "/5c460949-b240-4328-9d87-17e0822b6443.jpg"


class AnalysisCoverage(unittest.TestCase):
    def uploads(self):
        return [{"object_path": P1, "sha256": SHA_A}, {"object_path": P2, "sha256": SHA_B}]

    def test_complete_manifest_is_covered(self):
        rows = [{"object_path": P1, "source_sha256": SHA_A},
                {"object_path": P2, "source_sha256": SHA_B}]
        self.assertTrue(seed.analyses_cover(self.uploads(), rows))

    def test_missing_row_is_not_covered(self):
        self.assertFalse(seed.analyses_cover(self.uploads(),
                                             [{"object_path": P1, "source_sha256": SHA_A}]))

    def test_same_path_wrong_sha_is_not_covered(self):
        rows = [{"object_path": P1, "source_sha256": SHA_B},
                {"object_path": P2, "source_sha256": SHA_B}]
        self.assertFalse(seed.analyses_cover(self.uploads(), rows))

    def test_no_rows_is_not_coverage(self):
        self.assertFalse(seed.analyses_cover(self.uploads(), []))
        self.assertFalse(seed.analyses_cover([], []))


class TableContract(unittest.TestCase):
    def statements(self):
        return {label: statement for label, statement, _ in ensure.STATEMENTS}

    def test_conflict_target_is_the_unique_constraint(self):
        create = " ".join(self.statements()["create"].split())
        self.assertIn("UNIQUE (order_id, object_path, source_sha256, model)", create)
        self.assertIn("IF NOT EXISTS", create)

    def test_writer_and_gate_columns_exist(self):
        create = self.statements()["create"]
        for column in ("order_id", "object_path", "source_sha256", "model",
                       "provider_request_id", "analysis", "created_at"):
            self.assertIn(column, create)

    def test_every_statement_carries_a_boolean_verify(self):
        for label, _, verify in ensure.STATEMENTS:
            self.assertTrue(verify.lstrip().upper().startswith("SELECT"), label)

    def test_store_is_private(self):
        labels = [label for label, _, _ in ensure.STATEMENTS]
        self.assertIn("rls", labels)
        self.assertIn("revoke_public", labels)
        self.assertIn("grant_service_role", labels)


if __name__ == "__main__":
    unittest.main()
