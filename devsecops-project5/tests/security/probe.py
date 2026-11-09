"""Small in-container probes. Supplied on stdin by baseline.py; installs nothing.

Only synthetic canaries are modified. Tokens stay inside the workload process.
The setuid experiment requires an already-root operator and removes its seeded
helper; it is not an exploit of an existing setuid program or a host escape.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SA = Path("/var/run/secrets/kubernetes.io/serviceaccount")
PAYROLL = b"employee,salary,classification\nDEMO-PERSON-001,12345,SYNTHETIC-ONLY\n"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def api(args):
    path = args["path"]
    assert path.startswith(("/api/v1/namespaces/project5", "/apis/apps/v1/namespaces/project5",
                            "/apis/authentication.k8s.io/v1/selfsubjectreviews"))
    headers = {"Content-Type": args.get("content_type", "application/json")}
    if args.get("authenticated", True):
        headers["Authorization"] = "Bearer " + (SA / "token").read_text().strip()
    body = json.dumps(args["body"]).encode() if "body" in args else None
    request = Request("https://kubernetes.default.svc" + path, data=body,
                      headers=headers, method=args.get("method", "GET"))
    context = ssl.create_default_context(cafile=str(SA / "ca.crt"))
    try:
        with urlopen(request, context=context, timeout=8) as response:
            code, data = response.status, json.load(response)
    except HTTPError as exc:
        code, data = exc.code, json.load(exc)
    result = {"http_status": code, "kind": data.get("kind"),
              "metadata": {k: data.get("metadata", {}).get(k) for k in ("name", "namespace", "uid")}}
    if data.get("kind") == "Status":
        result.update({k: data.get(k) for k in ("reason", "message", "code")})
    elif data.get("kind") == "Secret":
        result["data_keys"] = sorted(data.get("data", {}))
        result["data_sha256"] = {k: digest(base64.b64decode(v)) for k, v in data.get("data", {}).items()}
    elif data.get("kind") == "SelfSubjectReview":
        result["user_info"] = data["status"]["userInfo"]
    elif data.get("kind") == "Pod":
        spec = data["spec"]
        result["spec"] = {k: spec.get(k) for k in ("hostNetwork", "hostPID", "hostIPC", "securityContext", "volumes")}
        result["spec"]["containers"] = [{"name": c["name"], "securityContext": c.get("securityContext", {})}
                                           for c in spec["containers"]]
    elif data.get("kind") == "Deployment":
        result["dryrun_annotation"] = data.get("metadata", {}).get("annotations", {}).get("phase2-test")
    elif data.get("kind") == "PersistentVolumeClaim":
        result["spec"] = {k: data["spec"].get(k) for k in ("accessModes", "volumeName", "storageClassName")}
        result["phase"] = data.get("status", {}).get("phase")
    elif "items" in data:
        result["resource_names"] = [item["metadata"]["name"] for item in data["items"]]
    return result


def http(args):
    try:
        with urlopen(args["url"], timeout=args.get("timeout", 2)) as response:
            data = response.read(131072)
            return {"http_status": response.status, "bytes": len(data), "sha256": digest(data)}
    except HTTPError as exc:
        return {"http_status": exc.code}
    except (OSError, URLError) as exc:
        return {"http_status": None, "error": type(exc).__name__, "detail": str(exc)}


def tcp(args):
    result = {"host": args["host"], "port": args["port"]}
    try:
        result["resolved_addresses"] = sorted({row[4][0] for row in socket.getaddrinfo(args["host"], args["port"], type=socket.SOCK_STREAM)})
        with socket.create_connection((args["host"], args["port"]), timeout=3) as connection:
            result["connected"] = True
            if args.get("postgres"):
                connection.sendall(bytes.fromhex("0000000804d2162f"))  # PostgreSQL SSLRequest
                result["postgres_ssl_response"] = connection.recv(1).decode("ascii")
    except OSError as exc:
        result.update(connected=False, error=type(exc).__name__, detail=str(exc))
    return result


def process_status(path):
    wanted = {"Name", "Uid", "Gid", "CapEff", "CapBnd", "NoNewPrivs", "Seccomp", "Seccomp_filters"}
    return {key: value.strip() for line in Path(path).read_text().splitlines()
            for key, value in [line.split(":", 1)] if key in wanted}


def runtime(args):
    result = {"uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(),
              "pid1": process_status("/proc/1/status"), "probe": process_status("/proc/self/status"),
              "token_present": (SA / "token").is_file()}
    path = Path("/app") / (".phase2-" + args["run_id"])
    try:
        with path.open("xb") as stream:
            stream.write(b"SYNTHETIC-ROOTFS-CANARY")
        result["rootfs_write"] = path.read_bytes() == b"SYNTHETIC-ROOTFS-CANARY"
    except OSError as exc:
        result.update(rootfs_write=False, rootfs_error=type(exc).__name__)
    finally:
        if path.exists():
            path.unlink()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP):
            result["raw_socket_created_without_sending_packets"] = True
    except OSError as exc:
        result.update(raw_socket_created_without_sending_packets=False, raw_socket_error=type(exc).__name__)
    return result


def setuid(args):
    if os.geteuid() != 0:
        return {"attempted": False, "reason": "Root is required to seed the controlled root-owned helper"}
    result = {"attempted": True, "precondition": "UID 0 operator seeds a temporary setuid copy of Python; no existing helper is exploited"}
    with tempfile.TemporaryDirectory(prefix="phase2-" + args["run_id"] + "-", dir="/tmp") as directory:
        os.chmod(directory, 0o755)
        canary = Path(directory) / "synthetic-root-only"
        with canary.open("xb") as stream:
            stream.write(b"SYNTHETIC-ROOT-ONLY")
        canary.chmod(0o600)
        helper = Path(directory) / "python-setuid-helper"
        shutil.copyfile(sys.executable, helper)
        helper.chmod(0o4755)
        child = """import json,os,sys
result={'ruid':os.getuid(),'euid':os.geteuid()}
try:
 with open(sys.argv[1],'rb') as f: result['root_canary_read']=f.read()==b'SYNTHETIC-ROOT-ONLY'
except PermissionError: result['root_canary_read']=False
print(json.dumps(result))
"""
        for name, executable in [("ordinary_child", sys.executable), ("seeded_setuid_child", str(helper))]:
            process = subprocess.run([executable, "-I", "-S", "-c", child, str(canary)],
                                     user=65532, group=65532, extra_groups=[], capture_output=True, text=True, timeout=10)
            result[name] = {"exit_code": process.returncode, "stderr": process.stderr,
                            "observation": json.loads(process.stdout) if process.returncode == 0 else None}
    result["seeded_artifacts_removed"] = not Path(directory).exists()
    return result


def volume(args):
    path = Path(args["path"])
    result = {"uid": os.getuid(), "path": str(path)}
    before = path.read_bytes()
    if before != PAYROLL:
        return dict(result, refused="Fixture is not exactly the known synthetic payroll; no write attempted", sha256=digest(before))
    metadata = path.stat()
    result.update(original_sha256=digest(before), file_mode=oct(stat.S_IMODE(metadata.st_mode)),
                  owner_uid=metadata.st_uid, owner_gid=metadata.st_gid,
                  directory_mode=oct(stat.S_IMODE(path.parent.stat().st_mode)))
    if args.get("child_write"):
        child = """import hashlib,json,os,sys
p=sys.argv[1]; marker=sys.argv[2].encode()
with open(p,'rb') as f: original=f.read()
with open(p,'ab') as f: f.write(marker)
with open(p,'rb') as f: changed=f.read()
print(json.dumps({'uid':os.getuid(),'read_sha256':hashlib.sha256(original).hexdigest(),'modified_sha256':hashlib.sha256(changed).hexdigest(),'modified':changed==original+marker}))
"""
        try:
            process = subprocess.run([sys.executable, "-I", "-S", "-c", child, str(path), "PHASE2-SYNTHETIC-" + args["run_id"] + "\n"],
                                     user=65532, group=65532, extra_groups=[], capture_output=True, text=True, timeout=10)
            result["unprivileged_child"] = {"exit_code": process.returncode, "stderr": process.stderr,
                                             "observation": json.loads(process.stdout) if process.returncode == 0 else None}
        finally:
            current = path.read_bytes()
            expected = before + ("PHASE2-SYNTHETIC-" + args["run_id"] + "\n").encode()
            if current not in (before, expected):
                raise RuntimeError("Concurrent fixture modification; refused to overwrite unknown data")
            if current != before:
                path.write_bytes(before)
        result["restored_sha256"] = digest(path.read_bytes())
        result["restored_bytes_and_mode"] = path.read_bytes() == before and stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(metadata.st_mode)
    else:
        result["synthetic_payroll_read"] = True
        marker = path.parent / ("phase2-" + args["run_id"])
        try:
            with marker.open("xb") as stream:
                stream.write(b"SYNTHETIC-VOLUME-WRITE")
            result["new_file_write"] = marker.read_bytes() == b"SYNTHETIC-VOLUME-WRITE"
            result["new_file_uid"] = marker.stat().st_uid
        finally:
            if marker.exists():
                marker.unlink()
        try:
            os.listdir(path.parent.parent / "pgdata")
            result["pgdata_directory_listing_allowed"] = True
        except PermissionError:
            result["pgdata_directory_listing_allowed"] = False
        result["temporary_file_removed"] = not marker.exists()
    return result


def database(args):
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,sku,name,quantity,location FROM inventory ORDER BY id")
            rows = cursor.fetchall()
            cursor.execute("SELECT current_user,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
            role = cursor.fetchone()
            cursor.execute("SAVEPOINT token_read_check")
            try:
                # The token never leaves PostgreSQL: project only its public
                # subject claim, never its raw bytes or signature.
                cursor.execute("""
                    WITH token AS (
                        SELECT split_part(pg_read_file('/var/run/secrets/kubernetes.io/serviceaccount/token'),'.',2) AS payload
                    )
                    SELECT convert_from(decode(translate(payload,'-_','+/') ||
                        repeat('=',(4-length(payload)%4)%4),'base64'),'UTF8')::jsonb->>'sub'
                    FROM token
                """)
                token_read = {"allowed": True, "subject_only": cursor.fetchone()[0]}
            except psycopg.Error as exc:
                cursor.execute("ROLLBACK TO SAVEPOINT token_read_check")
                token_read = {"allowed": False, "sqlstate": exc.sqlstate}
            privileges = {}
            for privilege in ["SELECT", "INSERT", "UPDATE", "DELETE"]:
                cursor.execute("SELECT has_table_privilege(current_user,'inventory',%s)", (privilege,))
                privileges[privilege] = cursor.fetchone()[0]
            cursor.execute("SELECT last_value,is_called FROM inventory_id_seq")
            sequence = cursor.fetchone()
            cursor.execute("CREATE TEMP TABLE phase2_transaction_canary (value text)")
            cursor.execute("INSERT INTO phase2_transaction_canary VALUES ('SYNTHETIC-ONLY')")
            cursor.execute("SELECT value FROM phase2_transaction_canary")
            canary = cursor.fetchone()[0] == "SYNTHETIC-ONLY"
        connection.rollback()
    return {"row_count": len(rows), "rows_sha256": digest(json.dumps(rows, sort_keys=True).encode()),
            "database_user": role[0], "superuser": role[1], "create_database": role[2], "create_role": role[3],
            "inventory_privileges": privileges, "sequence": list(sequence), "temporary_table_write_then_rollback": canary,
            "database_server_serviceaccount_file_read": token_read}


def http_crud(args):
    base = args["url"].rstrip("/")
    item_id = None
    result = {"origin_uid": os.getuid(), "origin_token_present": (SA / "token").is_file()}
    def request(path, method="GET", body=None):
        req = Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except HTTPError as exc:
            return exc.code, None
    try:
        result["list_status"], items = request("/api/items")
        result["readable_records"] = len(items) if isinstance(items, list) else None
        payload = {"sku": "SEC-" + args["run_id"], "name": "Synthetic Phase 2 canary", "quantity": 1, "location": "Disposable test namespace"}
        result["create_status"], created = request("/api/items", "POST", payload)
        if result["create_status"] != 201:
            return result
        item_id = created["id"]
        payload["quantity"] = 2
        result["update_status"], updated = request(f"/api/items/{item_id}", "PUT", payload)
        result["read_status"], read = request(f"/api/items/{item_id}")
        result["updated_value_observed"] = read is not None and read["quantity"] == 2
        result["delete_status"], _ = request(f"/api/items/{item_id}", "DELETE")
        if result["delete_status"] == 204:
            deleted = item_id
            item_id = None
            result["read_after_delete_status"], _ = request(f"/api/items/{deleted}")
    finally:
        if item_id is not None:
            result["cleanup_status"], _ = request(f"/api/items/{item_id}", "DELETE")
    return result


if __name__ == "__main__":
    actions = {"api": api, "http": http, "tcp": tcp, "runtime": runtime,
               "setuid": setuid, "volume": volume, "database": database, "http-crud": http_crud}
    try:
        print(json.dumps(actions[sys.argv[1]](json.loads(sys.argv[2])), sort_keys=True), flush=True)
    except Exception as exc:
        print(json.dumps({"probe_error": {"type": type(exc).__name__, "detail": str(exc)}}), flush=True)
        raise SystemExit(1)
