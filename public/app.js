const $ = (selector) => document.querySelector(selector);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const labels = {
  overview: "Overview",
  projects: "Projects",
  deployments: "Deployments",
  activity: "Activity",
  about: "About",
};
const state = {
  data: null,
  view: "overview",
  search: "",
  environment: "",
  editor: null,
};
let toastTimer;

function icon(name) {
  const paths = {
    arrow: "M5 19 19 5M5 5h14v14",
    folder: "M3 7h7l2-3h9v15H3Z",
    branch: "M6 3v12a4 4 0 0 0 4 4h8M6 9h8a4 4 0 0 0 4-4V3",
    target:
      "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20",
    search: "m20 20-5-5M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14",
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="${paths[name] || paths.arrow}"/></svg>`;
}

async function api(path, method = "GET", data) {
  const response = await fetch(path, {
    method,
    headers: data === undefined ? {} : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request failed.");
  return result;
}

function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 4500);
}

function date(value, full = false) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    ...(full ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(new Date(value));
}

function badge(status) {
  return `<span class="badge ${escape(status || "none")}"><i></i>${{ live: "Live", building: "Building", failed: "Failed" }[status] || "No deployments"}</span>`;
}

function projectIcon(project) {
  return `<span class="project-icon ${escape(project.color)}">${escape(project.name.slice(0, 2).toUpperCase())}</span>`;
}

function empty(title, description, action = "") {
  return `<div class="empty-state"><span class="empty-icon">${icon("folder")}</span><h3>${title}</h3><p>${description}</p>${action}</div>`;
}

function toolbar(placeholder, withEnvironment = false) {
  return `<div class="toolbar"><label class="search"><span aria-hidden="true">${icon("search")}</span><input id="search" type="search" placeholder="${placeholder}" aria-label="${placeholder}" value="${escape(state.search)}"></label>${withEnvironment ? `<div class="environment-filter"><label class="sr-only" for="environment-filter">Environment</label><select id="environment-filter"><option value="">All environments</option>${["production", "staging", "development"].map((env) => `<option value="${env}" ${state.environment === env ? "selected" : ""}>${env[0].toUpperCase() + env.slice(1)}</option>`).join("")}</select></div>` : '<span class="toolbar-label">A place for everything you build</span>'}</div>`;
}

function projectCards(projects) {
  if (!projects.length) {
    if (state.data.projects.length)
      return empty("No projects found", "Try a different name or description.");
    return empty(
      "Big things start with a small project.",
      "Create your first project and keep every release in one place.",
      `<button class="button primary" data-action="new-project">＋ Create your first project</button><button class="text-button demo-button" data-action="demo">Or explore with example data ${icon("arrow")}</button>`,
    );
  }
  return `<div class="project-grid">${projects.map((p) => `<article class="project-card"><div class="card-top">${projectIcon(p)}<button class="icon-button" data-action="edit-project" data-id="${p.id}" aria-label="Edit ${escape(p.name)}" title="Edit project">${icon("arrow")}</button></div><h3>${escape(p.name)}</h3><p class="project-description">${escape(p.description || "A new idea, ready to take shape.")}</p><div class="card-status">${badge(p.latest?.status)}<span class="environment-name">${escape(p.latest?.environment || "Not released yet")}</span></div><div class="card-bottom"><span>${p.latest ? `<span class="version-icon">${icon("branch")}</span> ${escape(p.latest.version)}` : `Created ${date(p.created_at)}`}</span><button class="text-button" data-action="new-deployment" data-id="${p.id}">Record release <span>＋</span></button></div></article>`).join("")}</div>`;
}

function deploymentTable(deployments, limit) {
  const rows = limit ? deployments.slice(0, limit) : deployments;
  if (!rows.length)
    return empty(
      state.data.deployments.length
        ? "No matching releases"
        : "Your release history starts here.",
      state.data.deployments.length
        ? "Try another search or environment."
        : "Record a deployment to track its version, environment, and outcome.",
      state.data.projects.length && !state.data.deployments.length
        ? '<button class="button secondary" data-action="new-deployment">＋ Record deployment</button>'
        : "",
    );
  return `<div class="table-scroll"><table><thead><tr><th scope="col">PROJECT / VERSION</th><th scope="col">ENVIRONMENT</th><th scope="col">STATUS</th><th scope="col">RECORDED</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${rows.map((d) => `<tr><td><div class="table-project">${projectIcon({ name: d.project_name, color: d.project_color })}<div><strong>${escape(d.project_name)}</strong><small>${escape(d.version)}</small></div></div></td><td><span class="env-tag">${escape(d.environment)}</span></td><td>${badge(d.status)}</td><td class="date-cell">${date(d.created_at, true)}</td><td><button class="icon-button" data-action="edit-deployment" data-id="${d.id}" aria-label="Update ${escape(d.project_name)} ${escape(d.version)} status" title="Update status">${icon("arrow")}</button></td></tr>`).join("")}</tbody></table></div>`;
}

function activityList(limit) {
  const items = limit
    ? state.data.activity.slice(0, limit)
    : state.data.activity;
  if (!items.length)
    return '<div class="quiet-state">A fresh start. Your workspace activity will appear here.</div>';
  return `<ol class="activity-list">${items.map((item) => `<li><span class="activity-dot ${escape(item.kind)}"></span><div><p>${escape(item.message)}</p><time datetime="${item.created_at}">${date(item.created_at, true)}</time></div></li>`).join("")}</ol>`;
}

function renderContent() {
  const { projects, deployments, summary } = state.data;
  const query = state.search.toLowerCase();
  const filteredProjects = projects.filter((p) =>
    `${p.name} ${p.description}`.toLowerCase().includes(query),
  );
  const filteredDeployments = deployments.filter(
    (d) =>
      `${d.project_name} ${d.version} ${d.note}`
        .toLowerCase()
        .includes(query) &&
      (!state.environment || d.environment === state.environment),
  );
  if (state.view === "overview") {
    return `<section class="welcome-card"><div><span class="welcome-kicker"><span></span> YOUR IDEAS, TAKING FLIGHT</span><h2>Build something<br>worth shipping.</h2><p>A clear view of your projects. A home for every release.<br>Keep the momentum going.</p><a class="welcome-link" href="#projects">Explore your projects <span>${icon("arrow")}</span></a></div><div class="cloud-art" aria-hidden="true"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="cloud-tile tile-back">{ }</div><div class="cloud-tile tile-front"><img src="/favicon.svg" alt="" width="88" height="88"></div><span class="art-star star-one">＋</span><span class="art-star star-two">✧</span><span class="art-dot"></span></div></section>
      <section class="stats" aria-label="Workspace statistics">${[
        [
          summary.projects,
          "Total projects",
          "Your ideas in motion",
          icon("folder"),
        ],
        [
          summary.live,
          "Live environments",
          "Latest recorded status",
          icon("arrow"),
        ],
        [
          summary.deployments,
          "Deployments",
          "Every release, in one place",
          icon("branch"),
        ],
        [
          summary.attention,
          "Need attention",
          "Latest failed releases",
          icon("target"),
        ],
      ]
        .map(
          ([value, title, note, icon]) =>
            `<div class="stat"><div class="stat-label">${title}<span>${icon}</span></div><strong>${value.toString().padStart(2, "0")}</strong><small>${note}</small></div>`,
        )
        .join("")}</section>
      <div class="section-heading"><div><h2>Your projects <span class="count-pill">${projects.length}</span></h2><p>Small beginnings. Great possibilities.</p></div><a class="text-button" href="#projects">View all projects ${icon("arrow")}</a></div>${projectCards(projects.slice(0, 3))}
      <div class="lower-grid"><section class="panel"><div class="panel-heading"><h2>Recent deployments</h2><a href="#deployments" class="text-button">View all ${icon("arrow")}</a></div>${deploymentTable(deployments, 4)}</section><section class="panel activity-panel"><div class="panel-heading"><h2>Workspace activity</h2><a href="#activity" class="text-button" aria-label="View all activity">${icon("arrow")}</a></div>${activityList(4)}</section></div>`;
  }
  if (state.view === "projects")
    return `${toolbar("Search projects…")}<div id="search-results">${projectCards(filteredProjects)}</div>`;
  if (state.view === "deployments")
    return `<div class="record-banner"><p>Keep a timeline of what you ship. Records are entered manually.</p><button class="button primary" data-action="new-deployment" ${!projects.length ? "disabled" : ""}>＋ Record deployment</button></div>${toolbar("Search releases…", true)}<section class="panel" id="search-results">${deploymentTable(filteredDeployments)}</section>`;
  if (state.view === "activity")
    return `<section class="panel full-activity"><div class="panel-heading"><h2>The story of your workspace</h2><span class="muted">Latest 40 events</span></div>${activityList()}</section>`;
  return `<section class="panel about-panel"><img src="/favicon.svg" width="64" height="64" alt=""><h2>A little clarity for your cloud.</h2><p>Projet Cloud is a personal workspace for managing projects and recording deployment history. Create a project, record a release, and follow its progress across development, staging, and production.</p><h3>Your data stays here</h3><p>Projects are saved in a SQLite database on the machine running this server. Reloading the page or restarting the app preserves them. Keep a backup of the data directory.</p><h3>A tracker, made for one workspace</h3><p>Deployment statuses are manually recorded; this app does not deploy code, monitor infrastructure, or connect to cloud accounts. It has no authentication and is intended for local use. Add authentication and HTTPS before exposing it publicly.</p><a class="button secondary" href="#overview">Back to overview ${icon("arrow")}</a></section>`;
}

function render() {
  const descriptions = {
    overview: "Your projects and releases, with room to grow.",
    projects: "A home for every idea you’re turning into something.",
    deployments: "From first build to latest release. Keep the whole story.",
    activity: "Every project, every release, every step forward.",
    about: "Simple tools. A little more room to build.",
  };
  $("#breadcrumb").textContent = labels[state.view];
  $("#page-title").innerHTML =
    `${state.view === "overview" ? "Workspace overview" : labels[state.view]}<span>.</span>`;
  $("#page-description").textContent = descriptions[state.view];
  document.title = `${labels[state.view]} · Projet Cloud`;
  document.querySelectorAll("[data-nav]").forEach((el) => {
    const selected = el.dataset.nav === state.view;
    el.classList.toggle("active", selected);
    if (selected) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("#content").setAttribute("aria-busy", "false");
  if (state.data) {
    $("#project-count").textContent = state.data.summary.projects;
    $("#content").innerHTML = renderContent();
  }
}

async function refresh() {
  try {
    state.data = await api("/api/dashboard");
    $("#connection-error").hidden = true;
    render();
  } catch (error) {
    $("#connection-error").innerHTML =
      `${escape(error.message)} <button class="text-button" data-action="retry">Try again</button>`;
    $("#connection-error").hidden = false;
    $("#content").setAttribute("aria-busy", "false");
    if (!state.data)
      $("#content").innerHTML = empty(
        "Couldn’t load your workspace.",
        "Check that the server is running, then try again.",
      );
  }
}

function field(name, label, value = "", options = {}) {
  const common = `id="field-${name}" name="${name}" ${options.required ? "required" : ""}`;
  let control;
  if (options.choices)
    control = `<select ${common}>${options.choices.map(([key, title]) => `<option value="${escape(key)}" ${value === key ? "selected" : ""}>${escape(title)}</option>`).join("")}</select>`;
  else if (options.area)
    control = `<textarea ${common} rows="3" maxlength="${options.max || 300}" placeholder="${options.placeholder || ""}">${escape(value)}</textarea>`;
  else
    control = `<input ${common} type="${options.type || "text"}" maxlength="${options.max || 80}" value="${escape(value)}" placeholder="${options.placeholder || ""}">`;
  return `<div class="field"><label for="field-${name}">${label}${options.required ? ' <span class="required" aria-hidden="true">*</span>' : ""}</label>${control}</div>`;
}

function openEditor(kind, id) {
  if (!state.data) return;
  const project = state.data.projects.find((p) => p.id === id);
  const deployment = state.data.deployments.find((d) => d.id === id);
  state.editor = { kind, id };
  $("#form-error").hidden = true;
  $("#save-button").disabled = false;
  if (kind === "project") {
    $("#dialog-title").textContent = project ? "Edit project" : "New project";
    $("#dialog-eyebrow").textContent = "YOUR NEXT BIG IDEA";
    $("#save-button").textContent = project ? "Save changes" : "Create project";
    $("#form-fields").innerHTML =
      field("name", "Project name", project?.name, {
        required: true,
        placeholder: "e.g. My next big idea",
      }) +
      field("description", "Description", project?.description, {
        area: true,
        placeholder: "What are you building?",
      }) +
      field("repository", "Repository URL (optional)", project?.repository, {
        type: "url",
        max: 300,
        placeholder: "https://github.com/you/project",
      }) +
      field("color", "Project color", project?.color || "mint", {
        choices: [
          ["mint", "Mint"],
          ["blue", "Blue"],
          ["violet", "Violet"],
          ["amber", "Amber"],
        ],
      }) +
      (project
        ? `<div class="project-danger">${project.repository ? `<a href="${escape(project.repository)}" target="_blank" rel="noopener noreferrer" class="text-button">Open repository ${icon("arrow")}</a>` : "<span></span>"}<button type="button" class="text-button danger" data-action="delete-project" data-id="${project.id}">Delete project</button></div>`
        : "");
  } else if (kind === "deployment") {
    if (!state.data.projects.length)
      return toast("Create a project before recording a deployment.");
    $("#dialog-title").textContent = "Record deployment";
    $("#dialog-eyebrow").textContent = "ANOTHER STEP FORWARD";
    $("#save-button").textContent = "Record deployment";
    $("#form-fields").innerHTML =
      '<p class="dialog-description">Add a release to your history. This records a deployment; it does not run one.</p>' +
      field("project_id", "Project", id || state.data.projects[0].id, {
        required: true,
        choices: state.data.projects.map((p) => [p.id, p.name]),
      }) +
      field("version", "Version or commit", "", {
        required: true,
        placeholder: "e.g. v1.0.0 or a1b2c3d",
      }) +
      field("environment", "Environment", "production", {
        choices: [
          ["production", "Production"],
          ["staging", "Staging"],
          ["development", "Development"],
        ],
      }) +
      field("status", "Status", "live", {
        choices: [
          ["live", "Live"],
          ["building", "Building"],
          ["failed", "Failed"],
        ],
      }) +
      field("note", "Release note (optional)", "", {
        area: true,
        max: 500,
        placeholder: "What changed in this release?",
      });
  } else if (kind === "status" && deployment) {
    $("#dialog-title").textContent = "Update release status";
    $("#dialog-eyebrow").textContent =
      `${deployment.project_name} · ${deployment.version}`;
    $("#save-button").textContent = "Save status";
    $("#form-fields").innerHTML =
      `<p class="dialog-description">${escape(deployment.environment)} · Recorded ${date(deployment.created_at, true)}</p>${deployment.note ? `<p class="release-note">${escape(deployment.note)}</p>` : ""}` +
      field("status", "Status", deployment.status, {
        choices: [
          ["live", "Live"],
          ["building", "Building"],
          ["failed", "Failed"],
        ],
      });
  }
  $("#editor").showModal();
}

$("#editor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#save-button");
  button.disabled = true;
  $("#form-error").hidden = true;
  const { kind, id } = state.editor;
  const data = Object.fromEntries(new FormData(event.target));
  try {
    if (kind === "project")
      await api(
        `/api/projects${id ? `/${id}` : ""}`,
        id ? "PATCH" : "POST",
        data,
      );
    else if (kind === "deployment") await api("/api/deployments", "POST", data);
    else await api(`/api/deployments/${id}`, "PATCH", data);
    $("#editor").close();
    toast(
      kind === "project"
        ? id
          ? "Project updated."
          : "Your new project is ready."
        : "Deployment history updated.",
    );
    await refresh();
  } catch (error) {
    $("#form-error").textContent = error.message;
    $("#form-error").hidden = false;
  } finally {
    button.disabled = false;
  }
});

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const { action, id } = target.dataset;
  if (action === "new-project") openEditor("project");
  if (action === "edit-project") openEditor("project", id);
  if (action === "new-deployment") openEditor("deployment", id);
  if (action === "edit-deployment") openEditor("status", id);
  if (action === "close-dialog") $("#editor").close();
  if (action === "retry") await refresh();
  if (action === "demo") {
    target.disabled = true;
    try {
      await api("/api/demo", "POST", {});
      await refresh();
      toast("Example projects added. These are demonstration records.");
    } catch (error) {
      toast(error.message);
    } finally {
      target.disabled = false;
    }
  }
  if (action === "delete-project") {
    const project = state.data.projects.find((p) => p.id === id);
    if (
      !confirm(
        `Delete “${project.name}” and all its deployment history? This cannot be undone.`,
      )
    )
      return;
    target.disabled = true;
    try {
      await api(`/api/projects/${id}`, "DELETE");
      $("#editor").close();
      await refresh();
      toast("Project deleted.");
    } catch (error) {
      $("#form-error").textContent = error.message;
      $("#form-error").hidden = false;
    } finally {
      target.disabled = false;
    }
  }
});

function updateSearchResults() {
  // Update only the results so typing keeps focus and caret position.
  const container = document.createElement("div");
  container.innerHTML = renderContent();
  $("#search-results").innerHTML =
    container.querySelector("#search-results").innerHTML;
}
document.addEventListener("input", (event) => {
  if (event.target.id === "search") {
    state.search = event.target.value;
    updateSearchResults();
  }
});
document.addEventListener("change", (event) => {
  if (event.target.id === "environment-filter") {
    state.environment = event.target.value;
    updateSearchResults();
  }
});
function navigate() {
  const requested = location.hash.slice(1) || "overview";
  state.view = Object.hasOwn(labels, requested) ? requested : "overview";
  state.search = "";
  state.environment = "";
  render();
}
window.addEventListener("hashchange", navigate);
navigate();
refresh();
