# Baseline security measurements

`baseline.py` orchestrates eight reproducible measurements against **only** the
named `project5-lab` kind cluster. `probe.py` runs inside existing containers
through stdin. No scanners, packages, agents, or application changes are needed.

From `devsecops-project5/`, with the baseline already running:

```sh
python3 tests/security/baseline.py --expect-baseline
```

Every invocation creates a new `evidence/v1/phase2-<UTC timestamp>-<run ID>/`;
an existing output directory is never overwritten. `--output` can select a new
directory under `evidence/`. Evidence includes:

- `results.json`: structured observations and optional insecure-baseline checks.
- `commands.jsonl`: exact commands, actor/transport roles, exit codes, stdout,
  stderr, and hashes/references for stdin source or fixture definitions.
- `fixtures.json`: exact synthetic fixtures, including dry-run-only Pod specs.
- `environment.json`: cluster, CNI, storage, source hashes and initial state.
- `cleanup.json` and `integrity.json`: fixture removal, application definitions,
  Pod identities, source hashes and database-row digests before/after.

**MATCH means the expected insecure behavior was reproduced, not that the
application is secure.** Without `--expect-baseline`, the runner records
observations without requiring vulnerable outcomes. Namespace, data namespace,
ServiceAccount and PVC arguments allow reuse when responsibilities are separated
in later phases. Absent paths/tokens and changed conditions must be interpreted
separately from an authorization denial; an inconclusive transport failure is
not a security success.

| ID | Objective |
| --- | --- |
| T01 | RBAC authorization plus real, TLS-verified mounted-token API reads and dry-run deployment patch |
| T02 | Shared workload identity, token presence, and an actual impersonated `pods/exec` request into the frontend |
| T03 | Required/prohibited communications and positive→deny→positive CNI enforcement control |
| T04 | Actual UID, capabilities, NoNewPrivs/seccomp, writable root filesystems, raw socket creation, and conditional setuid experiment |
| T05 | UID 65532 reads/modifies/restores a synthetic export; workload token creates a restricted Pod reusing the PVC |
| T06 | Server-side dry-run of individual/combined dangerous settings, safe control, invalid type and contradictory-setting controls; no dangerous Pod is started |
| T07 | A tokenless, non-root Pod in a separate disposable namespace reads/creates/updates/deletes only its synthetic API canary |
| T08 | Legitimate database authority and PostgreSQL-superuser access to its own Pod's projected-token file (only the public subject is returned) |

The operator creates a uniquely labelled disposable test namespace, two
synthetic Secrets, and a restricted HTTP target Pod. A second restricted Pod is
created using the backend's **actual token**, mounting only the lab PVC. Its
admission settings are safe; it never reads database data files. The CNI deny
policy selects **only the disposable target**, never application Pods. It is
removed after the control measurement. These are test fixtures, not architecture
changes or application hardening. No external network destinations are probed.

The setuid test has an explicit precondition: an already-root operator inside
the backend seeds a temporary root-owned 4755 copy of Python and a root-only
synthetic canary, then launches UID 65532 children. The ordinary child is the
negative control. Only this seeded helper can demonstrate the conditional
transition; it does not establish that an existing exploitable helper was
present, that an initially unprivileged attacker can seed it, or that the host
was compromised. Both files are removed in `finally`.

Export writes are limited to the exact known synthetic payroll fixture and are
restored immediately. Concurrent unexpected changes cause a refusal to overwrite
unknown content. File timestamps may reflect the controlled write/restore. The
API canary consumes an inventory sequence value even after deletion; existing
rows must have the same digest afterward. A temporary SQL table is rolled back.

No token strings, kubeconfigs, real passwords, host credentials, host files,
or raw PostgreSQL data files are copied into evidence. Synthetic Secret hashes
and public identity claims are recorded instead. Namespace deletion and Pod
cleanup require matching the unique run label and original UID. Do not delete
resources by broad application selectors if an interrupted run needs cleanup.

This suite does not exploit the app, launch privileged/host-namespace Pods,
mount host directories, test destructive deletion of the PVC, or install native
or advanced protections. Approval for later phases remains separate.

The [completed Phase 2 assessment](../../evidence/v1/SECURITY-ASSESSMENT.md) links
the authoritative successful run and the initial harness failures. The final
frontend token requests supply authorization headers through stdin so a curl
option diagnostic cannot echo the token-bearing argument. The raw-token output
guard remains enabled.
