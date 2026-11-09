"""Functional checks only. Run with a frontend port-forward already active.

--persistence restarts only this lab's PostgreSQL and backend Deployments and
checks that the exact temporary record survives. No security tests run here.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8080"


def api(path, method="GET", body=None, expected=200):
    request = Request(BASE + path, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            status, data = response.status, response.read()
    except HTTPError as exc:
        status, data = exc.code, exc.read()
    assert status == expected, f"{method} {path}: expected {expected}, got {status}: {data!r}"
    return json.loads(data) if data else None


def k(*args):
    return subprocess.check_output(["bash", "scripts/lab.sh", "kubectl", *args], cwd=ROOT, text=True)


def wait_record(item_id, expected):
    for _ in range(60):
        try:
            if api(f"/api/items/{item_id}") == expected:
                return
        except (AssertionError, URLError, TimeoutError):
            pass
        time.sleep(2)
    raise AssertionError("Record did not return unchanged after restart")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persistence", action="store_true")
    args = parser.parse_args()
    item_id = None
    try:
        for path, marker in [("/", "stockroom"), ("/app.js", "fetch("), ("/styles.css", ":root")]:
            with urlopen(BASE + path, timeout=15) as response:
                assert response.status == 200 and marker in response.read().decode()
        print("PASS frontend HTML, JavaScript, and CSS served", flush=True)
        items = api("/api/items")
        assert isinstance(items, list)
        print(f"PASS frontend → backend → PostgreSQL read ({len(items)} records)", flush=True)
        payload = {"sku": "CHECK-" + uuid.uuid4().hex[:12], "name": "Functional check item", "quantity": 7, "location": "Synthetic test shelf"}
        item = api("/api/items", "POST", payload, 201)
        item_id = item["id"]
        assert api(f"/api/items/{item_id}") == item
        assert any(row == item for row in api("/api/items"))
        print("PASS create and read exact stored record", flush=True)
        api("/api/items", "POST", payload, 409)
        invalid = dict(payload, sku="BAD-" + uuid.uuid4().hex[:8], quantity=-1)
        api("/api/items", "POST", invalid, 422)
        api("/api/items", "POST", dict(invalid, quantity=1.5), 422)
        print("PASS duplicate SKU, negative and fractional quantity rejected", flush=True)
        payload["quantity"] = 19
        payload["name"] = "Updated functional check"
        item = api(f"/api/items/{item_id}", "PUT", payload)
        assert api(f"/api/items/{item_id}") == dict(payload, id=item_id)
        print("PASS update persisted and returned", flush=True)
        if args.persistence:
            pvc = json.loads(k("-n", "project5", "get", "pvc", "postgres-data", "-o", "json"))
            assert pvc["status"]["phase"] == "Bound"
            old_pg = json.loads(k("-n", "project5", "get", "pods", "-l", "app=postgres", "-o", "json"))["items"][0]["metadata"]["uid"]
            print(k("-n", "project5", "rollout", "restart", "deployment/postgres"), end="", flush=True)
            print(k("-n", "project5", "rollout", "status", "deployment/postgres", "--timeout=180s"), end="", flush=True)
            new_pg = json.loads(k("-n", "project5", "get", "pods", "-l", "app=postgres", "-o", "json"))["items"][0]["metadata"]["uid"]
            assert old_pg != new_pg, "Database Pod was not recreated"
            wait_record(item_id, item)
            after = json.loads(k("-n", "project5", "get", "pvc", "postgres-data", "-o", "json"))
            assert after["metadata"]["uid"] == pvc["metadata"]["uid"]
            assert after["spec"]["volumeName"] == pvc["spec"]["volumeName"]
            print(f"PASS exact record survived PostgreSQL Pod replacement; same PVC/PV {after['spec']['volumeName']}", flush=True)
            print(k("-n", "project5", "rollout", "restart", "deployment/backend"), end="", flush=True)
            print(k("-n", "project5", "rollout", "status", "deployment/backend", "--timeout=180s"), end="", flush=True)
            wait_record(item_id, item)
            print("PASS exact record survived backend Pod replacement", flush=True)
        api(f"/api/items/{item_id}", "DELETE", expected=204)
        deleted_id = item_id
        item_id = None
        api(f"/api/items/{deleted_id}", expected=404)
        api(f"/api/items/{deleted_id}", "PUT", payload, 404)
        api(f"/api/items/{deleted_id}", "DELETE", expected=404)
        print("PASS delete and missing-record responses", flush=True)
        print("PASS all requested functional checks", flush=True)
    finally:
        if item_id is not None:
            try:
                api(f"/api/items/{item_id}", "DELETE", expected=204)
            except Exception as exc:
                print(f"Cleanup could not remove temporary record {item_id}: {exc}", flush=True)


if __name__ == "__main__":
    main()
