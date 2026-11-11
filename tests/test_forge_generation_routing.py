"""The forge may never guess about a possible music POST, and a hold must be legible.

Two silent failures are pinned here. ``preflight_failed`` is the CAS-confirmed proof
that no submit happened, so it must reserve again -- routing it into a resume makes a
confirmed preflight exit dead-end forever. And a blocked record that says only
``forge_failed_or_audio_incomplete`` costs another paid run to diagnose, so the
worker's own code is lifted across a door that still refuses free text: its prose can
quote customer material, a code cannot.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import order_workflow as ow  # noqa: E402


class GenerationRouting(unittest.TestCase):
    def test_only_submitted_may_resume(self):
        for job in ({"status": "submitting"}, {"status": "reserved"},
                    {"status": "preflight_failed_extra"}, {"status": "done"}):
            self.assertEqual(ow.generation_action(job), "reconcile", job)

    def test_preflight_failure_is_retryable(self):
        self.assertEqual(ow.generation_action({"status": "preflight_failed"}), "reserve")

    def test_fresh_order_reserves(self):
        self.assertEqual(ow.generation_action(None), "reserve")
        self.assertEqual(ow.generation_action({}), "reserve")

    def test_submitted_polls_only(self):
        self.assertEqual(ow.generation_action({"status": "submitted"}), "resume")


class FailureCodeDoor(unittest.TestCase):
    def stage(self, reason):
        root = Path(self._tmp.name)
        (root / "songs").mkdir(parents=True, exist_ok=True)
        (root / "songs" / "AS-X.failed.json").write_text(
            '{"order_id": "AS-X", "status": "failed", "reason": %s}' % reason)
        return root

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_code_is_lifted(self):
        self.assertEqual(ow.child_failure_code(self.stage('"generation_reconciliation_required"'), "AS-X"),
                         "generation_reconciliation_required")
        self.assertEqual(ow.child_failure_code(self.stage('"photo_intake: source_files_missing"'), "AS-X"),
                         "photo_intake: source_files_missing")

    def test_prose_is_refused(self):
        prose = '"lyrics generation failed: the photo shows a woman holding a note."'
        self.assertEqual(ow.child_failure_code(self.stage(prose), "AS-X"), "")

    def test_absent_evidence_is_empty(self):
        self.assertEqual(ow.child_failure_code(Path(self._tmp.name), "AS-MISSING"), "")


if __name__ == "__main__":
    unittest.main()
