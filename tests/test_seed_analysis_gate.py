"""Pin the seed's analysis coverage check and the private-store DDL contract, offline.

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

import ensure_private_store as ensure  # noqa: E402  (import after the path insert)
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
    # The writer's on_conflict target per table. A CREATE whose UNIQUE differs from this
    # turns every vision/composition write into a duplicate-key failure, so it is pinned.
    CONFLICT = {
        "anthemsmith_recovery_analyses": "UNIQUE (order_id, object_path, source_sha256, model)",
        "anthemsmith_recovery_compositions": "UNIQUE (order_id, source_digest, model)",
    }
    COLUMNS = {
        "anthemsmith_recovery_analyses": ("order_id", "object_path", "source_sha256", "model",
                                          "provider_request_id", "analysis", "created_at"),
        "anthemsmith_recovery_compositions": ("order_id", "source_digest", "title", "lyrics",
                                              "provenance", "model", "created_at"),
    }

    def statements(self, table):
        return {label: statement for label, statement, _ in ensure.statements(table, ensure.TABLES[table][1])}

    def test_both_tables_are_created(self):
        self.assertEqual(set(ensure.TABLES), set(self.CONFLICT))

    def test_conflict_target_is_the_unique_constraint(self):
        for table, unique in self.CONFLICT.items():
            create = " ".join(self.statements(table)["create"].split())
            self.assertIn(unique, create, table)
            self.assertIn("IF NOT EXISTS", create)

    def test_writer_and_gate_columns_exist(self):
        for table, columns in self.COLUMNS.items():
            create = self.statements(table)["create"]
            for column in columns:
                self.assertIn(column, create, (table, column))

    def test_create_cannot_trip_the_recursion_gate(self):
        """The deploy organ refuses any body matching "<own name> (" as self-recursion."""
        for table in self.CONFLICT:
            create = self.statements(table)["create"]
            self.assertIn('public."%s"(' % table, create, table)
            self.assertNotIn(" %s (" % table, create, table)

    def test_every_statement_carries_a_boolean_verify(self):
        for table in self.CONFLICT:
            for label, _, verify in ensure.statements(table, ensure.TABLES[table][1]):
                self.assertTrue(verify.lstrip().upper().startswith("SELECT"), (table, label))

    def test_store_is_private(self):
        for table in self.CONFLICT:
            labels = [label for label, _, _ in ensure.statements(table, ensure.TABLES[table][1])]
            self.assertEqual(labels, ["create", "rls", "revoke_public", "grant_service_role"])


class ProbeStates(unittest.TestCase):
    """A 404 the schema cache raises is still a missing table, not an outage."""

    def raise_http(self, code, body):
        import io
        import urllib.error

        def fake(url, body_=None, timeout=60):
            raise urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))
        return fake

    def test_schema_cache_404_is_missing(self):
        real, ensure.req = ensure.req, self.raise_http(404, b'{"code":"PGRST205"}')
        try:
            self.assertEqual(ensure.probe("t", ("a",)), ("missing", "PGRST205"))
        finally:
            ensure.req = real

    def test_postgres_404_is_missing(self):
        real, ensure.req = ensure.req, self.raise_http(404, b'{"code":"42P01"}')
        try:
            self.assertEqual(ensure.probe("t", ("a",)), ("missing", "42P01"))
        finally:
            ensure.req = real

    def test_unreadable_table_is_private(self):
        real, ensure.req = ensure.req, self.raise_http(403, b'{"code":"42501"}')
        try:
            self.assertEqual(ensure.probe("t", ("a",)), ("private", "42501"))
        finally:
            ensure.req = real


if __name__ == "__main__":
    unittest.main()
