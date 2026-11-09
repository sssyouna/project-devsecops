"""Run Phase 3 sequentially and always remove only project5-lab.

Requires the existing cached Docker bases/Calico images, kind and Python 3.
Run from any directory: python3 devsecops-project5/scripts/validate_native.py
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def capture(args):
    return subprocess.check_output(args,cwd=ROOT,text=True)


def git_state():
    def git(*args): return capture(["git",*args])
    files = [ROOT / p for p in ["tests/security/baseline.py","tests/security/probe.py","tests/functional.py","tests/browser.py"]]
    files += [p for p in (ROOT / "evidence/v1").rglob("*") if p.is_file()]
    outside = [s for s in git("status","--porcelain=v1","--untracked-files=all").splitlines() if not s[3:].startswith("devsecops-project5/")]
    return {"branch":git("branch","--show-current").strip(), "head":git("rev-parse","HEAD").strip(),
            "protected_refs":{r:git("rev-parse",r).strip() for r in ["main","refs/heads/v1-insecure","refs/tags/v1-insecure","refs/tags/v1-insecure^{}"]},
            "index_sha256":hashlib.sha256(git("ls-files","--stage").encode()).hexdigest(),
            "outside_status":outside,
            "outside_diff_sha256":hashlib.sha256(git("diff","--", ":(exclude)devsecops-project5").encode()).hexdigest(),
            "immutable_sha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}


def main():
    os.chdir(ROOT)
    before = git_state()
    if before["branch"] != "v2-secured" or before["head"] != "aeb8e9de32a14aba32e6f923aad9c0deb5a043ec":
        raise RuntimeError("Expected the approved V2 branch and V1 baseline HEAD")
    if len(before["outside_status"]) != 17 or any(not s.startswith(" D ") for s in before["outside_status"]):
        raise RuntimeError("Unrelated working tree differs from the 17 known unstaged deletions")
    if capture(["git","diff","--cached","--name-only"]).strip():
        raise RuntimeError("Refusing to run with staged changes")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = ROOT / "evidence/v2/native" / stamp
    out.mkdir(parents=True,exist_ok=False)
    def save(name,obj):
        text = json.dumps(obj,indent=2,sort_keys=True) + "\n"
        if JWT.search(text): raise RuntimeError("Token-shaped output refused")
        (out/name).write_text(text)
    save("git-before.json",before)
    state = {"started_utc":datetime.now(timezone.utc).isoformat(),"steps":[],"errors":[],"evidence":str(out.relative_to(ROOT))}
    forward = None
    env = dict(os.environ,PROJECT5_PORT="18080",PROJECT5_BASE="http://127.0.0.1:18080",PYTHONDONTWRITEBYTECODE="1")
    prefix = [str(ROOT/".lab/bin/kubectl"),"--kubeconfig",str(ROOT/".lab/kubeconfig"),"--context","kind-project5-lab"]

    def run(name,args,timeout=900,check=True):
        print(name + ": running",flush=True)
        started = datetime.now(timezone.utc).isoformat()
        # Poll a file-backed subprocess so a signal cannot strand a setup
        # child after the finally block has removed its cluster.
        path = out/(name+".txt")
        with path.open("w") as stream:
            proc = subprocess.Popen(args,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except BaseException:
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL); proc.wait()
                raise
        text = path.read_text()
        if JWT.search(text):
            path.unlink()
            raise RuntimeError("Refused token-shaped command output")
        step = {"name":name,"argv":args,"started_utc":started,"exit_code":code,"log":path.name}
        state["steps"].append(step); save("run.json",state)
        print(name + ": " + str(code),flush=True)
        if check and code: raise RuntimeError(name + " failed; see " + path.name)
        return code

    def interrupted(sig,frame): raise RuntimeError("Interrupted by signal " + str(sig))
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    try:
        run("cluster",["bash","scripts/lab.sh","cluster"],timeout=1800)
        run("build",["bash","scripts/lab.sh","build"],timeout=900)
        run("setuid-build",["docker","build","--network=none","--pull=false","-f","tests/security/setuid.Dockerfile","-t","project5-setuid-control:native","tests/security"],timeout=180)
        run("setuid-load",["kind","load","docker-image","--name","project5-lab","project5-setuid-control:native"],timeout=180)
        run("deploy",["bash","scripts/lab.sh","deploy"],timeout=900)
        with (out/"port-forward.txt").open("w") as log:
            forward = subprocess.Popen(["bash","scripts/lab.sh","forward"],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        for _ in range(60):
            if forward.poll() is not None: raise RuntimeError("Port-forward exited")
            try:
                with urlopen(env["PROJECT5_BASE"]+"/api/items",timeout=3) as response:
                    if response.status == 200: break
            except OSError: time.sleep(1)
        else: raise RuntimeError("Frontend never became ready")
        run("functional-before",[sys.executable,"tests/native_functional.py","--persistence"],timeout=600)
        security_code = run("security",[sys.executable,"tests/security/native.py","--output",str(out/"security")],timeout=1200,check=False)
        run("functional-after",[sys.executable,"tests/native_functional.py"],timeout=180)
        if security_code: raise RuntimeError("Security checks failed; see security/results.json")
    except BaseException as exc:
        state["errors"].append({"type":type(exc).__name__,"message":str(exc)})
        print(str(exc),file=sys.stderr,flush=True)
    finally:
        # Evidence is captured before cleanup, including on failed runs.
        for ns in ["project5","project5-data","kube-system"]:
            try:
                run("diagnostics-"+ns,prefix+["-n",ns,"get","pods,events","-o","json"],timeout=30,check=False)
            except Exception as exc: state["errors"].append({"diagnostics":str(exc)})
        for ns,name in [("project5","backend"),("project5","frontend"),("project5-data","postgres")]:
            try: run("container-"+name,prefix+["-n",ns,"logs","deployment/"+name,"--all-containers=true","--tail=150"],timeout=20,check=False)
            except Exception as exc: state["errors"].append({"container_logs":str(exc)})
        if forward is not None and forward.poll() is None:
            os.killpg(forward.pid,signal.SIGTERM)
            forward.wait(timeout=15)
        cleanup = {"cluster":"project5-lab"}
        try:
            cleanup["delete_exit_code"] = run("cluster-delete",["bash","scripts/lab.sh","down"],timeout=180,check=False)
            remaining = capture(["docker","ps","-a","--filter","label=io.x-k8s.kind.cluster=project5-lab","--format","{{.Names}}"])
            cleanup["remaining_cluster_containers"] = remaining.splitlines()
            cleanup["deleted"] = cleanup["delete_exit_code"] == 0 and not remaining.strip()
        except Exception as exc:
            cleanup.update(deleted=False,error=str(exc))
        save("cleanup.json",cleanup)
        after = git_state()
        save("git-after.json",after)
        state["git_safety_unchanged"] = before == after
        state["cluster_deleted"] = cleanup["deleted"]
        state["finished_utc"] = datetime.now(timezone.utc).isoformat()
        state["passed"] = not state["errors"] and state["git_safety_unchanged"] and state["cluster_deleted"]
        save("run.json",state)
    print("Evidence: " + str(out),flush=True)
    return 0 if state["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
