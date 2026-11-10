"""Separate secured expectations; reuses immutable V1 probes and dry-run fixtures.

Use scripts/validate_native.py for the full lifecycle, including cluster cleanup.
This runner owns only uniquely labelled fixtures, not the cluster lifecycle.
"""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import time

import baseline
from integrity import runtime_integrity

ROOT = baseline.ROOT
NATIVE = Path(__file__).with_name("native_probe.py")
V1_FIXTURES = ROOT / "evidence/v1/phase2-20261109T191255Z-336a65ecfb/fixtures.json"


def timed_out(value):
    return value.get("http_status") is None and "timed out" in value.get("detail", "").lower()


def denied(process):
    return process.returncode != 0 and "Forbidden" in process.stderr


class NativeLab(baseline.Lab):
    def probe(self, action, parameters=None, *, pod=None, namespace=None, impersonate=False):
        loader = "import json,sys; s=json.load(sys.stdin); g={'__name__':'probe_import'}; exec(s['v1'],g); exec(s['native'],g); g['main']()"
        bundle = json.dumps({"v1": baseline.PROBE.read_text(), "native": NATIVE.read_text()})
        args = (self.groups if impersonate else []) + ["-n", namespace or self.ns, "exec", "-i", pod or "deployment/backend",
                "--", "python", "-c", loader, action, json.dumps(dict(parameters or {}, run_id=self.run_id))]
        result = self.k(*args, stdin=bundle, source="probe.py + native_probe.py",
                        role="operator stdin transport; workload UID/network/SQL credentials", check=False)
        try:
            observation = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError("Probe transport failed: " + result.stderr[-600:])
        # Callers explicitly inspect expected HTTP timeout probes. Unexpected
        # process/probe failures cannot become successful security assertions.
        return observation

    def snapshot(self):
        state = super().snapshot()
        state["data_network_policies"] = self.obj("-n", self.data_ns, "get", "networkpolicies")["items"]
        state["data_namespace_labels"] = self.obj("get", "namespace", self.data_ns)["metadata"]["labels"]
        state["identities"] = {}
        for ns, name in [(self.ns, "frontend"), (self.ns, "backend"), (self.data_ns, "postgres")]:
            obj = self.obj("-n", ns, "get", "serviceaccount", name)
            state["identities"][ns + "/" + name] = {"uid": obj["metadata"]["uid"], "automount": obj.get("automountServiceAccountToken")}
        return state

    def export_state(self):
        p = self.k("-n", self.data_ns, "exec", "deployment/postgres", "--", "sh", "-ec",
                   "stat -c '%u:%g %a %n' /var/lib/postgresql/data/exports /var/lib/postgresql/data/exports/fictional-payroll.csv; sha256sum /var/lib/postgresql/data/exports/fictional-payroll.csv")
        return p.stdout

    def setup(self):
        super().setup()
        self.export_before = self.export_state()
        # Fixture namespace permits only the deliberately seeded setuid
        # positive control. Application/data PSA labels stay Restricted.
        self.k("label", "namespace", self.test_ns, "pod-security.kubernetes.io/enforce=privileged")
        self.k("-n", self.test_ns, "label", "pod", "target", "app=backend")
        source = self.safe_pod("source", self.test_ns)
        source["metadata"]["labels"].update(app="frontend", role="phase3-source")
        self.create(source)
        self.k("-n", self.test_ns, "wait", "--for=condition=Ready", "pod/source", "--timeout=120s")
        self.checks = {}

    def check(self, case, observations, checks):
        self.checks[case] = checks
        return {"observations": observations, "secured_checks": checks, "passed": all(checks.values())}

    def rbac(self):
        observations = super().rbac()
        direct = {}
        pod = self.safe_pod("rbac-denied-" + self.run_id, self.ns)
        calls = {
            "secret_read": ["-n", self.ns, "get", "secret", self.canary, "-o", "name"],
            "workload_create": ["create", "-f", "-", "--dry-run=server"],
            "deployment_modify": ["-n", self.ns, "patch", "deployment", "backend", "--type=merge", "--dry-run=server", "-p", '{"metadata":{"annotations":{"native-denied":"true"}}}'],
            "exec": ["create", "--raw", f"/api/v1/namespaces/{self.ns}/pods/unused/exec?command=id&stdout=true", "-f", "-"],
        }
        for name, args in calls.items():
            proc = self.k(*(self.groups + args), stdin=json.dumps(pod) if name == "workload_create" else ("" if name == "exec" else None),
                          source="restricted fixture" if name == "workload_create" else None,
                          check=False, role="actual API request impersonating backend identity")
            direct[name] = {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "forbidden": denied(proc)}
        observations["actual_impersonated_requests"] = direct
        return self.check("T01", observations, {
            "all_14_authorization_operations_denied": len(observations["authorization"]) == 14 and all(x["answer"] == "no" for x in observations["authorization"]),
            "no_mounted_backend_credential": observations["canary_secret"].get("credential_present") is False,
            "four_actual_requests_forbidden": all(x["forbidden"] for x in direct.values()),
        })

    def identities(self):
        observations = {}
        for name in ["frontend", "backend", "postgres"]:
            ns = self.data_ns if name == "postgres" else self.ns
            spec = self.obj("-n", ns, "get", "deployment", name)["spec"]["template"]["spec"]
            p = self.k("-n", ns, "exec", "deployment/" + name, "--", "sh", "-ec",
                       "if test -e /var/run/secrets/kubernetes.io/serviceaccount/token; then echo PRESENT; else echo ABSENT; fi; id -u")
            account = spec["serviceAccountName"]
            auth = []
            identity = f"system:serviceaccount:{ns}:{account}"
            groups = ["--as=" + identity, "--as-group=system:authenticated", "--as-group=system:serviceaccounts", "--as-group=system:serviceaccounts:" + ns]
            for verb, resource, sub in [("get", "secrets", None), ("create", "pods", None), ("patch", "deployments.apps", None), ("create", "pods", "exec")]:
                proc = self.k(*(groups + ["auth", "can-i", verb, resource, "-n", self.ns] + (["--subresource=" + sub] if sub else [])), check=False)
                auth.append(proc.stdout.strip())
            observations[name] = {"service_account": account, "identity": identity, "automount": spec.get("automountServiceAccountToken"),
                                  "token_absent": p.stdout.splitlines()[0] == "ABSENT", "uid": int(p.stdout.splitlines()[1]), "authorization": auth}
        frontend = self.obj("-n", self.ns, "get", "pods", "-l", "app=frontend")["items"][0]["metadata"]["name"]
        lateral = self.k(*(self.groups + ["-n", self.ns, "exec", frontend, "--", "id", "-u"]), check=False)
        observations["lateral_exec"] = {"exit_code": lateral.returncode, "stderr": lateral.stderr}
        return self.check("T02", observations, {
            "three_distinct_dedicated_identities": len({observations[n]["identity"] for n in ["frontend", "backend", "postgres"]}) == 3,
            "all_tokenless_and_unprivileged_in_api": all(observations[n]["automount"] is False and observations[n]["token_absent"] and observations[n]["authorization"] == ["no"] * 4 for n in ["frontend", "backend", "postgres"]),
            "lateral_exec_forbidden": denied(lateral),
        })

    def cni_control(self, direction):
        def request():
            return self.probe("http", {"url": self.target_url}, pod="source", namespace=self.test_ns)
        before = request()
        name = "fixture-deny-" + direction.lower()
        obj = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": self.metadata(name, self.test_ns),
               "spec": {"podSelector": {"matchLabels": {"role": "phase2-target" if direction == "Ingress" else "phase3-source"}}, "policyTypes": [direction]}}
        self.create(obj)
        blocked = []
        try:
            for _ in range(12):
                blocked.append(request())
                if len(blocked) >= 2 and all(timed_out(x) for x in blocked[-2:]):
                    break
                time.sleep(1)
        finally:
            self.k("-n", self.test_ns, "delete", "networkpolicy", name, "--wait=true")
        after = None
        for _ in range(12):
            after = request()
            if after.get("http_status") == 200:
                break
            time.sleep(1)
        target_now = self.obj("-n", self.test_ns, "get", "pod", "target")
        same_uid = target_now["metadata"]["uid"] == self.target["metadata"]["uid"]
        return {"before": before, "during": blocked, "after": after, "target_uid_unchanged": same_uid,
                "enforced": before.get("http_status") == 200 and len(blocked) >= 2 and all(timed_out(x) for x in blocked[-2:]) and after.get("http_status") == 200 and same_uid}

    def network(self):
        db = f"postgres.{self.data_ns}.svc.cluster.local"
        back = f"http://backend.{self.ns}.svc.cluster.local:8000/health/ready"
        front = f"http://frontend.{self.ns}.svc.cluster.local:8080/"
        o = {"required_backend_database": self.probe("tcp", {"host": db, "port": 5432, "postgres": True}),
             "backend_frontend": self.probe("http", {"url": front}),
             "backend_external_controlled_target": self.probe("http", {"url": self.target_url}),
             "foreign_frontend_label_to_backend": self.probe("http", {"url": back}, pod="source", namespace=self.test_ns),
             "foreign_backend_label_to_database": self.probe("tcp", {"host": db, "port": 5432, "postgres": True}, pod="target", namespace=self.test_ns)}
        p = self.k("-n", self.ns, "exec", "deployment/frontend", "--", "wget", "-qO-", "-T", "4", back, check=False)
        o["required_frontend_backend"] = {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
        started = time.monotonic()
        p = self.k("-n", self.ns, "exec", "deployment/frontend", "--", "sh", "-ec", "printf '\\000\\000\\000\\010\\004\\322\\026\\057' | nc -w 2 " + db + " 5432", check=False)
        o["frontend_database"] = {"exit_code": p.returncode, "protocol_response": p.stdout,
                                  "stderr": p.stderr, "elapsed_seconds": time.monotonic() - started}
        api_ip = self.obj("-n", "default", "get", "service", "kubernetes")["spec"]["clusterIP"]
        o["backend_api_egress"] = self.probe("tcp", {"host": api_ip, "port": 443})
        o["cni_ingress_control"] = self.cni_control("Ingress")
        o["cni_egress_control"] = self.cni_control("Egress")
        return self.check("T03", o, {
            "both_required_links_work_with_dns": o["required_backend_database"].get("postgres_ssl_response") in ["N", "S"] and o["required_frontend_backend"]["exit_code"] == 0 and '"reachable"' in o["required_frontend_backend"]["stdout"],
            "frontend_cannot_contact_database": o["frontend_database"]["exit_code"] != 0 and
                o["frontend_database"]["protocol_response"] == "" and
                o["frontend_database"]["elapsed_seconds"] >= 1.5,
            "backend_cannot_contact_frontend_or_external_target": timed_out(o["backend_frontend"]) and timed_out(o["backend_external_controlled_target"]),
            "foreign_label_spoofing_does_not_allow_ingress": timed_out(o["foreign_frontend_label_to_backend"]) and o["foreign_backend_label_to_database"].get("connected") is False and o["foreign_backend_label_to_database"].get("error") == "TimeoutError",
            "backend_api_egress_blocked": o["backend_api_egress"].get("connected") is False and o["backend_api_egress"].get("error") == "TimeoutError",
            "calico_enforces_ingress_and_egress": o["cni_ingress_control"]["enforced"] and o["cni_egress_control"]["enforced"],
        })

    def runtime(self):
        o = {"backend": self.probe("runtime"), "original_setuid_setup": self.probe("setuid")}
        for name, ns in [("frontend", self.ns), ("postgres", self.data_ns)]:
            p = self.k("-n", ns, "exec", "deployment/" + name, "--", "sh", "-ec",
                       "id -u; grep -E '^(Uid|CapEff|CapBnd|NoNewPrivs|Seccomp):' /proc/1/status; if touch /etc/.phase3-canary 2>/dev/null; then rm /etc/.phase3-canary; echo ROOT_WRITABLE; else echo ROOT_WRITE_DENIED; fi; touch /tmp/phase3-canary; rm /tmp/phase3-canary; echo TMP_WRITABLE")
            o[name] = {"exit_code": p.returncode, "stdout": p.stdout}
        # Positive and negative fixtures differ only in allowPrivilegeEscalation.
        for name, escalation in [("suid-positive", True), ("suid-secured", False)]:
            pod = self.safe_pod(name, self.test_ns)
            pod["spec"]["containers"][0]["image"] = "project5-setuid-control:native"
            pod["spec"]["containers"][0]["securityContext"]["allowPrivilegeEscalation"] = escalation
            self.create(pod)
            self.k("-n", self.test_ns, "wait", "--for=condition=Ready", "pod/" + name, "--timeout=120s")
            o[name] = self.probe("setuid-control", pod=name, namespace=self.test_ns)
            self.k("-n", self.test_ns, "delete", "pod", name, "--wait=true")
        b = o["backend"]
        positive = o["suid-positive"].get("seeded_setuid", {}).get("observation", {})
        secured = o["suid-secured"].get("seeded_setuid", {}).get("observation", {})
        return self.check("T04", o, {
            "backend_nonroot_no_caps_seccomp_nnp": b.get("uid") == 10001 and b.get("pid1", {}).get("NoNewPrivs") == "1" and b.get("pid1", {}).get("Seccomp") == "2" and int(b.get("pid1", {}).get("CapEff", "1"), 16) == 0 and int(b.get("pid1", {}).get("CapBnd", "1"), 16) == 0,
            "backend_readonly_root_writable_explicit_tmp": b.get("root_mount_read_only") is True and b.get("rootfs_write") is False and b.get("explicit_tmp_writable") is True,
            "raw_socket_denied": b.get("raw_socket_created_without_sending_packets") is False and b.get("raw_socket_error") == "PermissionError",
            "other_tiers_nonroot_hardened": all(o[name]["stdout"].splitlines()[0] == str(uid) and "NoNewPrivs:\t1" in o[name]["stdout"] and "Seccomp:\t2" in o[name]["stdout"] and "CapEff:\t0000000000000000" in o[name]["stdout"] and "ROOT_WRITE_DENIED" in o[name]["stdout"] and "TMP_WRITABLE" in o[name]["stdout"] for name, uid in [("frontend",101),("postgres",70)]),
            "matched_setuid_fixture_proves_no_new_privileges": positive.get("ruid") == 65532 and positive.get("euid") == 0 and positive.get("root_canary_read") is True and secured.get("euid") == 65532 and secured.get("root_canary_read") is False,
        })

    def storage(self):
        o = {"backend_export_access": self.probe("volume-denials", {"path": "/lab-shared/fictional-payroll.csv"})}
        spec = self.obj("-n", self.ns, "get", "deployment", "backend")["spec"]["template"]["spec"]
        o["backend_volumes"] = spec.get("volumes", [])
        wrong = self.safe_pod("wrong-namespace-pvc", self.ns)
        wrong["spec"]["volumes"] = [{"name": "data", "persistentVolumeClaim": {"claimName": self.args.pvc}}]
        wrong["spec"]["containers"][0]["volumeMounts"] = [{"name":"data", "mountPath":"/storage"}]
        self.create(wrong)
        for _ in range(20):
            events = self.obj("-n", self.ns, "get", "events", "--field-selector=involvedObject.name=wrong-namespace-pvc")["items"]
            if any("persistentvolumeclaim" in e.get("message", "").lower() and "not found" in e.get("message", "").lower() for e in events):
                break
            time.sleep(1)
        o["wrong_namespace_events"] = [{"reason":e.get("reason"),"message":e.get("message")} for e in events]
        o["wrong_namespace_phase"] = self.obj("-n", self.ns, "get", "pod", "wrong-namespace-pvc")["status"]["phase"]
        attacker = self.safe_pod("nonowner-pvc", self.data_ns)
        attacker["spec"]["volumes"] = [{"name":"data", "persistentVolumeClaim":{"claimName":self.args.pvc}}]
        attacker["spec"]["containers"][0]["volumeMounts"] = [{"name":"data", "mountPath":"/storage"}]
        # No fsGroup: changing a shared PVC's group would distort the test.
        self.create(attacker)
        self.k("-n", self.data_ns, "wait", "--for=condition=Ready", "pod/nonowner-pvc", "--timeout=120s")
        o["operator_created_nonowner_pod"] = self.probe("volume-denials", {"path":"/storage/exports/fictional-payroll.csv"}, pod="nonowner-pvc", namespace=self.data_ns)
        o["export_state"] = self.export_state()
        return self.check("T05", o, {
            "backend_has_no_export_or_pvc_mount": all("persistentVolumeClaim" not in v for v in o["backend_volumes"]) and o["backend_export_access"].get("read", {}).get("errno") == 2,
            "pvc_namespace_boundary": o["wrong_namespace_phase"] == "Pending" and any("not found" in e.get("message", "").lower() for e in events),
            "nonowner_read_and_append_denied": all(o["operator_created_nonowner_pod"].get(a, {}).get("errno") == 13 for a in ["read","append"]),
            "export_permissions_and_bytes_preserved": o["export_state"] == self.export_before and "70:70 700" in o["export_state"] and "70:70 600" in o["export_state"],
        })

    def admission(self):
        originals = [f["object"] for f in json.loads(V1_FIXTURES.read_text()) if f.get("dry_run_only")]
        o = {"baseline_fixture_file_sha256": baseline.sha(V1_FIXTURES.read_bytes()), "namespaces": {}}
        checks = {}
        for ns in [self.ns, self.data_ns]:
            cases = {}
            for original in originals:
                obj = copy.deepcopy(original)
                key = obj["metadata"]["name"].removeprefix("phase2-dry-").rsplit("-", 1)[0].replace("-", "_")
                obj["metadata"] = self.metadata("native-dry-" + key.replace("_","-") + "-" + self.run_id, ns)
                for c in obj["spec"]["containers"]:
                    c["image"] = self.image
                self.fixtures.append({"dry_run_only":True,"object":obj,"original_sha256":baseline.sha(original)})
                proc = self.k("create", "--raw", f"/api/v1/namespaces/{ns}/pods?dryRun=All", "-f", "-", stdin=json.dumps(obj),
                              source="exact V1 dry-run spec; metadata/image normalized", check=False, role="operator dry-run isolates PSA from workload RBAC")
                response = json.loads(proc.stdout) if proc.returncode == 0 else None
                cases[key] = {"exit_code":proc.returncode,"accepted":proc.returncode == 0,"stderr":proc.stderr,
                              "psa_rejection": "violates PodSecurity" in proc.stderr,
                              "response_kind": response.get("kind") if response else None}
                current = self.k("-n", ns, "get", "pod", obj["metadata"]["name"], "-o", "name", "--ignore-not-found=true")
                cases[key]["not_persisted"] = not current.stdout.strip()
            o["namespaces"][ns] = cases
            restricted = set(cases) - {"restricted_control", "writable_rootfs", "invalid_type_control", "contradictory_privileged_control"}
            checks[ns + ":dangerous_specs_rejected_by_psa"] = len(restricted) == 10 and all(cases[k]["psa_rejection"] for k in restricted)
            checks[ns + ":restricted_control_admitted"] = cases["restricted_control"]["accepted"]
            checks[ns + ":writable_rootfs_documents_psa_limit"] = cases["writable_rootfs"]["accepted"]
            checks[ns + ":invalid_controls_rejected_by_core"] = all(not cases[k]["accepted"] and not cases[k]["psa_rejection"] for k in ["invalid_type_control","contradictory_privileged_control"])
            checks[ns + ":no_dryrun_pods_persisted"] = all(x["not_persisted"] for x in cases.values())
        return self.check("T06", o, checks)

    def cross_namespace(self):
        o = {"foreign_crud": self.probe("http-crud", {"url":f"http://backend.{self.ns}.svc.cluster.local:8000"}, pod="source", namespace=self.test_ns),
             "foreign_positive_http_control": self.probe("http", {"url":self.target_url}, pod="source", namespace=self.test_ns),
             "source": {"uid":65532,"token_mounted":False,"namespace":self.test_ns,"spoofed_app_label":"frontend"}}
        err = o["foreign_crud"].get("probe_error", {})
        return self.check("T07", o, {"untrusted_crud_connection_times_out": err.get("type") in ["URLError","TimeoutError"] and "timed out" in err.get("detail", "").lower(),
                                      "same_source_positive_control_works": o["foreign_positive_http_control"].get("http_status") == 200})

    def database(self):
        o = self.probe("database")
        role = o.get("role", {})
        return self.check("T08", o, {
            "application_role_has_no_admin_attributes": role.get("name") == "inventory_app" and all(role.get(k) is False for k in ["superuser","create_database","create_role","replication","bypass_rls"]),
            "crud_allowed_truncate_denied": all(o.get("crud_privileges", {}).get(k) is True for k in ["SELECT","INSERT","UPDATE","DELETE"]) and o.get("crud_privileges", {}).get("TRUNCATE") is False,
            "file_reads_and_ddl_denied_independently_of_token_absence": len(o.get("denied_actions", {})) == 5 and all(not v["allowed"] and v.get("sqlstate") == "42501" for v in o["denied_actions"].values()),
            "admin_tcp_login_rejected_by_hba": o.get("admin_tcp", {}).get("allowed") is False and o.get("admin_tcp", {}).get("hba_reject") is True,
        })

    def run(self):
        try:
            self.setup()
            for case, fn in [("T01",self.rbac),("T02",self.identities),("T03",self.network),("T04",self.runtime),
                             ("T05",self.storage),("T06",self.admission),("T07",self.cross_namespace),("T08",self.database)]:
                self.case(case, fn)
        except Exception as exc:
            self.results["errors"].append({"setup_or_runner_error":str(exc)})
        finally:
            self.save("fixtures.json",self.fixtures)
            try:
                cleanup = self.cleanup()
                self.results["cleanup_succeeded"] = all(e.get("already_absent") or e.get("exit_code") == 0 for e in cleanup)
                if hasattr(self, "before"):
                    after, db_after = self.snapshot(), self.probe("database")
                    runtime = runtime_integrity(self.before, after, self.args.integrity_mode)
                    integrity = {"source_unchanged":self.inputs == baseline.source_hashes(),
                                 **runtime,
                                 "database_rows_unchanged":self.db_before.get("rows_sha256") is not None and self.db_before.get("rows_sha256") == db_after.get("rows_sha256"),
                                 "export_unchanged":hasattr(self,"export_before") and self.export_before == self.export_state(),
                                 "before":self.before,"after":after,"database_before":self.db_before,"database_after":db_after}
                    self.save("integrity.json",integrity)
                    self.results["integrity"] = {k:v for k,v in integrity.items() if isinstance(v,bool)}
            except Exception as exc:
                self.results["errors"].append({"cleanup_or_integrity_error":str(exc)})
            self.results["finished_utc"] = datetime.now(timezone.utc).isoformat()
            integrity = self.results.get("integrity", {})
            required_integrity = ["source_unchanged", "database_rows_unchanged", "export_unchanged", "runtime_expectation_met"]
            self.results["passed"] = not self.results["errors"] and len(self.results["cases"]) == 8 and all(c.get("passed") for c in self.results["cases"].values()) and self.results.get("cleanup_succeeded") is True and all(integrity.get(key) is True for key in required_integrity)
            self.save("results.json",self.results)
        for case, value in self.results["cases"].items():
            print(case + ": " + ("PASS" if value.get("passed") else "FAIL"), flush=True)
        print("Evidence: " + str(self.out),flush=True)
        return 0 if self.results["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True)
    parser.add_argument("--integrity-mode", choices=["migration", "regression"], default="migration",
                        help="expect security configuration to change (V1 to V2) or remain stable (V2 to V2)")
    args = parser.parse_args()
    args.namespace, args.data_namespace = "project5", "project5-data"
    args.service_account, args.pvc = "backend", "postgres-data"
    with (ROOT / ".lab/security-tests.lock").open("w") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise SystemExit(NativeLab(args).run())
