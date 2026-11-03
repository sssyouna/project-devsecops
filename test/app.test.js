import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { once } from "node:events";
import { createApp } from "../src/server.js";

async function fixture(t, dbPath = ":memory:") {
  const { server, store } = createApp({ dbPath });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  let closed = false;
  const close = async () => {
    if (closed) return;
    closed = true;
    server.close();
    server.closeIdleConnections();
    await once(server, "close");
    store.close();
  };
  t.after(close);
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = async (path, method = "GET", data, extraHeaders = {}) => {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: {
        ...(data === undefined ? {} : { "Content-Type": "application/json" }),
        ...extraHeaders,
      },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    return {
      status: response.status,
      body: await response.json(),
      headers: response.headers,
    };
  };
  const project = async (name = "Atlas API") =>
    (
      await request("/api/projects", "POST", {
        name,
        description: "Test project",
        color: "mint",
      })
    ).body;
  const release = async (
    id,
    status = "live",
    environment = "production",
    version = "v1.0.0",
  ) =>
    request("/api/deployments", "POST", {
      project_id: id,
      version,
      environment,
      status,
    });
  return { request, base, project, release, close };
}

test("fresh workspace is empty and storage health is verified", async (t) => {
  const { request } = await fixture(t);
  assert.deepEqual((await request("/api/health")).body, {
    status: "ok",
    storage: "sqlite",
  });
  const result = await request("/api/dashboard");
  assert.equal(result.status, 200);
  assert.deepEqual(result.body.summary, {
    projects: 0,
    deployments: 0,
    live: 0,
    attention: 0,
  });
  assert.deepEqual(result.body.projects, []);
});

test("project creation, partial editing, activity and cascading deletion", async (t) => {
  const { project, release, request } = await fixture(t);
  const item = await project();
  assert.match(item.id, /^[a-f0-9-]+$/);
  await release(item.id);
  const update = await request(`/api/projects/${item.id}`, "PATCH", {
    name: "Renamed API",
  });
  assert.equal(update.status, 200);
  assert.equal(update.body.name, "Renamed API");
  assert.equal(update.body.description, "Test project");
  assert.equal(
    (await request("/api/dashboard")).body.deployments[0].project_name,
    "Renamed API",
  );
  assert.equal(
    (await request(`/api/projects/${item.id}`, "DELETE")).status,
    200,
  );
  const snapshot = (await request("/api/dashboard")).body;
  assert.equal(snapshot.projects.length, 0);
  assert.equal(snapshot.deployments.length, 0);
  assert.match(snapshot.activity[0].message, /Deleted Renamed API/);
  assert.equal(
    (await request(`/api/projects/${item.id}`, "DELETE")).status,
    404,
  );
});

test("current environment summary uses latest release instead of historical failures", async (t) => {
  const { project, release, request } = await fixture(t);
  const item = await project();
  await release(item.id, "failed");
  await release(item.id, "live", "production", "v1.0.1");
  await release(item.id, "failed", "staging");
  const snapshot = (await request("/api/dashboard")).body;
  assert.deepEqual(snapshot.summary, {
    projects: 1,
    deployments: 3,
    live: 1,
    attention: 1,
  });
  assert.equal(snapshot.projects[0].latest.environment, "staging");
  const update = await request(
    `/api/deployments/${snapshot.deployments[0].id}`,
    "PATCH",
    { status: "live" },
  );
  assert.equal(update.status, 200);
  assert.deepEqual((await request("/api/dashboard")).body.summary, {
    projects: 1,
    deployments: 3,
    live: 2,
    attention: 0,
  });
});

test("project validation rejects bad fields and unsafe repository URLs", async (t) => {
  const { request } = await fixture(t);
  for (const input of [
    { name: "" },
    { name: 1 },
    { name: "x".repeat(81) },
    { name: "API", color: "red" },
    { name: "API", repository: "javascript:alert(1)" },
    { name: "API", repository: "https://user:password@example.com/private" },
  ]) {
    assert.equal((await request("/api/projects", "POST", input)).status, 400);
  }
  const valid = await request("/api/projects", "POST", {
    name: "  App  ",
    repository: "https://github.com/example/app",
  });
  assert.equal(valid.status, 201);
  assert.equal(valid.body.name, "App");
  assert.equal((await request("/api/dashboard")).body.projects.length, 1);
});

test("deployment validation and missing IDs do not create orphan records", async (t) => {
  const { project, request, release } = await fixture(t);
  assert.equal((await release("missing")).status, 404);
  const item = await project();
  for (const overrides of [
    { status: "unknown" },
    { environment: "moon" },
    { version: "" },
    { note: "x".repeat(501) },
  ]) {
    const result = await request("/api/deployments", "POST", {
      project_id: item.id,
      status: "live",
      environment: "production",
      version: "v1",
      ...overrides,
    });
    assert.equal(result.status, 400);
  }
  assert.equal(
    (await request("/api/deployments/missing", "PATCH", { status: "live" }))
      .status,
    404,
  );
  assert.equal(
    (await request("/api/projects/missing", "PATCH", { name: "API" })).status,
    404,
  );
  assert.equal((await request("/api/dashboard")).body.deployments.length, 0);
});

test("data survives closing and reopening the server with the same database", async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "projet-cloud-test-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const dbPath = join(dir, "workspace.sqlite");
  const first = await fixture(t, dbPath);
  const project = await first.project("Persistent project");
  await first.release(project.id);
  await first.close();
  const second = await fixture(t, dbPath);
  const snapshot = (await second.request("/api/dashboard")).body;
  assert.equal(snapshot.projects[0].id, project.id);
  assert.equal(snapshot.deployments.length, 1);
  assert.equal(snapshot.activity.length, 2);
});

test("examples are explicit, populated once, and cannot overwrite a workspace", async (t) => {
  const { request } = await fixture(t);
  const result = await request("/api/demo", "POST", {});
  assert.equal(result.status, 201);
  assert.deepEqual(result.body.summary, {
    projects: 3,
    deployments: 3,
    live: 1,
    attention: 1,
  });
  assert.match(result.body.deployments[0].note, /Example record/);
  assert.equal((await request("/api/demo", "POST", {})).status, 409);
  assert.equal((await request("/api/dashboard")).body.projects.length, 3);
});

test("JSON handling rejects malformed, non-object and oversized bodies", async (t) => {
  const { base } = await fixture(t);
  for (const payload of ["{", "null", "[]"]) {
    const result = await fetch(`${base}/api/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
    });
    assert.equal(result.status, 400);
  }
  const tooLarge = await fetch(`${base}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "x".repeat(20_000) }),
  });
  assert.equal(tooLarge.status, 413);
});

test("cross-origin mutations and non-JSON requests are blocked", async (t) => {
  const { base, request } = await fixture(t);
  assert.equal(
    (
      await request(
        "/api/projects",
        "POST",
        { name: "API" },
        { Origin: "https://untrusted.example" },
      )
    ).status,
    403,
  );
  assert.equal(
    (
      await request(
        "/api/projects",
        "POST",
        { name: "API" },
        { "Sec-Fetch-Site": "cross-site" },
      )
    ).status,
    403,
  );
  const form = await fetch(`${base}/api/projects`, {
    method: "POST",
    body: "name=API",
  });
  assert.equal(form.status, 415);
  assert.equal((await request("/api/dashboard")).body.projects.length, 0);
});

test("static assets load with security headers and unknown paths return 404", async (t) => {
  const { base } = await fixture(t);
  for (const [path, type] of [
    ["/", "text/html"],
    ["/app.js", "text/javascript"],
    ["/styles.css", "text/css"],
    ["/favicon.svg", "image/svg+xml"],
  ]) {
    const result = await fetch(`${base}${path}`);
    assert.equal(result.status, 200);
    assert.ok(result.headers.get("content-type").startsWith(type));
    assert.equal(result.headers.get("x-content-type-options"), "nosniff");
    assert.match(
      result.headers.get("content-security-policy"),
      /script-src 'self'/,
    );
    assert.ok((await result.text()).length > 20);
  }
  for (const path of [
    "/src/store.js",
    "/package.json",
    "/data/cloudboard.sqlite",
    "/api/unknown",
    "/missing",
  ]) {
    assert.equal((await fetch(`${base}${path}`)).status, 404);
  }
  const head = await fetch(base, { method: "HEAD" });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), "");
});

test("SQL-like names are stored as data, without changing the schema", async (t) => {
  const { project, request } = await fixture(t);
  const name = "Robert'); DROP TABLE projects; --";
  await project(name);
  const snapshot = (await request("/api/dashboard")).body;
  assert.equal(snapshot.projects[0].name, name);
  assert.equal(snapshot.summary.projects, 1);
});
