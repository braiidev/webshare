// Light / dark theme handling. The choice is remembered in localStorage and
// otherwise follows the operating system preference.

const STORAGE_KEY = "webshare.theme";
const ICON_LIGHT = "bi bi-moon-stars";
const ICON_DARK = "bi bi-sun";

function current() {
  return document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light";
}

function paint(theme) {
  document.documentElement.setAttribute("data-bs-theme", theme);
  const icon = document.querySelector("#themeToggle i");
  if (icon) {
    icon.className = theme === "dark" ? ICON_DARK : ICON_LIGHT;
  }
}

export function initTheme() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") {
    paint(stored);
    return;
  }
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  paint(prefersDark ? "dark" : "light");
}

export function toggleTheme() {
  const next = current() === "dark" ? "light" : "dark";
  localStorage.setItem(STORAGE_KEY, next);
  paint(next);
}
