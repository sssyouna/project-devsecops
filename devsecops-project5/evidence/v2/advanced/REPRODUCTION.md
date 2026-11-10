# Phase 4 evidence reproduction

The retained evidence was produced in disposable clusters that were deleted
after validation. No cluster is running for this report. The final architecture
contains only the native controls and Kyverno; Cilium, Linkerd, Falco, and
Tetragon are not deployed.

## Kyverno

1. In a separately approved validation run, create the native lab with
   `bash scripts/lab.sh up`.
2. Apply the pinned release manifest:

   ```sh
   bash scripts/lab.sh kubectl apply --server-side --force-conflicts \
     -f .lab/cache/kyverno-v1.19.2-install.yaml
   ```

3. Wait for all deployments in namespace `kyverno` to become ready.
4. Apply [kyverno-policies.yaml](../../../k8s/advanced/kyverno-policies.yaml).
5. Submit:
   - a PSA-compliant Pod using an approved image and complete resources;
   - a PSA-compliant Pod using `nginx:latest`;
   - a PSA-compliant Pod using an approved image without a memory or CPU limit;
   - a PSA-compliant Pod labeled
     `security.project5.io/image-policy: digest` using a tag.
6. Use server-side dry-run for denying cases and inspect `PolicyReport` objects
   after the audit case has had time for a background scan.
7. Poll the namespaced `PolicyReport` until the Pod-scoped result for
   `project5-immutable-protected-images` /
   `require-digests-on-protected-workloads` has `result: fail`.
8. Delete the test Pod only after saving that result. The deterministic
   implementation is [kyverno_digest_audit.py](../../../tests/kyverno_digest_audit.py).

The retained admission results are under [kyverno/](./kyverno/). The digest
policy is intentionally `Audit`; acceptance is not prevention. The targeted
validation evidence under
[`digest-audit-validation/`](./digest-audit-validation/) confirms admission,
the matching Pod-scoped PolicyReport failure, and deletion only afterward.

The retained Cilium and Linkerd directories contain historical diagnostics
only. They must not be interpreted as successful deployments or as proof of
incompatibility with the VM. No runtime detection evidence exists for Falco or
Tetragon.

## Cleanup

Always use a cleanup guard:

```sh
trap 'kind delete cluster --name project5-lab || true' EXIT INT TERM
```

Then verify:

```sh
kind get clusters
docker ps -a --filter label=io.x-k8s.kind.cluster=project5-lab
```
