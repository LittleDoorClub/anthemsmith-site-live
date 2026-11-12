"""Offline tests for scripts/order_lifecycle.py -- the lifecycle projection.

No network, no credentials, no live row. The projection is a pure function, so
these assert the DONE-TEST the lane owner set:

  1. one call answers "did the lane run and did it deliver?", in the words the
     source files themselves use;
  2. a missing manifest projects `unknown` -- never `source_received`, never a
     promoted intake value;
  3. sources that disagree give `contradiction`, not a smoothed story;
  4. the module has no write path at all (structural, not a string match).
"""
import importlib.util
import json
import os
import re
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HAND = os.path.join(os.path.dirname(HERE), "scripts", "order_lifecycle.py")

_spec = importlib.util.spec_from_file_location("order_lifecycle", HAND)
lifecycle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lifecycle)

OID = "AS-TEST-MAYA-20261110"
ROW = {"order_id": OID, "status": "source_received", "payment_state": "verified_paid",
       "delivery_result": {"mode": "email", "ok": True}}
MANIFEST = {"order_id": OID, "status": "audio_ready", "duration_full": 209.221, "private_audio": True}
DELIVERED = {"order_id": OID, "delivery_status": "delivered", "provider_status": "delivered"}
QUEUED = {"order_id": OID, "delivery_status": "submitted", "provider_status": "queued"}
FAILED = {"order_id": OID, "delivery_status": "failed", "provider_status": "failed"}


class TestProjection(unittest.TestCase):
    def test_agreeing_sources_answer_in_the_sources_own_words(self):
        p = lifecycle.project(OID, ROW, MANIFEST, DELIVERED)
        self.assertEqual(p["lifecycle"], "audio_ready")
        self.assertEqual(p["did_the_lane_run"], "yes")
        self.assertEqual(p["did_it_deliver"], "yes")
        for token in ("audio_ready", "delivered", "209.221"):
            self.assertIn(token, p["answer"])

    def test_missing_manifest_is_unknown_not_the_intake_gate(self):
        p = lifecycle.project(OID, ROW, None, None)
        self.assertEqual(p["lifecycle"], "unknown")
        self.assertEqual(p["did_the_lane_run"], "unknown")
        self.assertNotEqual(p["lifecycle"], ROW["status"])
        self.assertIn("manifest_missing", p["degraded"])
        # the intake value may be *reported* under intake.*, but never as the verdict
        self.assertEqual(p["intake"]["status"], "source_received")
        self.assertNotIn("source_received", p["answer"])
        self.assertIn("unknown", p["answer"])

    def test_manifest_without_delivery_record_says_unknown_delivery(self):
        p = lifecycle.project(OID, ROW, MANIFEST, None)
        self.assertEqual(p["lifecycle"], "audio_ready")
        self.assertEqual(p["did_it_deliver"], "unknown")
        self.assertIn("delivery_missing", p["degraded"])
        self.assertIn("no delivery record", p["answer"])

    def test_in_flight_delivery_is_not_called_delivered(self):
        p = lifecycle.project(OID, ROW, MANIFEST, QUEUED)
        self.assertEqual(p["lifecycle"], "audio_ready")
        self.assertEqual(p["did_it_deliver"], "yes")
        self.assertIn("submitted", p["answer"])
        self.assertNotIn("delivery delivered", p["answer"])

    def test_failed_delivery_against_audio_ready_is_a_contradiction(self):
        p = lifecycle.project(OID, ROW, MANIFEST, FAILED)
        self.assertEqual(p["lifecycle"], "contradiction")
        self.assertEqual(p["did_it_deliver"], "no -- source says failed")
        self.assertIn("sources disagree", p["answer"])
        self.assertIn("failed", p["answer"])

    def test_unreadable_row_degrades_without_inventing_intake(self):
        p = lifecycle.project(OID, None, MANIFEST, DELIVERED, notes=("row_unreadable:http_42501",))
        self.assertIsNone(p["intake"]["status"])
        self.assertEqual(p["lifecycle"], "audio_ready")
        self.assertIn("row_unreadable:http_42501", p["degraded"])
        self.assertIn("intake_missing", p["degraded"])

    def test_delivery_result_is_passed_through_untouched(self):
        p = lifecycle.project(OID, ROW, MANIFEST, DELIVERED)
        self.assertEqual(p["delivery_result"], ROW["delivery_result"])


class TestCommandLine(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.songs = os.path.join(self.dir, "songs")
        os.makedirs(self.songs)

    def _write(self, name, payload):
        with open(os.path.join(self.songs, name), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_cli_prints_one_json_line_and_exits_zero(self):
        self._write(OID + ".json", MANIFEST)
        self._write(OID + ".delivery.json", DELIVERED)
        rowfile = os.path.join(self.dir, "row.json")
        self._write("row.json", ROW)
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = lifecycle.main(["--order-id", OID, "--songs-dir", self.songs, "--row-file", rowfile])
        self.assertEqual(rc, 0)
        line = buf.getvalue().strip()
        self.assertTrue(line.startswith("ORDER_LIFECYCLE "))
        self.assertEqual(json.loads(line.split(" ", 1)[1])["lifecycle"], "audio_ready")

    def test_cli_missing_manifest_still_exits_zero_and_says_unknown(self):
        rowfile = os.path.join(self.dir, "row.json")
        self._write("row.json", ROW)
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = lifecycle.main(["--order-id", OID, "--songs-dir", self.songs, "--row-file", rowfile])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue().strip().split(" ", 1)[1])["lifecycle"], "unknown")

    def test_cli_contradiction_exits_nonzero(self):
        self._write(OID + ".json", MANIFEST)
        self._write(OID + ".delivery.json", FAILED)
        rowfile = os.path.join(self.dir, "row.json")
        self._write("row.json", ROW)
        import io
        from contextlib import redirect_stdout

        with redirect_stdout(io.StringIO()):
            self.assertEqual(lifecycle.main(
                ["--order-id", OID, "--songs-dir", self.songs, "--row-file", rowfile]), 1)

    def test_cli_refuses_a_path_shaped_order_id(self):
        import io
        from contextlib import redirect_stderr

        buf = io.StringIO()
        with redirect_stderr(buf):
            self.assertEqual(lifecycle.main(["--order-id", "../etc/passwd", "--songs-dir", self.songs]), 2)
        self.assertIn("bad_order_id", buf.getvalue())


class TestNoWritePath(unittest.TestCase):
    def test_module_cannot_mutate_the_row(self):
        with open(HAND, encoding="utf-8") as fh:
            src = fh.read()
        for verb in ("PATCH", "POST", "DELETE", "PUT"):
            self.assertIsNone(re.search(r'"%s"' % verb, src), "module gained a %s call" % verb)
        self.assertNotIn("def rest(", src)


if __name__ == "__main__":
    unittest.main()
