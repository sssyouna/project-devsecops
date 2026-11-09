# Phase 3 — Native Kubernetes security assessment

Status: complete for native Kubernetes controls. Advanced controls were not
implemented.

The authoritative validation run is
[20261109T221358Z-f1f1b7ae](20261109T221358Z-f1f1b7ae/). It created a fresh
`project5-lab` cluster, deployed the secured manifests, ran functional checks
before and after the security suite, and deleted the cluster in cleanup.
[All eight secured scenarios passed](20261109T221358Z-f1f1b7ae/security/results.json).

## Implemented controls

- Dedicated frontend, backend, and PostgreSQL ServiceAccounts with token
  automount disabled and no application RBAC permissions.
- Restricted Pod Security Admission labels in `project5` and `project5-data`.
- Default-deny ingress and egress policies with only frontend-to-backend,
  backend-to-PostgreSQL, and DNS exceptions.
- Non-root UIDs, disabled privilege escalation, all capabilities dropped,
  RuntimeDefault seccomp, read-only root filesystems, and explicit temporary
  filesystems.
- The backend no longer mounts the export/PVC. PostgreSQL data and synthetic
  export files are owned by UID 70 with restrictive permissions.
- A separate non-superuser `inventory_app` role has only the CRUD privileges
  needed by the API. Administrative TCP login is rejected by PostgreSQL HBA.

## V1 versus V2-native results

| Scenario | V1 insecure observation | V2-native demonstrated result |
| --- | --- | --- |
| T01 RBAC | Backend identity read Secrets, created Pods, patched Deployments, and used exec | All 14 authorization checks and four direct requests were denied; no backend token was mounted |
| T02 identity/lateral movement | Shared identity, mounted credentials, and frontend exec | Three dedicated tokenless identities; API permissions and frontend exec denied |
| T03 network | Unrestricted east-west and cross-namespace paths | Required paths worked; Calico blocked frontend-to-database, backend-to-frontend/API, foreign-label spoofing, and controlled lateral paths |
| T04 runtime | Web workloads ran as root with escalation/capabilities and writable roots | All workloads ran non-root with no effective/bounding capabilities, `NoNewPrivs`, RuntimeDefault seccomp, read-only roots, and writable `/tmp` only |
| T05 storage | Backend/export modification and PVC reuse were demonstrated | Backend had no export/PVC mount; non-owner reads/appends were denied and export bytes stayed unchanged |
| T06 admission | Dangerous dry-run Pod specifications were accepted | Restricted PSA rejected dangerous specifications in both namespaces; no test Pod persisted |
| T07 unauthenticated network access | Foreign namespace could perform CRUD through the backend path | Foreign CRUD connection timed out while the intended positive path still worked |
| T08 database privilege | Application database account was a superuser and could read files/create objects | CRUD remained available, while superuser attributes, file reads, DDL, temporary tables, sequence reads, and admin TCP login were denied |

These are measured lab results, not claims of complete tenant or host
isolation. The raw command transcript, environment, fixtures, integrity
checks, and cleanup record are retained in the run directory.

## Functional validation

The pre-security suite passed frontend serving, CRUD, validation, PostgreSQL
Pod replacement, backend replacement, and PVC persistence. The post-security
suite passed the same functional checks. Database rows, export bytes, and
source hashes remained unchanged. Runtime configuration changed as intended
for hardening; this is recorded as `runtime_hardening_changed` in
[integrity.json](20261109T221358Z-f1f1b7ae/security/integrity.json).

## Remaining risks and limitations

- CRUD is still unauthenticated inside the explicitly permitted application
  path; NetworkPolicy is not application authorization.
- Kubernetes Secrets remain plaintext synthetic fixtures and are not encryption
  at rest or a secret-management solution.
- There is no application/ database TLS, and DNS remains an allowed egress path.
- PostgreSQL owner/admin and authorized cluster or node administrators remain
  trusted. A compromised PostgreSQL process can access its own data.
- `ReadWriteOnce` limits volume attachment to nodes, not per-Pod authorization;
  authorized Pod creators in `project5-data` could still reuse the claim.
- PSA does not require `readOnlyRootFilesystem`; that control is explicitly
  configured and runtime-tested in the workloads.
- No host escape, node compromise, or production-scale availability guarantee
  was demonstrated. The setuid check uses an explicitly seeded positive
  control image and does not claim an application exploit.

## Reproduction and cleanup

From `devsecops-project5/`:

```sh
python3 scripts/validate_native.py
```

The validator preserves each attempt under `evidence/v2/native/`, stops its
port-forward, removes test fixtures, deletes only `project5-lab`, and records
remaining cluster containers. The authoritative cleanup record confirms
`deleted: true` and an empty container list.
