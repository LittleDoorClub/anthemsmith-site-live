"""Offline guards for the one sanctioned order-status repair hand.

No network, no credentials, no live row: rest() and gh() are replaced with
recorders, and the repo manifest is a temp file. Asserts BEHAVIOUR — that a
refused write performs zero mutations — not just error strings.
"""
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "set_order_status", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "set_order_status.py")
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)

OID = "AS-TEST-MAYA-20261110"
RUN_ID = "43073545082"
SHA = "b873c4c2c8f54edde7bc61d692ebab619b239b3b"
GOOD_RUN = {"id": int(RUN_ID), "name": "anthemsmith-order", "event": "workflow_dispatch",
            "status": "completed", "conclusion": "success", "head_sha": SHA,
            "created_at": "2026-11-11T18:00:14Z"}


class Recorder:
    """Stands in for rest(): a row store plus a log of every mutation."""

    def __init__(self, row, patch_status=200, patch_rows=None):
        self.row = dict(row)
        self.patch_status = patch_status
        self.patch_rows = patch_rows
        self.calls = []

    def __call__(self, method, params=None, body=None, prefer=None):
        self.calls.append({"method": method, "body": body})
        if method == "GET":
            return 200, [dict(self.row)]
        if method == "PATCH":
            rows = self.patch_rows if self.patch_rows is not None else [{**self.row, **body}]
            if self.patch_status in (200, 206) and rows:
                self.row.update(rows[0])
            return self.patch_status, rows
        raise AssertionError("unexpected method " + method)

    @property
    def writes(self):
        return [c for c in self.calls if c["method"] == "PATCH"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        (pathlib.Path(self.tmp.name) / "songs").mkdir()
        self.env = patch.dict(os.environ, {"GH_REPO": "LittleDoorClub/anthemsmith-site-live"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def manifest(self, status="audio_ready"):
        path = pathlib.Path(self.tmp.name) / "songs" / (OID + ".json")
        path.write_text(json.dumps({"order_id": OID, "status": status,
                                    "private_audio": True, "duration_full": 209.221}))

    def run_main(self, rest, gh=None, mode="write", status="audio_ready",
                 run_id=RUN_ID, main_sha=SHA, oid=OID):
        fake_gh = gh or (lambda path: dict(GOOD_RUN))
        with patch.object(s, "rest", rest), patch.object(s, "gh", fake_gh), \
                patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "test-key"}, clear=False):
            return s.main(["--order-id", oid, "--status", status, "--run-id", run_id,
                           "--main-sha", main_sha, "--mode", mode])


class ArgumentGuardTests(Base):
    def test_bad_ids_fail_before_any_call(self):
        rest = Recorder({"order_id": OID, "status": "source_received"})
        for kwargs, reason in (
            ({"oid": "AS TEST"}, "bad_order_id"),
            ({"status": "delivered_x"}, "target_not_allowed"),
            ({"main_sha": "abc"}, "bad_main_sha"),
            ({"run_id": "12"}, "bad_run_id"),
        ):
            with self.assertRaises(s.Hold) as caught:
                self.run_main(rest, **kwargs)
            self.assertIn(reason, str(caught.exception))
        self.assertEqual(rest.calls, [])


class EvidenceGuardTests(Base):
    def test_repo_manifest_disagreeing_blocks_write(self):
        self.manifest(status="source_received")
        rest = Recorder({"order_id": OID, "status": "source_received"})
        with self.assertRaises(s.Hold) as caught:
            self.run_main(rest)
        self.assertIn("repo_manifest_not_audio_ready", str(caught.exception))
        self.assertEqual(rest.writes, [])

    def test_missing_manifest_blocks_write(self):
        rest = Recorder({"order_id": OID, "status": "source_received"})
        with self.assertRaises(s.Hold) as caught:
            self.run_main(rest)
        self.assertIn("manifest_missing", str(caught.exception))
        self.assertEqual(rest.writes, [])

    def test_run_must_be_green(self):
        self.manifest()
        rest = Recorder({"order_id": OID, "status": "source_received"})
        red = lambda path: {**GOOD_RUN, "conclusion": "failure"}
        with self.assertRaises(s.Hold) as caught:
            self.run_main(rest, gh=red)
        self.assertIn("run_not_green", str(caught.exception))
        self.assertEqual(rest.writes, [])

    def test_run_head_sha_must_match(self):
        self.manifest()
        rest = Recorder({"order_id": OID, "status": "source_received"})
        other = lambda path: {**GOOD_RUN, "head_sha": "c" * 40}
        with self.assertRaises(s.Hold) as caught:
            self.run_main(rest, gh=other)
        self.assertIn("run_head_sha_mismatch", str(caught.exception))
        self.assertEqual(rest.writes, [])


class WriteTests(Base):
    def test_one_write_then_readback(self):
        self.manifest()
        rest = Recorder({"order_id": OID, "status": "source_received"})
        self.assertEqual(self.run_main(rest), 0)
        self.assertEqual(len(rest.writes), 1)
        self.assertEqual(rest.writes[0]["body"], {"status": "audio_ready"})
        self.assertEqual(rest.row["status"], "audio_ready")
        evidence = json.loads(pathlib.Path(OID + ".status-evidence.json").read_text())
        self.assertEqual(evidence["verdict"], "WRITTEN")
        self.assertEqual(evidence["writes_performed"], 1)
        self.assertTrue(evidence["provenance_columns_absent"])

    def test_already_correct_performs_no_write(self):
        self.manifest()
        rest = Recorder({"order_id": OID, "status": "audio_ready"})
        self.assertEqual(self.run_main(rest), 0)
        self.assertEqual(rest.writes, [])
        evidence = json.loads(pathlib.Path(OID + ".status-evidence.json").read_text())
        self.assertEqual(evidence["verdict"], "ALREADY_CORRECT")

    def test_illegal_transition_refused(self):
        self.manifest()
        rest = Recorder({"order_id": OID, "status": "awaiting_sources"})
        with self.assertRaises(s.Hold) as caught:
            self.run_main(rest)
        self.assertIn("transition_not_allowed", str(caught.exception))
        self.assertEqual(rest.writes, [])

    def test_inspect_never_writes(self):
        self.manifest()
        rest = Recorder({"order_id": OID, "status": "source_received"})
        self.assertEqual(self.run_main(rest, mode="inspect"), 0)
        self.assertEqual(rest.writes, [])

    def test_patch_returning_no_row_is_a_hold(self):
        self.manifest()
        rest = Recorder({"order_id": OID, "status": "source_received"}, patch_rows=[])
        with self.assertRaises(s.Hold) as caught:
            self.run_main(rest)
        self.assertIn("patch_row_count_0", str(caught.exception))


class ErrorShapeTests(Base):
    def test_postgrest_details_never_reach_the_log(self):
        body = json.dumps({
            "code": "23514",
            "details": "Failing row contains (AS-TEST-MAYA-20261110, 1221ec54..., 2026-11-10 22:33:03)",
            "hint": None,
            "message": 'new row for relation "anthemsmith_recovery_orders" violates check constraint "anthemsmith_recovery_orders_status_check"',
        }).encode()
        error = urllib.error.HTTPError("http://x", 400, "Bad Request", {}, io.BytesIO(body))
        with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "test-key"}, clear=False), \
                patch.object(s.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(s.Hold) as caught:
                s.rest("PATCH", {"order_id": "eq." + OID}, {"status": "audio_ready"})
        message = str(caught.exception)
        self.assertIn("23514", message)
        self.assertIn("status_check", message)
        self.assertNotIn("1221ec54", message)
        self.assertNotIn("Failing row contains", message)


if __name__ == "__main__":
    unittest.main()
