"""Verify the opt-in Kyverno digest Audit result before deleting the test Pod."""
import argparse
import json
from pathlib import Path
import subprocess
import time


POLICY = "project5-immutable-protected-images"
RULE = "require-digests-on-protected-workloads"


def kubectl(args, *, input_text=None, check=True):
    return subprocess.run(
        ["kubectl", *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def find_result(report, pod_name):
    scope = report.get("scope", {})
    if scope.get("kind") != "Pod" or scope.get("name") != pod_name:
        return None
    for result in report.get("results", []):
        if result.get("policy") == POLICY and result.get("rule") == RULE:
            return result
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = Path(args.manifest)
    pod_name = "kyverno-digest-audit-deterministic"
    evidence = {"pod": pod_name, "policy": POLICY, "rule": RULE, "admission": None, "reports": []}
    try:
        manifest_text = manifest.read_text().replace("kyverno-digest-audit", pod_name)
        admitted = kubectl(["apply", "--server-side", "-f", "-"], input_text=manifest_text)
        evidence["admission"] = {"exit_code": admitted.returncode, "stdout": admitted.stdout}
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            reports = json.loads(kubectl(["-n", "project5", "get", "policyreports", "-o", "json"]).stdout)
            evidence["reports"] = reports.get("items", [])
            for report in evidence["reports"]:
                result = find_result(report, pod_name)
                if result is not None:
                    evidence["matched_report"] = {
                        "name": report["metadata"]["name"],
                        "namespace": report["metadata"].get("namespace"),
                        "scope": report["scope"],
                        "result": result,
                    }
                    if result.get("result") != "fail":
                        raise AssertionError("digest Audit result was not fail")
                    output.joinpath("result.json").write_text(json.dumps(evidence, indent=2) + "\n")
                    return 0
            time.sleep(2)
        raise TimeoutError("No matching digest PolicyReport result before timeout")
    finally:
        deleted = kubectl(["-n", "project5", "delete", "pod", pod_name, "--ignore-not-found"], check=False)
        evidence["deletion"] = {"exit_code": deleted.returncode, "stdout": deleted.stdout}
        output.joinpath("result.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
