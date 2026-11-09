"use strict";
const form = document.querySelector("#item-form");
const status = document.querySelector("#status");
let editingId = null;
let records = [];

function message(text, error = false) {
  status.textContent = text;
  status.classList.toggle("error", error);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { /* proxy error */ }
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status}). Check the API and input fields.`);
  }
  return response.status === 204 ? null : response.json();
}

function cancelEdit() {
  editingId = null;
  form.reset();
  document.querySelector("#form-title").textContent = "Add an item";
  document.querySelector("#save").textContent = "Add item";
  document.querySelector("#cancel").hidden = true;
}

async function load() {
  records = await request("/api/items");
  document.querySelector("#total").textContent = records.reduce((sum, item) => sum + item.quantity, 0).toLocaleString();
  const body = document.querySelector("#records");
  body.replaceChildren();
  for (const item of records) {
    const row = document.createElement("tr");
    for (const key of ["sku", "name", "location", "quantity"]) {
      const cell = document.createElement("td");
      cell.textContent = item[key];
      row.append(cell);
    }
    const actions = document.createElement("td");
    for (const action of ["Edit", "Delete"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = action;
      button.setAttribute("aria-label", `${action} ${item.sku}`);
      button.addEventListener("click", async () => {
        if (action === "Edit") {
          editingId = item.id;
          for (const key of ["sku", "name", "location", "quantity"]) form.elements[key].value = item[key];
          document.querySelector("#form-title").textContent = "Edit item";
          document.querySelector("#save").textContent = "Save changes";
          document.querySelector("#cancel").hidden = false;
          form.elements.sku.focus();
          return;
        }
        if (!window.confirm(`Delete fictional record ${item.sku}?`)) return;
        button.disabled = true;
        try {
          await request(`/api/items/${item.id}`, { method: "DELETE" });
          if (editingId === item.id) cancelEdit();
          await load();
          message("Record deleted.");
        } catch (error) { message(error.message, true); }
        finally { button.disabled = false; }
      });
      actions.append(button);
    }
    row.append(actions);
    body.append(row);
  }
  document.querySelector("#empty").hidden = records.length !== 0;
  message(`${records.length} fictional records · Connected to PostgreSQL through the API`);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const save = document.querySelector("#save");
  save.disabled = true;
  const payload = Object.fromEntries(new FormData(form));
  payload.quantity = Number(payload.quantity);
  const updating = editingId !== null;
  try {
    await request(updating ? `/api/items/${editingId}` : "/api/items", {
      method: updating ? "PUT" : "POST", body: JSON.stringify(payload),
    });
    cancelEdit();
    await load();
    message(updating ? "Changes saved." : "Record added.");
  } catch (error) { message(error.message, true); }
  finally { save.disabled = false; }
});
document.querySelector("#cancel").addEventListener("click", cancelEdit);
document.querySelector("#refresh").addEventListener("click", () => load().catch(error => message(error.message, true)));
load().catch(error => message(error.message, true));
