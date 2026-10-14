// Thin API client. Owns the write token (kept in sessionStorage only) and
// attaches it to every request so the server never has to embed it in HTML.

const TOKEN_KEY = "webshare.token";
let token = sessionStorage.getItem(TOKEN_KEY) || "";

export function getToken() {
  return token;
}

export function setToken(value) {
  token = (value || "").trim();
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  } else {
    sessionStorage.removeItem(TOKEN_KEY);
  }
}

export function hasToken() {
  return token.length > 0;
}

function qs(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      search.set(key, value);
    }
  }
  return search.toString();
}

async function request(method, url) {
  const headers = {};
  if (token) {
    headers["X-Webshare-Token"] = token;
  }
  const response = await fetch(url, { method, headers, cache: "no-store" });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!response.ok) {
    const error = new Error((data && data.message) || response.statusText || "Error de red");
    error.status = response.status;
    error.code = data && data.error;
    throw error;
  }
  return data;
}

export const api = {
  status() {
    return request("GET", "/api/status");
  },
  auth() {
    return request("GET", "/api/auth");
  },
  list(path, offset = 0, limit = 2000) {
    return request("GET", `/api/list?${qs({ path, offset, limit })}`);
  },
  mkdir(path, name) {
    return request("POST", `/api/mkdir?${qs({ path, name })}`);
  },
  rename(path, newName) {
    return request("POST", `/api/rename?${qs({ path, new_name: newName })}`);
  },
  remove(path, confirm) {
    return request("DELETE", `/api/delete?${qs({ path, confirm })}`);
  },
};

export function currentPath() {
  const raw = new URLSearchParams(location.search).get("path") || "";
  return raw.replace(/^\/+/, "");
}
