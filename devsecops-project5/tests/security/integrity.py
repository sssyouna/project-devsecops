"""Integrity expectations for native security validation modes."""


def runtime_integrity(before, after, mode):
    """Evaluate the runtime snapshot for a migration or regression run."""
    if mode not in {"migration", "regression"}:
        raise ValueError("mode must be 'migration' or 'regression'")
    unchanged = before == after
    changed = not unchanged
    return {
        "runtime_baseline_unchanged": unchanged,
        "runtime_hardening_changed": changed,
        "runtime_expectation_met": changed if mode == "migration" else unchanged,
    }
