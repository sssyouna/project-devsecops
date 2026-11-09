# Native security controls — Phase 3

`../base` includes this directory's NetworkPolicies. Both application namespaces
use Restricted Pod Security Admission pinned to v1.35. No advanced policy,
runtime monitoring or secret-management product is installed.

| Boundary | Native control |
| --- | --- |
| Workload → Kubernetes API | Dedicated frontend/backend/postgres ServiceAccounts; no application RoleBindings; token automount disabled on Pods and accounts |
| Network | Default-deny ingress/egress in both namespaces; namespace **and** Pod selectors on frontend→backend TCP 8000 and backend→database TCP 5432; DNS UDP/TCP 53 to CoreDNS only |
| Process | Non-root UID 101/10001/70, no privilege escalation, drop ALL capabilities, RuntimeDefault seccomp, read-only image filesystems, explicit temporary volumes |
| Admission | Restricted enforcement rejects privileged, host-access, root and other prohibited Pod fields; dangerous tests use server dry-run only |
| Storage | PVC exclusively in project5-data; no backend export mount; exports directory 0700/file 0600 owned by UID 70; database PGDATA 0700 |
| SQL | NOLOGIN owner, separate bootstrap admin, CRUD-only inventory_app, sequence USAGE only, no schema creation/TEMP; admin TCP logins rejected by pg_hba.conf |

NetworkPolicy is additive, not a substitute for application authentication or
RBAC. Namespace selectors prevent an identically labelled foreign Pod from
receiving the application exceptions. Administrators, node processes and
`kubectl port-forward`/exec are trusted operator paths; policies do not protect
against a cluster administrator or compromised node. DNS remains a permitted
outbound channel and is not domain filtered.

Restricted PSA does **not** enforce read-only root filesystems. That field is
set in these manifests and observed at runtime; the admission retest deliberately
records that a writable-root fixture remains accepted. PVC RWO restricts nodes,
not Pods. Authorized Pod creators in the data namespace can select the database
UID/fsGroup and reuse a claim; RBAC/namespace isolation, not RWO or PSA alone,
prevents application identities from doing this. Filesystem permissions do not
protect an export from its owner, PostgreSQL process compromise, or an admin.

Use `python3 scripts/validate_native.py` for separate secured expectations and
actual evidence. The script runs unchanged V1 functional assertions through a
namespace adapter, invokes the secured security suite, repeats functionality,
collects diagnostics, and deletes only project5-lab even after failure. V1 tests
and evidence are immutable. See `../../evidence/v2/native/` for results.
