import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from integrity import runtime_integrity


class RuntimeIntegrityTests(unittest.TestCase):
    def test_unchanged_v2_snapshot_passes_regression(self):
        snapshot = {"network_policies": ["default-deny"], "psa": "restricted"}
        result = runtime_integrity(snapshot, dict(snapshot), "regression")
        self.assertTrue(result["runtime_baseline_unchanged"])
        self.assertFalse(result["runtime_hardening_changed"])
        self.assertTrue(result["runtime_expectation_met"])

    def test_changed_v2_snapshot_fails_regression(self):
        before = {"network_policies": ["default-deny"], "psa": "restricted"}
        after = {"network_policies": ["default-deny", "unexpected"], "psa": "restricted"}
        result = runtime_integrity(before, after, "regression")
        self.assertFalse(result["runtime_baseline_unchanged"])
        self.assertTrue(result["runtime_hardening_changed"])
        self.assertFalse(result["runtime_expectation_met"])

    def test_changed_snapshot_passes_original_migration_expectation(self):
        before = {"network_policies": ["default-deny"]}
        after = {"network_policies": ["default-deny"], "psa": "restricted"}
        result = runtime_integrity(before, after, "migration")
        self.assertFalse(result["runtime_baseline_unchanged"])
        self.assertTrue(result["runtime_hardening_changed"])
        self.assertTrue(result["runtime_expectation_met"])

    def test_unchanged_snapshot_fails_original_migration_expectation(self):
        snapshot = {"network_policies": ["default-deny"]}
        result = runtime_integrity(snapshot, dict(snapshot), "migration")
        self.assertFalse(result["runtime_expectation_met"])


if __name__ == "__main__":
    unittest.main()
