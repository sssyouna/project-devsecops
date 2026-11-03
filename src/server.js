import http from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { openStore, InputError } from "./store.js";

const publicDir = fileURLToPath(new URL("../public/", import.meta.url));
const assets = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
  ["/favicon.svg", ["favicon.svg", "image/svg+xml"]],
]);
const securityHeaders = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "Content-Security-Policy":
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
};

async function body(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 16_384) throw new InputError("Request body is too large.", 413);
    chunks.push(chunk);
  }
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object")
      throw new Error();
    return parsed;
  } catch {
    throw new InputError("Send a valid JSON object.");
  }
}

export function createApp({
  dbPath = resolve(process.env.DATA_DIR || "data", "cloudboard.sqlite"),
} = {}) {
  const store = openStore(dbPath);
  const server = http.createServer(async (req, res) => {
    const json = (status, data) => {
      res.writeHead(status, {
        ...securityHeaders,
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      });
      res.end(JSON.stringify(data));
    };
    try {
      const url = new URL(req.url, "http://localhost");
      const path = url.pathname;
      const method = req.method;
      if (path.startsWith("/api/")) {
        if (["POST", "PATCH", "DELETE"].includes(method)) {
          // Cross-origin pages must not be able to mutate this local workspace.
          const origin = req.headers.origin;
          if (
            req.headers["sec-fetch-site"] === "cross-site" ||
            (origin && new URL(origin).host !== req.headers.host)
          )
            throw new InputError("Cross-origin writes are not allowed.", 403);
          if (
            method !== "DELETE" &&
            req.headers["content-type"]?.split(";")[0].trim() !==
              "application/json"
          )
            throw new InputError("Use Content-Type: application/json.", 415);
        }
        if (path === "/api/health" && method === "GET") {
          store.snapshot();
          return json(200, { status: "ok", storage: "sqlite" });
        }
        if (path === "/api/dashboard" && method === "GET")
          return json(200, store.snapshot());
        if (path === "/api/projects" && method === "POST")
          return json(201, store.createProject(await body(req)));
        const project = path.match(/^\/api\/projects\/([a-zA-Z0-9-]+)$/);
        if (project && method === "PATCH")
          return json(200, store.updateProject(project[1], await body(req)));
        if (project && method === "DELETE") {
          store.deleteProject(project[1]);
          return json(200, { deleted: true });
        }
        if (path === "/api/deployments" && method === "POST")
          return json(201, store.createDeployment(await body(req)));
        const deployment = path.match(/^\/api\/deployments\/([a-zA-Z0-9-]+)$/);
        if (deployment && method === "PATCH")
          return json(
            200,
            store.updateDeployment(deployment[1], await body(req)),
          );
        if (path === "/api/demo" && method === "POST") {
          await body(req);
          store.seedDemo();
          return json(201, store.snapshot());
        }
        return json(404, { error: "API endpoint not found." });
      }
      const asset = assets.get(path);
      if (asset && (method === "GET" || method === "HEAD")) {
        const content = await readFile(resolve(publicDir, asset[0]));
        res.writeHead(200, {
          ...securityHeaders,
          "Content-Type": asset[1],
          "Cache-Control": "no-cache",
        });
        return res.end(method === "HEAD" ? undefined : content);
      }
      json(404, { error: "Page not found." });
    } catch (error) {
      if (error instanceof InputError)
        return json(error.status, { error: error.message });
      console.error("Request failed:", error.message);
      json(500, { error: "Something went wrong. Please try again." });
    }
  });
  server.requestTimeout = 15_000;
  return { server, store };
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const port = Number(process.env.PORT || 3000);
  const host = process.env.HOST || "127.0.0.1";
  const { server, store } = createApp();
  server.on("error", (error) => {
    console.error(`Cannot start server: ${error.message}`);
    store.close();
    process.exitCode = 1;
  });
  server.listen(port, host, () =>
    console.log(`Projet Cloud listening on ${host}:${port}`),
  );
  const shutdown = () => {
    server.close(() => {
      store.close();
      process.exit(0);
    });
    server.closeIdleConnections();
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}
