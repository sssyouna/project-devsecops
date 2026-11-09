"""Native measurements, loaded after the unchanged V1 probe in the container.

The operator provides stdin transport only; all operations use the container's
UID, mounts, capabilities, network and SQL credentials. Never output secrets.
"""
v1_runtime = runtime


def native_api(args):
    present = (SA / "token").is_file()
    return {"credential_present": present, "request_attempted": False,
            "denial_layer": "token_not_mounted" if not present else "unexpected_token"}


def native_runtime(args):
    result = v1_runtime(args)
    result["root_mount_read_only"] = bool(os.statvfs("/").f_flag & os.ST_RDONLY)
    with tempfile.TemporaryFile(dir="/tmp") as stream:
        stream.write(b"SYNTHETIC-TEMP"); stream.seek(0)
        result["explicit_tmp_writable"] = stream.read() == b"SYNTHETIC-TEMP"
    return result


def native_database(args):
    import psycopg
    result = {}
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
        rows = conn.execute("SELECT id,sku,name,quantity,location FROM inventory ORDER BY id").fetchall()
        result["rows_sha256"] = digest(json.dumps(rows, sort_keys=True).encode())
        result["row_count"] = len(rows)
        role = conn.execute("SELECT current_user,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
        result["role"] = dict(zip(["name", "superuser", "create_database", "create_role", "replication", "bypass_rls"], role))
        result["crud_privileges"] = {verb: conn.execute("SELECT has_table_privilege(current_user,'public.inventory',%s)", (verb,)).fetchone()[0]
                                     for verb in ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]}
        # Each rejected action has a savepoint; a denial cannot invalidate
        # subsequent probes or grant permissions just to satisfy the old test.
        statements = {
            "projected_token_read": "SELECT pg_read_file('/var/run/secrets/kubernetes.io/serviceaccount/token')",
            "server_file_read": "SELECT pg_read_file('/etc/passwd')",
            "schema_create": "CREATE TABLE public.phase3_denied_canary(id int)",
            "temporary_table_create": "CREATE TEMP TABLE phase3_denied_temp(id int)",
            "sequence_read": "SELECT last_value FROM public.inventory_id_seq",
        }
        result["denied_actions"] = {}
        for name, sql in statements.items():
            conn.execute("SAVEPOINT native_check")
            try:
                conn.execute(sql)
                # Never fetch or log results from a file read, even on failure
                # of the security control. Savepoint rollback removes any DDL.
                observation = {"allowed": True}
            except psycopg.Error as exc:
                observation = {"allowed": False, "sqlstate": exc.sqlstate,
                               "error_class": type(exc).__name__}
            conn.execute("ROLLBACK TO SAVEPOINT native_check")
            result["denied_actions"][name] = observation
        conn.rollback()
    config = psycopg.conninfo.conninfo_to_dict(os.environ["DATABASE_URL"])
    config.update(user="lab_admin", password="SYNTHETIC-INCORRECT-CONTROL", connect_timeout="3")
    try:
        with psycopg.connect(**config):
            result["admin_tcp"] = {"allowed": True}
    except psycopg.Error as exc:
        # Connection exceptions may omit SQLSTATE; require explicit HBA reject
        # text rather than confusing a wrong password with admin isolation.
        result["admin_tcp"] = {"allowed": False, "sqlstate": exc.sqlstate,
                               "hba_reject": "pg_hba.conf rejects connection" in str(exc)}
    return result


def volume_denials(args):
    path = Path(args["path"])
    result = {"uid": os.getuid(), "groups": os.getgroups(), "path": str(path)}
    for action, mode in [("read", "rb"), ("append", "ab")]:
        try:
            with path.open(mode):
                result[action] = {"allowed": True}
        except OSError as exc:
            result[action] = {"allowed": False, "errno": exc.errno, "error": type(exc).__name__}
    return result


def setuid_control(args):
    child = """import json,os
r={'ruid':os.getuid(),'euid':os.geteuid()}
try:
 with open('/opt/phase3-control/canary','rb') as f: r['root_canary_read']=f.read()==b'SYNTHETIC-ROOT-ONLY'
except PermissionError: r['root_canary_read']=False
print(json.dumps(r))
"""
    result = {"parent": process_status("/proc/self/status")}
    for name, executable in [("ordinary", sys.executable), ("seeded_setuid", "/opt/phase3-control/helper")]:
        proc = subprocess.run([executable, "-I", "-S", "-c", child], capture_output=True, text=True, timeout=10)
        result[name] = {"exit_code": proc.returncode, "observation": json.loads(proc.stdout) if not proc.returncode else None,
                        "stderr": proc.stderr}
    return result


def main():
    actions = {"api": native_api, "http": http, "tcp": tcp, "runtime": native_runtime,
               "setuid": setuid, "volume-denials": volume_denials, "database": native_database,
               "setuid-control": setuid_control, "http-crud": http_crud}
    try:
        print(json.dumps(actions[sys.argv[1]](json.loads(sys.argv[2])), sort_keys=True), flush=True)
    except Exception as exc:
        print(json.dumps({"probe_error": {"type": type(exc).__name__, "detail": str(exc)}}), flush=True)
        raise SystemExit(1)
