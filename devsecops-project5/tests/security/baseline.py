"""Reproducible, disposable-lab-only security measurements; no security fixes.

Run: python3 tests/security/baseline.py --expect-baseline
Each run writes a new evidence directory. No kubeconfig or token is recorded.
Operator fixture setup/observation and workload-token actions are distinguished.
"""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name("probe.py")
JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def sha(value):
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def source_hashes():
    return {str(path.relative_to(ROOT)): sha(path.read_bytes()) for directory in ("app", "k8s", "scripts")
            for path in (ROOT / directory).rglob("*") if path.is_file() and "__pycache__" not in path.parts}


class Lab:
    def __init__(self, args):
        self.args = args
        self.run_id = uuid.uuid4().hex[:10]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.out = Path(args.output).resolve() if args.output else ROOT / "evidence/v1" / f"phase2-{stamp}-{self.run_id}"
        if not self.out.is_relative_to(ROOT / "evidence"):
            raise ValueError("Evidence must be saved under this project's evidence directory")
        self.out.mkdir(parents=True, exist_ok=False)
        self.log = self.out / "commands.jsonl"
        self.prefix = [str(ROOT / ".lab/bin/kubectl"), "--kubeconfig", str(ROOT / ".lab/kubeconfig"), "--context", "kind-project5-lab"]
        self.ns = args.namespace
        self.data_ns = args.data_namespace or self.ns
        if not all(name.startswith("project5") for name in [self.ns, self.data_ns]):
            raise ValueError("Only project5 lab namespaces are supported")
        self.test_ns = "project5-check-" + self.run_id
        self.canary = "phase2-canary-" + self.run_id
        self.volume_pod = "phase2-volume-" + self.run_id
        self.identity = f"system:serviceaccount:{self.ns}:{args.service_account}"
        self.groups = ["--as=" + self.identity, "--as-group=system:authenticated", "--as-group=system:serviceaccounts",
                       "--as-group=system:serviceaccounts:" + self.ns]
        self.created = []
        self.fixtures = []
        self.results = {"run_id": self.run_id, "started_utc": datetime.now(timezone.utc).isoformat(),
                        "cluster": "project5-lab", "namespace": self.ns, "data_namespace": self.data_ns,
                        "workload_identity": self.identity, "cases": {}, "errors": []}

    def save(self, name, value):
        text = json.dumps(value, indent=2, sort_keys=True) + "\n"
        if JWT.search(text):
            raise RuntimeError("Refused to record token-shaped output")
        (self.out / name).write_text(text)

    def command(self, arguments, *, stdin=None, source=None, role="operator observation", check=True, timeout=150):
        started = datetime.now(timezone.utc).isoformat()
        try:
            process = subprocess.run(arguments, cwd=ROOT, input=stdin, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self.results["errors"].append({"command_timeout": arguments, "seconds": timeout})
            raise RuntimeError(f"Command exceeded {timeout}s: {arguments[:3]}") from exc
        record = {"time_utc": started, "role": role, "argv": arguments, "exit_code": process.returncode,
                  "stdout": process.stdout, "stderr": process.stderr}
        if stdin is not None:
            record["stdin"] = {"source": source, "sha256": sha(stdin.encode()), "bytes": len(stdin.encode())}
        encoded = json.dumps(record, sort_keys=True)
        if JWT.search(encoded):
            raise RuntimeError("Refused to record token-shaped output")
        with self.log.open("a") as stream:
            stream.write(encoded + "\n")
        if check and process.returncode:
            raise RuntimeError(f"Command exited {process.returncode}: {arguments[:4]}: {process.stderr[-400:]}")
        return process

    def k(self, *args, **kwargs):
        return self.command(self.prefix + list(args), **kwargs)

    def obj(self, *args):
        return json.loads(self.k(*args, "-o", "json").stdout)

    def probe(self, action, parameters=None, *, pod=None, namespace=None, impersonate=False):
        parameters = dict(parameters or {}, run_id=self.run_id)
        params = json.dumps(parameters, sort_keys=True)
        args = (self.groups if impersonate else []) + ["-n", namespace or self.ns, "exec", "-i", pod or "deployment/backend", "--", "python", "-", action, params]
        result = self.k(*args, stdin=PROBE.read_text(), source="tests/security/probe.py",
                        role="impersonated workload exec" if impersonate else "operator exec transport; probe uses workload context", check=False)
        try:
            observation = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"transport_exit_code": result.returncode, "transport_error": result.stderr[-1000:]}
        return observation

    def api(self, path, method="GET", body=None, **kwargs):
        parameters = {"path": path, "method": method, **kwargs}
        if body is not None:
            parameters["body"] = body
        return self.probe("api", parameters)

    def create(self, obj):
        self.fixtures.append(copy.deepcopy(obj))
        created = json.loads(self.k("create", "-f", "-", "-o", "json", stdin=json.dumps(obj), source="fixtures.json",
                                    role="operator fixture setup").stdout)
        self.created.append((obj["kind"].lower(), obj["metadata"].get("namespace"), obj["metadata"]["name"], created["metadata"]["uid"]))
        return created

    def metadata(self, name, namespace=None):
        return {"name": name, **({"namespace": namespace} if namespace else {}),
                "labels": {"phase2-run": self.run_id}}

    def safe_pod(self, name, namespace):
        return {"apiVersion": "v1", "kind": "Pod", "metadata": self.metadata(name, namespace), "spec": {
            "automountServiceAccountToken": False, "terminationGracePeriodSeconds": 1, "restartPolicy": "Never",
            "securityContext": {"runAsUser": 65532, "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{"name": "probe", "image": self.image, "imagePullPolicy": "Never",
                            "command": ["python", "-c", "import time; time.sleep(3600)"],
                            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                                "capabilities": {"drop": ["ALL"]}},
                            "resources": {"requests": {"cpu": "10m", "memory": "24Mi"}, "limits": {"cpu": "250m", "memory": "96Mi"}}}]}}

    def snapshot(self):
        # Deliberately record Secret hashes only; no secret values or tokens.
        state = {}
        for name in ("backend", "frontend", "postgres"):
            namespace = self.data_ns if name == "postgres" else self.ns
            obj = self.obj("-n", namespace, "get", "deployment", name)
            state["deployment/" + name] = {"uid": obj["metadata"]["uid"], "spec_sha256": sha(obj["spec"])}
            obj = self.obj("-n", namespace, "get", "service", name)
            state["service/" + name] = sha(obj["spec"])
        for kind, name in [("role", "excessive-workload-access"), ("rolebinding", "shared-workload-excessive-access"),
                           ("serviceaccount", self.args.service_account), ("namespace", self.ns)]:
            raw = self.k("-n", self.ns, "get", kind, name, "-o", "json", "--ignore-not-found=true").stdout
            if not raw.strip():
                state[kind + "/" + name] = {"absent": True}
                continue
            obj = json.loads(raw)
            metadata = obj["metadata"]
            for field in ("status", "metadata"):
                obj.pop(field, None)
            state[kind + "/" + name] = {"object_sha256": sha(obj), "uid": metadata["uid"], "labels": metadata.get("labels", {})}
        # Secret data is measured by a workload-token request in T01 and is
        # never emitted by the operator's resource snapshots.
        obj = self.obj("-n", self.data_ns, "get", "pvc", self.args.pvc)
        state["pvc"] = {"uid": obj["metadata"]["uid"], "spec_sha256": sha(obj["spec"]), "volume": obj["spec"].get("volumeName")}
        pods = [obj for namespace in sorted({self.ns, self.data_ns}) for obj in self.obj("-n", namespace, "get", "pods")["items"]]
        state["pods"] = [{"name": obj["metadata"]["name"], "uid": obj["metadata"]["uid"],
                          "image_ids": [container.get("imageID") for container in obj["status"].get("containerStatuses", [])]}
                         for obj in sorted(pods, key=lambda p: p["metadata"]["name"]) if obj["metadata"].get("labels", {}).get("app") in ("backend", "frontend", "postgres")]
        state["application_network_policies"] = self.obj("-n", self.ns, "get", "networkpolicies")["items"]
        return state

    def setup(self):
        self.command(["bash", "scripts/lab.sh", "kubectl", "get", "node", "project5-lab-control-plane", "-o", "json"], role="operator cluster guard")
        self.inputs = source_hashes()
        obj = self.obj("-n", self.ns, "get", "deployment", "backend")
        self.image = obj["spec"]["template"]["spec"]["containers"][0]["image"]
        if not self.image.startswith("project5-backend:"):
            raise RuntimeError("Unexpected backend image; refusing to test an unidentified workload")
        self.before = self.snapshot()
        self.db_before = self.probe("database")
        role = self.k("-n", self.ns, "get", "role", "excessive-workload-access", "-o", "json", "--ignore-not-found=true").stdout
        environment = {"cluster_version": json.loads(self.k("version", "-o", "json").stdout), "baseline": self.before,
                       "database": self.db_before, "source_sha256": self.inputs, "probe_sha256": sha(PROBE.read_bytes()),
                       "runner_sha256": sha(Path(__file__).read_bytes()),
                       "namespace": self.obj("get", "namespace", self.ns),
                       "role": json.loads(role) if role.strip() else None,
                       "cni": self.obj("-n", "kube-system", "get", "daemonset", "calico-node"),
                       "storage_class": self.obj("get", "storageclass", "standard")}
        self.save("environment.json", environment)
        self.create({"apiVersion": "v1", "kind": "Namespace", "metadata": self.metadata(self.test_ns)})
        for namespace in [self.ns, self.test_ns]:
            self.create({"apiVersion": "v1", "kind": "Secret", "metadata": self.metadata(self.canary, namespace),
                         "type": "Opaque", "stringData": {"canary": "SYNTHETIC-ONLY-" + self.run_id}})
        pod = self.safe_pod("target", self.test_ns)
        pod["metadata"]["labels"]["role"] = "phase2-target"
        pod["spec"]["containers"][0]["command"] = ["python", "-m", "http.server", "8081", "--bind", "0.0.0.0", "--directory", "/tmp"]
        pod["spec"]["containers"][0]["readinessProbe"] = {"httpGet": {"path": "/", "port": 8081}, "periodSeconds": 2}
        self.create(pod)
        self.k("-n", self.test_ns, "wait", "--for=condition=Ready", "pod/target", "--timeout=120s")
        self.target = self.obj("-n", self.test_ns, "get", "pod", "target")
        self.target_url = "http://" + self.target["status"]["podIP"] + ":8081/"

    def case(self, case_id, function):
        print(case_id + ": measuring", flush=True)
        try:
            self.results["cases"][case_id] = function()
        except Exception as exc:
            self.results["cases"][case_id] = {"harness_error": str(exc)}
            self.results["errors"].append({"case": case_id, "error": str(exc)})
        self.save("results.json", self.results)
        print(case_id + ": recorded", flush=True)

    def rbac(self):
        matrix = []
        operations = [("get", "secrets", self.ns), ("list", "secrets", self.ns), ("create", "pods", self.ns),
                      ("create", "pods/exec", self.ns), ("patch", "deployments.apps", self.ns),
                      ("get", "persistentvolumeclaims", self.data_ns), ("delete", "persistentvolumeclaims", self.data_ns),
                      ("get", "secrets", self.test_ns), ("get", "nodes", None), ("get", "persistentvolumes", None),
                      ("list", "namespaces", None), ("create", "rolebindings.rbac.authorization.k8s.io", self.ns),
                      ("create", "serviceaccounts/token", self.ns), ("get", "secrets", "kube-system")]
        for verb, resource, namespace in operations:
            resource_type, _, subresource = resource.partition("/")
            command = self.groups + ["auth", "can-i", verb, resource_type]
            if subresource:
                command += ["--subresource=" + subresource]
            if namespace:
                command += ["-n", namespace]
            result = self.k(*command, check=False, role="operator impersonation authorization test")
            matrix.append({"verb": verb, "resource": resource, "namespace": namespace, "answer": result.stdout.strip(), "exit_code": result.returncode})
        same = f"/api/v1/namespaces/{self.ns}/secrets/{self.canary}"
        result = {"authorization": matrix, "token_identity": self.api("/apis/authentication.k8s.io/v1/selfsubjectreviews", "POST", {"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"}),
                  "canary_secret": self.api(same), "anonymous_control": self.api(same, authenticated=False),
                  "cross_namespace_secret": self.api(f"/api/v1/namespaces/{self.test_ns}/secrets/{self.canary}"),
                  "demo_database_secret": self.api(f"/api/v1/namespaces/{self.ns}/secrets/demo-database"),
                  "pvc": self.api(f"/api/v1/namespaces/{self.data_ns}/persistentvolumeclaims/{self.args.pvc}")}
        result["deployment_patch_dryrun"] = self.api(f"/apis/apps/v1/namespaces/{self.ns}/deployments/backend?dryRun=All", "PATCH",
                  {"metadata": {"annotations": {"phase2-test": self.run_id}}}, content_type="application/merge-patch+json")
        return result

    def identities(self):
        observations = {}
        shell = """set -eu
set -o pipefail
test -s /var/run/secrets/kubernetes.io/serviceaccount/token
PROJECT5_TEST_TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
printf 'Authorization: Bearer %s\\n' "$PROJECT5_TEST_TOKEN" | curl --silent --show-error --fail --max-time 8 --cacert /var/run/secrets/kubernetes.io/serviceaccount/ca.crt --header @- --header 'Content-Type: application/json' --data '{"apiVersion":"authentication.k8s.io/v1","kind":"SelfSubjectReview"}' https://kubernetes.default.svc/apis/authentication.k8s.io/v1/selfsubjectreviews
"""
        for name in ["frontend"]:
            result = self.k("-n", self.ns, "exec", "deployment/" + name, "--", "sh", "-ec", shell, check=False,
                            role="operator exec transport; workload's mounted token authenticates request")
            try:
                observation = json.loads(result.stdout)
                observations[name] = {"exit_code": result.returncode, "user_info": observation.get("status", {}).get("userInfo")}
            except json.JSONDecodeError:
                observations[name] = {"exit_code": result.returncode, "error": result.stderr}
        secret_shell = """set -eu
set -o pipefail
PROJECT5_TEST_TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
printf 'Authorization: Bearer %s\\n' "$PROJECT5_TEST_TOKEN" | curl --silent --show-error --fail --max-time 8 --cacert /var/run/secrets/kubernetes.io/serviceaccount/ca.crt --header @- "https://kubernetes.default.svc/api/v1/namespaces/""" + self.ns + "/secrets/" + self.canary + '" | sha256sum'
        observation = self.k("-n", self.ns, "exec", "deployment/frontend", "--", "sh", "-ec", secret_shell,
                             role="frontend's mounted token reads only the synthetic canary Secret", check=False)
        if observation.returncode == 0:
            observations["frontend_canary_secret"] = {"exit_code": 0, "response_sha256": observation.stdout.split()[0]}
        else:
            observations["frontend_canary_secret"] = {"exit_code": observation.returncode, "error": observation.stderr}
        observation = self.k("-n", self.data_ns, "exec", "deployment/postgres", "--", "sh", "-ec",
                             "if test -s /var/run/secrets/kubernetes.io/serviceaccount/token; then echo TOKEN_PRESENT; else echo TOKEN_ABSENT; fi; grep -E '^(Name|Uid|Gid):' /proc/1/status", check=False)
        observations["postgres"] = {"token_present": "TOKEN_PRESENT" in observation.stdout, "pid1": observation.stdout,
                                     "service_account": self.obj("-n", self.data_ns, "get", "deployment", "postgres")["spec"]["template"]["spec"].get("serviceAccountName"),
                                     "note": "No verified HTTPS client in this image; SQL-side token subject is measured in T08"}
        frontend = self.obj("-n", self.ns, "get", "pods", "-l", "app=frontend")["items"][0]["metadata"]["name"]
        process = self.k(*(self.groups + ["-n", self.ns, "exec", frontend, "--", "sh", "-ec", "printf 'SYNTHETIC-LATERAL-EXEC uid='; id -u"]),
                         role="actual pods/exec request using impersonated workload identity", check=False)
        observations["lateral_exec_frontend"] = {"exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
        return observations

    def network(self):
        database = f"postgres.{self.data_ns}.svc.cluster.local"
        backend = f"http://backend.{self.ns}.svc.cluster.local:8000/health/ready"
        frontend = f"http://frontend.{self.ns}.svc.cluster.local:8080/"
        result = {"backend_to_database": self.probe("tcp", {"host": database, "port": 5432, "postgres": True}),
                  "backend_to_frontend": self.probe("http", {"url": frontend}),
                  "cross_namespace_to_backend": self.probe("http", {"url": backend}, pod="target", namespace=self.test_ns),
                  "cross_namespace_to_database": self.probe("tcp", {"host": database, "port": 5432, "postgres": True}, pod="target", namespace=self.test_ns)}
        process = self.k("-n", self.ns, "exec", "deployment/frontend", "--", "wget", "-qO-", "-T", "4", backend, check=False)
        result["frontend_to_backend_required"] = {"exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
        # SSLRequest elicits a single PostgreSQL protocol byte. No credentials,
        # SQL login, or authentication bypass is attempted from the frontend.
        process = self.k("-n", self.ns, "exec", "deployment/frontend", "--", "sh", "-ec",
                         "printf '\\000\\000\\000\\010\\004\\322\\026\\057' | nc -w 2 " + database + " 5432", check=False)
        result["frontend_to_database_prohibited"] = {"exit_code": process.returncode, "protocol_response": process.stdout, "stderr": process.stderr}
        control = {"before": self.probe("http", {"url": self.target_url})}
        policy = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": self.metadata("target-deny", self.test_ns),
                  "spec": {"podSelector": {"matchLabels": {"role": "phase2-target"}}, "policyTypes": ["Ingress"]}}
        self.create(policy)
        observations = []
        try:
            for _ in range(12):
                observation = self.probe("http", {"url": self.target_url})
                observations.append(observation)
                if len(observations) >= 2 and all(o.get("http_status") is None and "error" in o for o in observations[-2:]):
                    break
                time.sleep(1)
        finally:
            self.k("-n", self.test_ns, "delete", "networkpolicy", "target-deny", "--wait=true", role="operator fixture policy removal")
        control["with_fixture_only_deny"] = observations
        for _ in range(12):
            control["after_policy_removal"] = self.probe("http", {"url": self.target_url})
            if control["after_policy_removal"].get("http_status") == 200:
                break
            time.sleep(1)
        control["target_uid_unchanged"] = self.obj("-n", self.test_ns, "get", "pod", "target")["metadata"]["uid"] == self.target["metadata"]["uid"]
        result["cni_enforcement_control"] = control
        return result

    def runtime(self):
        result = {"backend": self.probe("runtime"), "controlled_setuid": self.probe("setuid")}
        command = """set -eu
id
for p in /proc/[0-9]*/status; do
  if grep -q '^Name:[[:space:]]*nginx$' "$p"; then
    printf '%s\n' "$p"
    grep -E '^(Name|Uid|Gid|CapEff|CapBnd|NoNewPrivs|Seccomp|Seccomp_filters):' "$p"
  fi
done
"""
        observation = self.k("-n", self.ns, "exec", "deployment/frontend", "--", "sh", "-ec", command)
        result["frontend_processes"] = observation.stdout
        path = "/usr/share/nginx/html/.phase2-" + self.run_id
        command = "set -eu; trap 'rm -f " + path + "' EXIT; test ! -e " + path + "; printf SYNTHETIC-ROOTFS > " + path + "; test \"$(cat " + path + ")\" = SYNTHETIC-ROOTFS; echo ROOTFS_WRITE_SUCCEEDED"
        observation = self.k("-n", self.ns, "exec", "deployment/frontend", "--", "sh", "-ec", command, check=False)
        result["frontend_rootfs_write"] = {"exit_code": observation.returncode, "stdout": observation.stdout, "stderr": observation.stderr}
        return result

    def storage(self):
        result = {"backend_unprivileged_export_access": self.probe("volume", {"path": "/lab-shared/fictional-payroll.csv", "child_write": True})}
        pod = self.safe_pod(self.volume_pod, self.ns)
        pod["spec"]["volumes"] = [{"name": "data", "persistentVolumeClaim": {"claimName": self.args.pvc}}]
        pod["spec"]["containers"][0]["volumeMounts"] = [{"name": "data", "mountPath": "/storage", "readOnly": False}]
        self.fixtures.append(copy.deepcopy(pod))
        result["workload_token_create_pvc_pod"] = self.api(f"/api/v1/namespaces/{self.ns}/pods", "POST", pod)
        if result["workload_token_create_pvc_pod"].get("http_status") == 201:
            uid = result["workload_token_create_pvc_pod"]["metadata"]["uid"]
            self.created.append(("pod", self.ns, self.volume_pod, uid))
            self.k("-n", self.ns, "wait", "--for=condition=Ready", "pod/" + self.volume_pod, "--timeout=120s")
            result["second_pod_access"] = self.probe("volume", {"path": "/storage/exports/fictional-payroll.csv"}, pod=self.volume_pod, impersonate=True)
            result["concurrent_pvc_mounts"] = [{"pod": p["metadata"]["name"], "node": p["spec"].get("nodeName"),
                                                "phase": p["status"].get("phase")}
                                               for p in self.obj("-n", self.ns, "get", "pods")["items"]
                                               if any(v.get("persistentVolumeClaim", {}).get("claimName") == self.args.pvc for v in p["spec"].get("volumes", []))]
        return result

    def admission(self):
        variations = {"restricted_control": {}, "privileged": {"privileged": True, "allowPrivilegeEscalation": True}, "allow_privilege_escalation": {"allowPrivilegeEscalation": True},
                      "sys_admin_capability": {"capabilities": {"drop": ["ALL"], "add": ["SYS_ADMIN"]}},
                      "unconfined_seccomp": {"seccompProfile": {"type": "Unconfined"}}, "writable_rootfs": {"readOnlyRootFilesystem": False}}
        pods = []
        for name, changes in variations.items():
            pod = self.safe_pod("phase2-dry-" + name.replace("_", "-") + "-" + self.run_id, self.ns)
            pod["spec"]["containers"][0]["securityContext"].update(changes)
            pods.append((name, pod))
        for name in ["root", "host_network", "host_pid", "host_ipc", "hostpath", "invalid_type_control", "contradictory_privileged_control", "combined_privileged_host_access"]:
            pod = self.safe_pod("phase2-dry-" + name.replace("_", "-") + "-" + self.run_id, self.ns)
            if name == "root":
                pod["spec"]["securityContext"].update(runAsNonRoot=False, runAsUser=0)
            elif name == "hostpath":
                pod["spec"]["volumes"] = [{"name": "admission-only", "hostPath": {"path": "/project5-synthetic-admission-only", "type": "Directory"}}]
                pod["spec"]["containers"][0]["volumeMounts"] = [{"name": "admission-only", "mountPath": "/admission-only", "readOnly": True}]
            elif name == "invalid_type_control":
                pod["spec"]["containers"][0]["securityContext"]["privileged"] = "not-a-boolean"
            elif name == "contradictory_privileged_control":
                pod["spec"]["containers"][0]["securityContext"]["privileged"] = True
            elif name == "combined_privileged_host_access":
                pod["spec"].update(hostNetwork=True, hostPID=True, hostIPC=True)
                pod["spec"]["securityContext"].update(runAsUser=0, runAsNonRoot=False, seccompProfile={"type": "Unconfined"})
                pod["spec"]["containers"][0]["securityContext"].update(privileged=True, allowPrivilegeEscalation=True)
                pod["spec"]["volumes"] = [{"name": "admission-only", "hostPath": {"path": "/project5-synthetic-admission-only", "type": "Directory"}}]
                pod["spec"]["containers"][0]["volumeMounts"] = [{"name": "admission-only", "mountPath": "/admission-only", "readOnly": True}]
            else:
                pod["spec"][{"host_network": "hostNetwork", "host_pid": "hostPID", "host_ipc": "hostIPC"}[name]] = True
            pods.append((name, pod))
        results = {}
        for name, pod in pods:
            self.fixtures.append({"dry_run_only": True, "object": pod})
            results[name] = self.api(f"/api/v1/namespaces/{self.ns}/pods?dryRun=All", "POST", pod)
        existing = {p["metadata"]["name"] for p in self.obj("-n", self.ns, "get", "pods")["items"]}
        results["no_dangerous_pod_persisted"] = all(pod["metadata"]["name"] not in existing for _, pod in pods)
        return results

    def cross_namespace(self):
        return {"untrusted_namespace_http_crud": self.probe("http-crud", {"url": f"http://backend.{self.ns}.svc.cluster.local:8000"},
                                                          pod="target", namespace=self.test_ns),
                "source_pod": {"namespace": self.test_ns, "uid": self.target["metadata"]["uid"],
                               "service_account": self.target["spec"].get("serviceAccountName"), "automount_token": False,
                               "run_as_user": 65532}}

    def cleanup(self):
        results = []
        # Namespace removal owns its contained fixtures. Never delete by a
        # broad application selector; verify the unique run label and UID.
        for kind, namespace, name, uid in reversed(self.created):
            if namespace == self.test_ns:
                continue
            args = (["-n", namespace] if namespace else []) + ["get", kind, name, "-o", "json", "--ignore-not-found=true"]
            current = self.k(*args, check=False)
            if not current.stdout.strip():
                results.append({"kind": kind, "name": name, "already_absent": True})
                continue
            obj = json.loads(current.stdout)
            owned = obj["metadata"]["uid"] == uid and obj["metadata"].get("labels", {}).get("phase2-run") == self.run_id
            if not owned:
                results.append({"kind": kind, "name": name, "ownership_mismatch": True})
                continue
            deletion = self.k(*((["-n", namespace] if namespace else []) + ["delete", kind, name, "--wait=true", "--timeout=90s"]),
                              role="operator fixture cleanup", check=False)
            results.append({"kind": kind, "namespace": namespace, "name": name, "exit_code": deletion.returncode})
        self.save("cleanup.json", results)
        return results

    def run(self):
        try:
            self.setup()
            for name, function in [("T01", self.rbac), ("T02", self.identities), ("T03", self.network),
                                   ("T04", self.runtime), ("T05", self.storage), ("T06", self.admission),
                                   ("T07", self.cross_namespace), ("T08", lambda: {"backend_database_access": self.probe("database")})]:
                self.case(name, function)
        except Exception as exc:
            self.results["errors"].append({"setup_or_runner_error": str(exc)})
        finally:
            self.save("fixtures.json", self.fixtures)
            try:
                cleanup = self.cleanup()
                self.results["cleanup_succeeded"] = all(entry.get("already_absent") or entry.get("exit_code") == 0 for entry in cleanup)
                if hasattr(self, "before"):
                    after = self.snapshot()
                    db_after = self.probe("database")
                    integrity = {"source_unchanged": self.inputs == source_hashes(), "runtime_baseline_unchanged": self.before == after,
                                 "database_rows_unchanged": self.db_before.get("rows_sha256") == db_after.get("rows_sha256"),
                                 "before": self.before, "after": after, "database_before": self.db_before, "database_after": db_after}
                    self.save("integrity.json", integrity)
                    self.results["integrity"] = {key: integrity[key] for key in ("source_unchanged", "runtime_baseline_unchanged", "database_rows_unchanged")}
            except Exception as exc:
                self.results["errors"].append({"cleanup_or_integrity_error": str(exc)})
            self.results["finished_utc"] = datetime.now(timezone.utc).isoformat()
            self.save("results.json", self.results)
        print("Evidence: " + str(self.out), flush=True)
        if self.args.expect_baseline and not self.results["errors"]:
            cases = self.results["cases"]
            checks = {
                "token_secret_access": cases["T01"]["canary_secret"].get("http_status") == 200,
                "cross_namespace_secret_denied": cases["T01"]["cross_namespace_secret"].get("http_status") == 403,
                "frontend_lateral_exec": cases["T02"]["lateral_exec_frontend"]["exit_code"] == 0,
                "frontend_database_protocol_reachable": cases["T03"]["frontend_to_database_prohibited"]["protocol_response"] in ("S", "N"),
                "cni_positive_negative_positive": cases["T03"]["cni_enforcement_control"]["before"].get("http_status") == 200
                    and all(o.get("http_status") is None and "error" in o for o in cases["T03"]["cni_enforcement_control"]["with_fixture_only_deny"][-2:])
                    and cases["T03"]["cni_enforcement_control"]["after_policy_removal"].get("http_status") == 200,
                "root_backend": cases["T04"]["backend"].get("uid") == 0,
                "unprivileged_fixture_tamper_restored": cases["T05"]["backend_unprivileged_export_access"].get("restored_bytes_and_mode") is True,
                "second_pod_pvc_access": cases["T05"].get("second_pod_access", {}).get("synthetic_payroll_read") is True,
                "privileged_dryrun_accepted": cases["T06"]["privileged"].get("http_status") == 201,
                "no_dangerous_pod_launched": cases["T06"]["no_dangerous_pod_persisted"],
                "untrusted_http_write": cases["T07"]["untrusted_namespace_http_crud"].get("updated_value_observed") is True,
                "temporary_fixtures_removed": self.results.get("cleanup_succeeded") is True,
                **self.results.get("integrity", {}),
            }
            self.results["baseline_expectation_checks"] = checks
            self.save("results.json", self.results)
            for name, passed in checks.items():
                print(("MATCH " if passed else "MISMATCH ") + name, flush=True)
            return 0 if all(checks.values()) else 1
        return 1 if self.results["errors"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="project5")
    parser.add_argument("--data-namespace")
    parser.add_argument("--service-account", default="shared-workload")
    parser.add_argument("--pvc", default="postgres-data")
    parser.add_argument("--output", help="New directory under this project's evidence/")
    parser.add_argument("--expect-baseline", action="store_true", help="Check expected INSECURE observations; MATCH does not mean secure")
    args = parser.parse_args()
    with (ROOT / ".lab/security-tests.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise SystemExit(Lab(args).run())


if __name__ == "__main__":
    main()
