# Phase 1 handoff — 2026-11-09

This is the historical Phase 1 handoff. The subsequent
[completed Phase 2 security assessment](SECURITY-ASSESSMENT.md) records actual
security results and the requested cluster teardown. Statements below describe
the state at the end of Phase 1.

The Git release retains final validation logs, structured security evidence and
the screenshot. Local build, startup and failed browser diagnostics remain on
disk but are ignored; their findings are summarized below. They are not needed
to deploy or validate the frozen baseline. See [release audit](RELEASE-AUDIT.json).

Phase 1 is complete. The application was deployed in the disposable
`project5-lab` kind cluster, and functional validation succeeded. The final
browser screenshot was inspected. No Phase 2 security validation has run.
This directory contains **functional/setup evidence**, not completed security
baseline findings. Dates refer to America/New_York; Kubernetes log timestamps
may use UTC.

## Implemented and running

- Nginx serves a responsive inventory interface and proxies `/api/*` to FastAPI.
- FastAPI implements PostgreSQL-backed create, list, read, update, and delete,
  input validation, duplicate handling, and liveness/readiness endpoints.
- PostgreSQL retains synthetic inventory records in the `postgres-data` PVC.
- Three Deployments and ClusterIP Services share namespace `project5` and the
  intentionally overprivileged `shared-workload` ServiceAccount.
- One dedicated kind node runs Kubernetes v1.35.0 and Calico v3.33.0. Node,
  Calico, DNS, all three application Deployments, and the PVC became ready.
- Access uses a port-forward bound to **127.0.0.1:8080**. No public application
  endpoint or host-directory application mount was introduced.

The prior parent-repository deletions were preserved. Existing cluster
inspection was read-only; all deployment changes targeted `project5-lab` through
its dedicated, ignored kubeconfig. No commit or Git tag was created.

## Observed final results

| Check | Result | Evidence |
| --- | --- | --- |
| Python/JavaScript/Bash syntax; YAML/Kustomize rendering | PASS; 12 application resources | [static-checks.log](static-checks.log) |
| Backend/frontend container builds | PASS; final runtime image IDs retained | [resources.log](resources.log) |
| Three Deployments ready; PVC Bound, 512Mi, RWO | PASS | [resources.log](resources.log) |
| Calico node/controller and CoreDNS readiness | PASS | [network-components.log](network-components.log) |
| Backend live and database-ready endpoints | PASS | [health.log](health.log) |
| Frontend HTML/CSS/JS served | PASS | [functional.log](functional.log) |
| Frontend → backend → PostgreSQL CRUD | PASS | [functional.log](functional.log) |
| Duplicate SKU, invalid quantities, missing records | PASS | [functional.log](functional.log) |
| Exact item survives PostgreSQL Pod replacement | PASS; changed Pod UID, unchanged PVC/PV | [functional.log](functional.log) |
| Exact item survives backend Pod replacement | PASS | [functional.log](functional.log) |
| Real browser SKU validation, create/edit/delete, reload | PASS | [browser.log](browser.log) |
| Mobile layout and browser/application error checks | PASS | [browser.log](browser.log) |
| Actual rendered interface | Captured after deleting temporary browser record | [frontend.png](frontend.png) |

The unchanged PV observed across database Pod replacement was
`pvc-11e2ab95-77ac-40b7-9615-f47fe70dafdd`. This demonstrates retention across
the tested Pod replacements, not recovery from PVC, node, or cluster deletion.
`standard` uses `rancher.io/local-path`, `WaitForFirstConsumer`, and reclaim
policy `Delete`. Setup versions/storage are recorded in [platform.log](platform.log);
final runtime image IDs are recorded in [resources.log](resources.log).

Calico readiness is **not** evidence that a deny NetworkPolicy was tested.
No restrictive NetworkPolicy was applied in this phase.

## Resolved failures and limits

1. Initial sandboxed Docker access failed. Subsequent Docker and cluster work
   used the runner's approved execution path; host Docker permissions were not
   changed.
2. The pre-CNI kind readiness wait timed out. The final setup creates the control
   plane with `--wait 0s`, installs Calico, then explicitly waits for readiness.
   Slow registry transfers also exceeded the first 300-second CNI wait; the
   script now allows 900 seconds. The original local `cluster-create.log` and
   `cluster-network.log` are excluded diagnostic transcripts; the final ready
   state is retained in `network-components.log`.
3. Background process cleanup ended the first port-forward, causing the first
   functional attempt to fail before requests reached the application. A
   persistent command session fixed access. Replacing the frontend Pod later
   required reconnecting its port-forward. The local `functional-initial.log`
   and `browser-stale-forward.log` are excluded diagnostics.
4. An initial persistence run found a transient 502 during backend replacement.
   Added a ten-second preStop drain and five-second readiness stabilization;
   the complete rerun passed. The local `functional-before-drain.log` is excluded;
   the successful rerun is retained in `functional.log`.
   The single database replica still has expected restart downtime; this is not
   a high-availability evaluation.
5. The installed Playwright package lacked its JavaScript driver. Native
   headless Chromium and Firefox attempts also failed during startup/rendering.
   Selenium with Chromium under the already-installed Xvfb virtual display
   completed validation. No additional browser packages were installed.
   The failed attempts remain in `browser-system-driver-failure.log`,
   `browser-renderer-timeout.log`, `browser-chromium-crash.log`, and
   `browser-firefox-timeout.log`.
6. The working browser exposed an invalid HTML SKU pattern under current regex
   parsing rules. Escaped its hyphen, rebuilt/reloaded the frontend, and verified
   both rejection of spaces and successful valid CRUD with no browser errors.
   The local `browser-before-pattern-fix.log` is excluded; the final
   [browser.log](browser.log) is retained.

These failures are not security findings. Successful final results are only
those recorded in the final logs above.

## Commands executed

Read-only inspection included `git status --short`, `git log`, `git ls-tree`,
`git show HEAD:README.md`, tool/version discovery, Docker version/info/ps/images,
kind cluster discovery, and inspection of Kubernetes contexts, nodes,
namespaces, networking components, and storage classes. `rg` was unavailable;
inspection used the available filesystem tools instead.

Key deployment and validation commands are below, with paths normalized to the
project directory. Build and validation steps were repeated after the fixes
described above:

```sh
# Downloaded and SHA-256 checked the pinned Calico v3.33.0 manifest.
curl -fL https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/calico.yaml -o .lab/cache/calico-v3.33.0.yaml
sha256sum .lab/cache/calico-v3.33.0.yaml

# Node digest and kind configuration are also pinned in scripts/lab.sh.
kind create cluster --name project5-lab --image kindest/node@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f --config k8s/kind.yaml --kubeconfig .lab/kubeconfig --retain --wait 180s
kind get kubeconfig --name project5-lab
bash scripts/lab.sh cluster

# Pulled pinned image sources; resolved all Python package versions with uv.
uv pip compile app/backend/requirements.txt --python-version 3.13 --no-header --no-annotate --output-file app/backend/requirements.lock
docker build -t project5-backend:phase1 app/backend
docker build -t project5-frontend:phase1 app/frontend
docker tag postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94 project5-postgres:17.6
kind load docker-image --name project5-lab project5-backend:phase1 project5-frontend:phase1
kind load docker-image --name project5-lab project5-postgres:17.6
bash scripts/lab.sh deploy
bash scripts/lab.sh forward

python3 tests/functional.py --persistence
xvfb-run -a -s '-screen 0 1280x1100x24 -nolisten tcp' python3 tests/browser.py
bash scripts/lab.sh status

# Applied and validated the frontend fix; restarted its local forward afterward.
docker build -t project5-frontend:phase1 app/frontend
kind load docker-image --name project5-lab project5-frontend:phase1
bash scripts/lab.sh kubectl -n project5 rollout restart deployment/frontend
bash scripts/lab.sh kubectl -n project5 rollout status deployment/frontend --timeout=180s
```

The cluster's kubectl was copied to ignored `.lab/bin/` to match the server.
The guard checks kind identity, the project node label, and the loopback API
endpoint before operations. The final clean deployment entry point is
`bash scripts/lab.sh up`; the commands above document this session's incremental
implementation, including the earlier readiness wait that was corrected.
Logs were captured with `tee` and shell `pipefail` so failures were retained.

## Created files

All implementation files are new under `devsecops-project5/`:

```text
.gitignore
README.md
app/backend/{.dockerignore,Dockerfile,main.py,requirements.txt,requirements.lock}
app/frontend/{.dockerignore,Dockerfile,nginx.conf,index.html,styles.css,app.js}
k8s/kind.yaml
k8s/base/{kustomization.yaml,namespace.yaml,identity.yaml,config.yaml,postgres.yaml,backend.yaml,frontend.yaml}
k8s/security/README.md
scripts/lab.sh
tests/{functional.py,browser.py}
tests/security/README.md
evidence/v1/README.md
evidence/v1/*.log
evidence/v1/frontend.png
evidence/v2/README.md
```

Generated `.lab/` holds the kubeconfig, kubectl, and Calico download cache and
is Git-ignored. No real credentials or ServiceAccount token contents are saved
in evidence. Existing parent-repository files were not restored or overwritten.

## Intentional weaknesses and next phase

The baseline retains excessive namespace RBAC; shared ServiceAccount tokens;
unrestricted application networking; one shared application namespace; root web
containers and weak workload security settings; permissive Pod Security
Admission; and an unnecessary writable PVC export mount with 0777/0666
permissions on synthetic files. The project README maps each to a future
measurement. No actual privilege escalation, host compromise, unauthorized API
access, lateral movement, or data exposure has been demonstrated.

After approval, Phase 2 will formalize the threat matrix and implement/run
controlled reusable authorization, network, runtime-configuration, volume, and
admission tests. Their actual results must be captured before Phase 3 creates
`v1-insecure`. Native security, advanced tools, `v2-secured`, comparative results,
and blast-radius evaluation remain later work. **Stop here pending approval.**
