// UI layer: renders the file list in the browser (the server only serves the
// shell), wires the CRUD dialogs and handles uploads with progress.

import { api, getToken, setToken, hasToken, currentPath } from "./api.js";
import { initTheme, toggleTheme } from "./theme.js";

const state = {
  path: "",
  entries: [],
  files: [],
  status: null,
  startedAt: null,
  uptimeTimer: null,
  renameTarget: null,
  deleteTarget: null,
};

const el = (id) => document.getElementById(id);

function icon(name, extra = "") {
  const node = document.createElement("i");
  node.className = `bi bi-${name}${extra ? " " + extra : ""}`;
  node.setAttribute("aria-hidden", "true");
  return node;
}

function formatSize(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = Number(bytes);
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return i === 0 ? `${value} ${units[i]}` : `${value.toFixed(1)} ${units[i]}`;
}

function formatDate(seconds) {
  if (!seconds) return "";
  try {
    return new Date(seconds * 1000).toLocaleString(undefined, {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function entryPath(entry) {
  return state.path ? `${state.path}/${entry.name}` : entry.name;
}

function folderQuery(path) {
  return path ? `/?path=${encodeURIComponent(path)}` : "/";
}

function navigate(path) {
  window.location.href = folderQuery(path);
}

function crudActive() {
  return Boolean(state.status && state.status.crud_enabled) && hasToken();
}

function crudOffered() {
  return Boolean(state.status && state.status.crud_enabled);
}

function uploadEnabled() {
  if (!state.status) return true;
  if (state.status.upload_enabled === undefined) return !state.status.read_only;
  return Boolean(state.status.upload_enabled);
}

/* ---------- alerts ---------- */

function clearAlerts() {
  el("alertArea").replaceChildren();
}

function showAlert(kind, message, timeout = 5000) {
  const wrap = document.createElement("div");
  wrap.className = `alert alert-${kind} alert-dismissible py-2 small d-flex align-items-center gap-2`;
  wrap.setAttribute("role", "alert");
  wrap.append(icon(kind === "danger" ? "exclamation-triangle" : kind === "success" ? "check-circle" : "info-circle"));
  const text = document.createElement("span");
  text.className = "flex-grow-1";
  text.textContent = message;
  wrap.append(text);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn-close btn-close-sm";
  close.setAttribute("aria-label", "Cerrar");
  close.addEventListener("click", () => wrap.remove());
  wrap.append(close);
  el("alertArea").replaceChildren(wrap);
  if (timeout) {
    window.setTimeout(() => wrap.remove(), timeout);
  }
}

function handleError(error, fallback = "Ocurrió un error.") {
  if (error && error.status === 403 && error.code === "token_invalid") {
    setToken("");
    updateAuthUI();
    showAlert("warning", "La clave no es válida. Volvé a ingresarla.");
    openTokenDialog();
    return;
  }
  showAlert("danger", (error && error.message) || fallback);
}

/* ---------- auth / status ---------- */

function updateAuthUI() {
  const status = state.status || {};
  const readOnly = Boolean(status.read_only);
  const tokenButton = el("tokenButton");
  tokenButton.hidden = !status.token_required || readOnly;
  tokenButton.title = hasToken() ? "Clave guardada en esta pestaña" : "Ingresar clave de escritura";

  const active = crudActive();
  el("newFolderButton").disabled = !active;

  const dropzone = el("dropzone");
  const uploadOk = uploadEnabled();
  dropzone.classList.toggle("is-disabled", !uploadOk);
  el("fileInput").disabled = !uploadOk;
  if (readOnly) {
    el("uploadHint").textContent = "El servidor está en modo solo lectura: las subidas están deshabilitadas.";
    el("uploadHint").className = "alert alert-warning small mb-3";
  } else if (!uploadOk) {
    el("uploadHint").textContent =
      "Las subidas están deshabilitadas: el servidor necesita token o --allow-anonymous-upload.";
    el("uploadHint").className = "alert alert-warning small mb-3";
  }

  renderEntries();
}

async function loadStatus() {
  try {
    state.status = await api.status();
  } catch {
    state.status = null;
  }
  const version = state.status && state.status.version;
  el("versionLabel").textContent = version ? `v${version}` : "";
  if (state.status && state.status.uptime_seconds !== undefined) {
    state.startedAt = Date.now() - state.status.uptime_seconds * 1000;
    el("uptimeBadge").hidden = false;
    paintUptime();
    startUptimeTicker();
  }
  updateAuthUI();
}

function paintUptime() {
  if (state.startedAt === null) return;
  const seconds = (Date.now() - state.startedAt) / 1000;
  el("uptimeBadge").textContent = `Activo ${formatUptime(seconds)}`;
}

function startUptimeTicker() {
  if (state.uptimeTimer !== null) return;
  state.uptimeTimer = window.setInterval(paintUptime, 1000);
}

function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${total}s`;
}

/* ---------- rendering ---------- */

function rootLabel() {
  const name = state.status && state.status.root_name;
  if (!name) return "Inicio";
  return name;
}

function renderBreadcrumb() {
  const list = el("breadcrumb");
  list.replaceChildren();

  const root = document.createElement("li");
  root.className = "breadcrumb-item";
  if (!state.path) {
    root.classList.add("active");
    root.setAttribute("aria-current", "page");
    root.append(icon("house-door", "me-1"));
    root.append(document.createTextNode(rootLabel()));
  } else {
    const link = document.createElement("a");
    link.href = folderQuery("");
    link.append(icon("house-door", "me-1"));
    link.append(document.createTextNode(rootLabel()));
    root.append(link);
  }
  list.append(root);

  const parts = state.path ? state.path.split("/") : [];
  parts.forEach((part, index) => {
    const item = document.createElement("li");
    item.className = "breadcrumb-item";
    const isLast = index === parts.length - 1;
    if (isLast) {
      item.classList.add("active");
      item.setAttribute("aria-current", "page");
      item.textContent = part;
    } else {
      const link = document.createElement("a");
      link.href = folderQuery(parts.slice(0, index + 1).join("/"));
      link.textContent = part;
      item.append(link);
    }
    list.append(item);
  });
}

function matchesFilter(entry) {
  if (!state.filter) return true;
  return entry.name.toLowerCase().includes(state.filter);
}

function renderEntries() {
  const list = el("entryList");
  list.replaceChildren();

  const visible = state.entries.filter(matchesFilter);
  const empty = el("emptyState");
  const hasEntries = state.entries.length > 0;

  if (!hasEntries) {
    empty.hidden = false;
    empty.querySelector("span").textContent = "No hay archivos en esta carpeta.";
  } else if (visible.length === 0) {
    empty.hidden = false;
    empty.querySelector("span").textContent = "Ningún archivo coincide con el filtro.";
  } else {
    empty.hidden = true;
  }

  const offered = crudOffered();
  const active = crudActive();

  for (const entry of visible) {
    list.append(renderEntry(entry, offered, active));
  }
}

function renderEntry(entry, offered, active) {
  const row = document.createElement("div");
  row.className = "list-group-item";
  row.setAttribute("role", "listitem");

  const isFolder = entry.type === "directory";
  row.append(icon(isFolder ? "folder2" : "file-earmark", `entry-icon ${isFolder ? "is-folder" : "is-file"}`));

  const link = document.createElement("a");
  link.className = "entry-name";
  link.textContent = entry.name;
  link.title = entry.name;
  if (isFolder) {
    link.href = folderQuery(entryPath(entry));
  } else {
    link.href = `/api/download?path=${encodeURIComponent(entryPath(entry))}`;
    link.setAttribute("download", entry.name);
  }
  row.append(link);

  const meta = document.createElement("span");
  meta.className = "entry-meta";
  if (isFolder) {
    meta.textContent = "Carpeta";
  } else {
    meta.textContent = `${formatSize(entry.size)} · ${formatDate(entry.modified)}`;
  }
  row.append(meta);

  if (offered) {
    const actions = document.createElement("span");
    actions.className = "entry-actions";

    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "btn btn-outline-secondary";
    rename.title = "Renombrar";
    rename.setAttribute("aria-label", `Renombrar ${entry.name}`);
    rename.disabled = !active;
    rename.append(icon("pencil-square"));
    rename.addEventListener("click", () => openRenameDialog(entry));

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-outline-danger";
    remove.title = "Borrar";
    remove.setAttribute("aria-label", `Borrar ${entry.name}`);
    remove.disabled = !active;
    remove.append(icon("trash3"));
    remove.addEventListener("click", () => openDeleteDialog(entry));

    actions.append(rename, remove);
    row.append(actions);
  }

  return row;
}

async function loadList() {
  try {
    const data = await api.list(state.path);
    state.entries = data.entries || [];
    renderEntries();
  } catch (error) {
    state.entries = [];
    renderEntries();
    handleError(error, "No se pudo leer la carpeta.");
  }
}

/* ---------- dialogs ---------- */

function openDialog(dialog) {
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
  } else {
    dialog.setAttribute("open", "");
  }
}

function closeDialog(dialog) {
  if (typeof dialog.close === "function") {
    dialog.close();
  } else {
    dialog.removeAttribute("open");
  }
}

function wireDialogClose() {
  document.querySelectorAll("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (dialog) closeDialog(dialog);
    });
  });
}

function openTokenDialog() {
  const dialog = el("tokenDialog");
  el("tokenInput").value = getToken();
  el("tokenError").hidden = true;
  openDialog(dialog);
  el("tokenInput").focus();
}

function openFolderDialog() {
  const dialog = el("folderDialog");
  el("folderName").value = "";
  el("folderError").hidden = true;
  openDialog(dialog);
  el("folderName").focus();
}

function openRenameDialog(entry) {
  state.renameTarget = entry;
  const dialog = el("renameDialog");
  el("renameName").value = entry.name;
  el("renameError").hidden = true;
  openDialog(dialog);
  el("renameName").focus();
  el("renameName").select();
}

function openDeleteDialog(entry) {
  state.deleteTarget = entry;
  const dialog = el("deleteDialog");
  el("deleteTargetName").textContent = entry.name;
  el("deleteConfirm").value = "";
  el("deleteError").hidden = true;
  openDialog(dialog);
  el("deleteConfirm").focus();
}

function setFieldError(id, message) {
  const node = el(id);
  node.textContent = message || "";
  node.hidden = !message;
}

/* ---------- actions ---------- */

async function submitToken(event) {
  event.preventDefault();
  const value = el("tokenInput").value.trim();
  const previous = getToken();

  if (!value) {
    setToken("");
    updateAuthUI();
    closeDialog(el("tokenDialog"));
    showAlert("secondary", "Clave eliminada de esta pestaña.", 3000);
    return;
  }

  setToken(value);
  try {
    await api.auth();
    closeDialog(el("tokenDialog"));
    updateAuthUI();
    showAlert("success", "Clave aceptada. Ya podés editar.", 3000);
  } catch (error) {
    setToken(previous);
    updateAuthUI();
    el("tokenError").textContent =
      error && error.code === "token_invalid" ? "La clave no es válida." : "No se pudo verificar la clave.";
    el("tokenError").hidden = false;
  }
}

async function submitFolder(event) {
  event.preventDefault();
  const name = el("folderName").value.trim();
  if (!name) {
    setFieldError("folderError", "Ingresá un nombre.");
    return;
  }
  try {
    await api.mkdir(state.path, name);
    closeDialog(el("folderDialog"));
    showAlert("success", `Carpeta "${name}" creada.`, 3000);
    await loadList();
  } catch (error) {
    setFieldError("folderError", (error && error.message) || "No se pudo crear la carpeta.");
    handleError(error);
  }
}

async function submitRename(event) {
  event.preventDefault();
  const entry = state.renameTarget;
  if (!entry) return;
  const newName = el("renameName").value.trim();
  if (!newName) {
    setFieldError("renameError", "Ingresá un nombre.");
    return;
  }
  if (newName === entry.name) {
    closeDialog(el("renameDialog"));
    return;
  }
  try {
    await api.rename(entryPath(entry), newName);
    closeDialog(el("renameDialog"));
    showAlert("success", `"${entry.name}" ahora es "${newName}".`, 3000);
    await loadList();
  } catch (error) {
    setFieldError("renameError", (error && error.message) || "No se pudo renombrar.");
    handleError(error);
  }
}

async function submitDelete(event) {
  event.preventDefault();
  const entry = state.deleteTarget;
  if (!entry) return;
  const confirm = el("deleteConfirm").value.trim();
  if (!confirm) {
    setFieldError("deleteError", "Escribí el nombre para confirmar.");
    return;
  }
  try {
    await api.remove(entryPath(entry), confirm);
    closeDialog(el("deleteDialog"));
    showAlert("success", `"${entry.name}" fue borrado.`, 3000);
    await loadList();
  } catch (error) {
    setFieldError("deleteError", (error && error.message) || "No se pudo borrar.");
    handleError(error);
  }
}

/* ---------- upload ---------- */

function setFiles(files) {
  state.files = Array.from(files);
  const count = state.files.length;
  el("selectedLabel").textContent = count
    ? `${count} archivo${count === 1 ? "" : "s"} seleccionado${count === 1 ? "" : "s"}`
    : "Ningún archivo seleccionado";
  el("submitButton").disabled = count === 0 || !uploadEnabled();
}

function setProgress(fraction) {
  const wrap = el("progressWrap");
  const bar = el("progressBar");
  if (fraction === null) {
    wrap.hidden = true;
    bar.style.width = "0%";
    bar.textContent = "";
    return;
  }
  wrap.hidden = false;
  const percent = Math.round(fraction * 100);
  bar.style.width = `${percent}%`;
  bar.textContent = `${percent}%`;
}

function uploadFiles() {
  if (!state.files.length) return;
  const submit = el("submitButton");
  submit.disabled = true;

  const form = new FormData();
  form.append("current_path", state.path);
  for (const file of state.files) {
    form.append("file", file);
  }

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/upload");
  if (getToken()) {
    xhr.setRequestHeader("X-Webshare-Token", getToken());
  }

  xhr.upload.addEventListener("progress", (event) => {
    if (event.lengthComputable) {
      setProgress(event.loaded / event.total);
    }
  });

  xhr.addEventListener("load", async () => {
    setProgress(null);
    if (xhr.status >= 200 && xhr.status < 400) {
      const count = state.files.length;
      state.files = [];
      setFiles([]);
      el("fileInput").value = "";
      showAlert("success", `${count} archivo${count === 1 ? "" : "s"} subido${count === 1 ? "" : "s"}.`, 4000);
      await loadList();
    } else {
      submit.disabled = false;
      showAlert("danger", "No se pudo subir. Verificá la clave o el tamaño.");
    }
  });

  xhr.addEventListener("error", () => {
    setProgress(null);
    submit.disabled = false;
    showAlert("danger", "Error de red durante la subida.");
  });

  setProgress(0);
  xhr.send(form);
}

/* ---------- wire up ---------- */

function wireEvents() {
  el("themeToggle").addEventListener("click", toggleTheme);
  el("tokenButton").addEventListener("click", openTokenDialog);
  el("newFolderButton").addEventListener("click", openFolderDialog);
  el("tokenForm").addEventListener("submit", submitToken);
  el("folderForm").addEventListener("submit", submitFolder);
  el("renameForm").addEventListener("submit", submitRename);
  el("deleteForm").addEventListener("submit", submitDelete);

  el("filterInput").addEventListener("input", (event) => {
    state.filter = event.target.value.trim().toLowerCase();
    renderEntries();
  });

  el("fileInput").addEventListener("change", (event) => setFiles(event.target.files));
  el("submitButton").addEventListener("click", uploadFiles);

  const dropzone = el("dropzone");
  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-dragover"));
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragover");
    if (event.dataTransfer && event.dataTransfer.files.length) {
      setFiles(event.dataTransfer.files);
    }
  });

  wireDialogClose();
}

export async function initApp() {
  initTheme();
  state.path = currentPath();
  wireEvents();
  renderBreadcrumb();
  setFiles([]);
  await loadStatus();
  renderBreadcrumb();
  await loadList();
}
