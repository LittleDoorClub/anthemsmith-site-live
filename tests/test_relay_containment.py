import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RelayContainment(unittest.TestCase):
    def run_gate(self, **values):
        env = {k: v for k, v in os.environ.items()
               if k not in {"AS_FORGE_OVERRIDE", "MUAPI_KEY", "PAID_STATUS",
                            "ORDER_ID", "AS_PAYMENT_GATE_TEST"}}
        env.update(ORDER_ID="AS-CONTAINMENT-TEST", AS_PAYMENT_GATE_TEST="1")
        env.update(values)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/forge_order.py")],
                                    env=env, cwd=directory, capture_output=True, text=True)
            self.assertEqual(list(Path(directory).iterdir()), [])
            return result

    def test_all_customer_payment_labels_rejected_before_any_side_effect(self):
        for label in ("", "awaiting_payment", "paid_claimed", "paid_stripe_return",
                      "paid_verified", "paid", "VERIFIED"):
            with self.subTest(label=label):
                result = self.run_gate(PAID_STATUS=label)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("public relay cannot verify", result.stdout)

    def test_bad_identifiers_rejected_even_with_operator_override(self):
        for oid in ("", "../../outside", "AS-../../outside", "AS-$(id)",
                    "AS-test\nnext", "AS-" + "a" * 81):
            with self.subTest(oid=oid):
                result = self.run_gate(ORDER_ID=oid, AS_FORGE_OVERRIDE="1")
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("invalid order ID", result.stdout)

    def test_explicit_operator_test_has_no_generation(self):
        result = self.run_gate(AS_FORGE_OVERRIDE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OPERATOR TEST ONLY", result.stdout)

    def test_workflow_has_no_active_legacy_jobs_or_payload_shell_interpolation(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/anthemsmith-order.yml").read_text())
        for job in workflow["jobs"].values():
            self.assertEqual(job["if"], "${{ false }}")
            for step in job["steps"]:
                self.assertNotIn("${{ github.event.client_payload", step.get("run", ""))
                self.assertNotIn("AS_FORGE_OVERRIDE", step.get("env", {}))


if __name__ == "__main__":
    unittest.main()
