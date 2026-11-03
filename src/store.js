import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";

export const environments = ["production", "staging", "development"];
export const statuses = ["live", "building", "failed"];

export class InputError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

function text(value, label, max, required = false) {
  if (typeof value !== "string") throw new InputError(`${label} must be text.`);
  const result = value.trim();
  if (required && !result) throw new InputError(`${label} is required.`);
  if (result.length > max)
    throw new InputError(`${label} must be ${max} characters or fewer.`);
  return result;
}

function choice(value, values, label) {
  if (!values.includes(value)) throw new InputError(`Choose a valid ${label}.`);
  return value;
}

export function openStore(filename) {
  if (filename !== ":memory:")
    mkdirSync(dirname(filename), { recursive: true });
  const db = new DatabaseSync(filename);
  db.exec(`
    PRAGMA foreign_keys = ON;
    PRAGMA journal_mode = WAL;
    PRAGMA busy_timeout = 5000;
    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
      repository TEXT NOT NULL, color TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS deployments (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      environment TEXT NOT NULL CHECK(environment IN ('production', 'staging', 'development')),
      version TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('live', 'building', 'failed')),
      note TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS deployment_project ON deployments(project_id, created_at DESC);
    CREATE TABLE IF NOT EXISTS activity (
      id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL, kind TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    PRAGMA user_version = 1;
  `);
  const now = () => new Date().toISOString();
  const event = (message, kind) =>
    db
      .prepare(
        "INSERT INTO activity(message, kind, created_at) VALUES (?, ?, ?)",
      )
      .run(message, kind, now());
  const transaction = (fn) => {
    db.exec("BEGIN IMMEDIATE");
    try {
      const result = fn();
      db.exec("COMMIT");
      return result;
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  };
  const getProject = (id) => {
    const project = db.prepare("SELECT * FROM projects WHERE id = ?").get(id);
    if (!project) throw new InputError("Project not found.", 404);
    return project;
  };
  const projectValues = (input) => {
    const name = text(input.name, "Project name", 80, true);
    const description = text(input.description ?? "", "Description", 300);
    const repository = text(input.repository ?? "", "Repository URL", 300);
    if (repository) {
      let url;
      try {
        url = new URL(repository);
      } catch {
        throw new InputError("Enter a valid HTTPS repository URL.");
      }
      if (url.protocol !== "https:" || url.username || url.password)
        throw new InputError(
          "Use an HTTPS repository URL without credentials.",
        );
    }
    const color = choice(
      input.color ?? "mint",
      ["mint", "blue", "violet", "amber"],
      "project color",
    );
    return { name, description, repository, color };
  };
  const createProject = (input) =>
    transaction(() => {
      const values = projectValues(input);
      const id = randomUUID();
      db.prepare("INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)").run(
        id,
        values.name,
        values.description,
        values.repository,
        values.color,
        now(),
      );
      event(`Created ${values.name}`, "project");
      return getProject(id);
    });
  const createDeployment = (input) =>
    transaction(() => {
      const project = getProject(text(input.project_id, "Project", 80, true));
      const environment = choice(
        input.environment,
        environments,
        "environment",
      );
      const status = choice(input.status, statuses, "status");
      const version = text(input.version, "Version", 80, true);
      const note = text(input.note ?? "", "Note", 500);
      const id = randomUUID();
      const date = now();
      db.prepare("INSERT INTO deployments VALUES (?, ?, ?, ?, ?, ?, ?, ?)").run(
        id,
        project.id,
        environment,
        version,
        status,
        note,
        date,
        date,
      );
      event(
        `Recorded ${project.name} ${version} · ${environment} · ${status}`,
        status,
      );
      return db.prepare("SELECT * FROM deployments WHERE id = ?").get(id);
    });
  return {
    close: () => db.close(),
    snapshot() {
      const projects = db
        .prepare("SELECT * FROM projects ORDER BY created_at DESC, rowid DESC")
        .all();
      const deployments = db
        .prepare(
          `SELECT d.*, p.name AS project_name, p.color AS project_color
        FROM deployments d JOIN projects p ON p.id = d.project_id ORDER BY d.created_at DESC, d.rowid DESC`,
        )
        .all();
      for (const project of projects)
        project.latest =
          deployments.find((d) => d.project_id === project.id) ?? null;
      // Only the latest record for each project/environment represents its current state.
      const current = new Map();
      for (const deployment of deployments) {
        const key = `${deployment.project_id}:${deployment.environment}`;
        if (!current.has(key)) current.set(key, deployment);
      }
      const live = [...current.values()].filter(
        (d) => d.status === "live",
      ).length;
      return {
        projects,
        deployments,
        activity: db
          .prepare("SELECT * FROM activity ORDER BY id DESC LIMIT 40")
          .all(),
        summary: {
          projects: projects.length,
          deployments: deployments.length,
          live,
          attention: [...current.values()].filter((d) => d.status === "failed")
            .length,
        },
      };
    },
    createProject,
    updateProject(id, input) {
      return transaction(() => {
        const project = getProject(id);
        const values = projectValues({ ...project, ...input });
        db.prepare(
          "UPDATE projects SET name = ?, description = ?, repository = ?, color = ? WHERE id = ?",
        ).run(
          values.name,
          values.description,
          values.repository,
          values.color,
          id,
        );
        event(`Updated ${values.name}`, "project");
        return getProject(id);
      });
    },
    deleteProject(id) {
      transaction(() => {
        const project = getProject(id);
        db.prepare("DELETE FROM projects WHERE id = ?").run(id);
        event(`Deleted ${project.name} and its deployment history`, "project");
      });
    },
    createDeployment,
    updateDeployment(id, input) {
      return transaction(() => {
        const deployment = db
          .prepare("SELECT * FROM deployments WHERE id = ?")
          .get(id);
        if (!deployment) throw new InputError("Deployment not found.", 404);
        const status = choice(input.status, statuses, "status");
        db.prepare(
          "UPDATE deployments SET status = ?, updated_at = ? WHERE id = ?",
        ).run(status, now(), id);
        event(
          `Marked ${getProject(deployment.project_id).name} ${deployment.version} as ${status}`,
          status,
        );
        return db.prepare("SELECT * FROM deployments WHERE id = ?").get(id);
      });
    },
    seedDemo() {
      if (db.prepare("SELECT COUNT(*) AS count FROM projects").get().count)
        throw new InputError(
          "Example data can only be added to an empty workspace.",
          409,
        );
      // Each public mutation owns its transaction; no nested SQLite transactions.
      const api = createProject({
        name: "Atlas API",
        description: "The backend that connects everything.",
        repository: "",
        color: "mint",
      });
      const web = createProject({
        name: "Orbit storefront",
        description: "A fast, thoughtful shopping experience.",
        repository: "",
        color: "blue",
      });
      const docs = createProject({
        name: "Developer docs",
        description: "A home for guides, references, and ideas.",
        repository: "",
        color: "violet",
      });
      createDeployment({
        project_id: api.id,
        version: "v1.4.0",
        environment: "production",
        status: "live",
        note: "Example record — no actual deployment was performed.",
      });
      createDeployment({
        project_id: web.id,
        version: "v2.1.0",
        environment: "staging",
        status: "building",
        note: "Example record — manually update the status to try the workflow.",
      });
      createDeployment({
        project_id: docs.id,
        version: "v1.0.0",
        environment: "production",
        status: "failed",
        note: "Example record — investigate and record a new version.",
      });
    },
  };
}
