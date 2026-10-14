#!/usr/bin/env python3
import argparse
import hmac
import json
import os
import re
import socket
import subprocess
import shutil
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_DIR = Path.home() / "Descargas"

__version__ = "0.9.1"
REPO_DIR = Path(__file__).resolve().parent
STATIC_DIR = REPO_DIR / "static"
INSTALL_ROOT = Path.home() / ".local/share/webshare"
BIN_PATH = Path.home() / ".local/bin/webshare"

# Upper bound of request body we are willing to drain when rejecting an
# oversized upload, so the client can read the 413 instead of a reset.
UPLOAD_DRAIN_LIMIT = 8 * 1024 * 1024

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def safe_relative(raw_path, root):
    if raw_path is None:
        return ""
    raw_path = urllib.parse.unquote(raw_path).strip().replace("\\", "/")
    if "\x00" in raw_path:
        return None
    # Never permit absolute roots or explicit reversal segments, even if encoders
    # attempt identity or traversal strings.
    parts = []
    for part in raw_path.split("/"):
        if part in {"", "."}:
            continue
        if part in {".."} or part.startswith("..%2") or part.lower() in {"..%5c"}:
            return None
        # Normalize drive-like and alternate delimiters just in case.
        if any(ch in part for ch in ("\\", ":", "\x00")):
            return None
        parts.append(part)
    if not parts:
        return ""
    normalized = "/".join(parts)
    root_resolved = root.resolve()
    candidate = (root / normalized).resolve()
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return relative.as_posix()


def safe_target(directory, filename):
    filename = os.path.basename(str(filename).replace("\\", "/"))
    if not filename or filename in {".", ".."} or "\x00" in filename or "/" in filename:
        return None
    if any(ord(c) < 32 for c in filename):
        return None
    target = (directory / filename).resolve()
    if target.parent != directory.resolve():
        return None
    if target.resolve().is_relative_to(directory.resolve()) is False:
        return None
    return target


def unique_path(directory, filename):
    target = directory / filename
    if not target.exists():
        return target

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    i = 1
    while (directory / f"{stem}_{i}{suffix}").exists():
        i += 1
    return directory / f"{stem}_{i}{suffix}"


def strict_name(value):
    """Validate a single path segment: no separators, traversal, or control chars."""
    name = str(value or "")
    if not name or name in {".", ".."}:
        return None
    if name != name.strip():
        return None
    if any(ch in name for ch in ("/", "\\", ":", "\x00")):
        return None
    if any(ord(ch) < 32 for ch in name):
        return None
    if len(name.encode("utf-8")) > 255:
        return None
    return name


def lexical_relative(raw_path):
    """Normalize a URL path without resolving symlinks; None when unsafe."""
    if raw_path is None:
        return ""
    raw_path = urllib.parse.unquote(str(raw_path)).strip().replace("\\", "/")
    if "\x00" in raw_path or raw_path.startswith("/"):
        return None
    parts = []
    for part in raw_path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        if any(ch in part for ch in ("\\", ":", "\x00")):
            return None
        parts.append(part)
    return "/".join(parts)


class Handler(BaseHTTPRequestHandler):
    server_version = f"webshare/{__version__}"

    @property
    def share_dir(self):
        return Path(self.server.directory)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(url.query)

        if url.path == "/":
            self.send_index()
        elif url.path.startswith("/static/"):
            self.send_static(url.path)
        elif url.path in ("/download", "/api/download"):
            self.send_file(params)
        elif url.path == "/api/list":
            self.send_list(params)
        elif url.path == "/api/info":
            self.send_info(params)
        elif url.path == "/api/auth":
            self.handle_auth()
        elif url.path == "/api/status":
            self.send_json(200, self.status_payload())
        elif url.path == "/version":
            self.send_version()
        elif url.path in ("/health", "/readyz"):
            payload = self.status_payload()
            if url.path == "/readyz":
                payload["path"] = "readyz"
            body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_json_error(404, "not_found", "No se encontró el recurso solicitado.")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/upload":
            if getattr(self.server, "read_only", False):
                self.send_error(403, "Servidor en modo solo lectura")
                return
            self.receive_files()
            return
        if path in ("/api/mkdir", "/api/rename", "/api/delete"):
            self.handle_crud(path)
            return
        self.send_json_error(404, "not_found", "No se encontró el recurso solicitado.")

    def do_DELETE(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/delete":
            self.handle_crud(path)
            return
        self.send_json_error(404, "not_found", "No se encontró el recurso solicitado.")

    def request_data(self):
        combined = {}
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        ctype = self.headers.get("Content-Type", "")
        if length > 0 and ctype.startswith("application/x-www-form-urlencoded"):
            raw = self.rfile.read(length).decode("utf-8", "replace")
            combined.update(urllib.parse.parse_qs(raw))
        combined.update(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query))
        return combined

    def extract_token(self, data=None):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        header = self.headers.get("X-Webshare-Token", "").strip()
        if header:
            return header
        if data:
            body_token = (data.get("token", [""])[0] or "").strip()
            if body_token:
                return body_token
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return (query.get("token", [""])[0] or "").strip()

    def token_configured(self):
        return bool(getattr(self.server, "write_token", ""))

    def require_crud_token(self, data=None):
        if not self.token_configured():
            self.send_json_error(
                403, "crud_disabled", "CRUD deshabilitado: iniciá el servidor con --token."
            )
            return False
        supplied = self.extract_token(data)
        if not supplied or not hmac.compare_digest(
            supplied.encode("utf-8"), self.server.write_token.encode("utf-8")
        ):
            self.send_json_error(403, "token_invalid", "Token inválido o ausente.")
            return False
        return True

    def handle_auth(self):
        if getattr(self.server, "read_only", False):
            self.send_json_error(403, "read_only", "Servidor en modo solo lectura.")
            return
        if self.require_crud_token():
            self.send_json(200, {"ok": True, "crud_enabled": True})

    def handle_crud(self, path):
        if getattr(self.server, "read_only", False):
            self.send_json_error(403, "read_only", "Servidor en modo solo lectura.")
            return
        data = self.request_data()
        if not self.require_crud_token(data):
            return
        if path == "/api/mkdir":
            self.api_mkdir(data)
        elif path == "/api/rename":
            self.api_rename(data)
        else:
            self.api_delete(data)

    def resolve_parent(self, raw):
        relative = safe_relative(raw, self.share_dir)
        if relative is None:
            return None, None
        parent = self.share_dir if not relative else self.share_dir / relative
        return relative, parent

    def resolve_entry(self, raw):
        """Resolve an existing entry without following a symlink in the leaf.

        Returns (relative, absolute) or (None, None) when the path is unsafe or
        traverses a symlinked directory outside the shared root.
        """
        relative = lexical_relative(raw)
        if relative is None:
            return None, None
        if not relative:
            # The shared root itself: callers reject it with a specific error.
            return "", self.share_dir
        candidate = self.share_dir / relative
        root = self.share_dir.resolve()
        try:
            parent_real = Path(os.path.realpath(candidate.parent))
        except OSError:
            return None, None
        if not parent_real.is_relative_to(root):
            return None, None
        return relative, candidate

    @staticmethod
    def rel_join(base, name):
        return name if not base else f"{base}/{name}"

    def api_mkdir(self, data):
        relative, parent = self.resolve_parent(data.get("path", [""])[0])
        if relative is None:
            self.send_json_error(403, "path_forbidden", "Ruta no permitida.")
            return
        if not parent.is_dir():
            self.send_json_error(404, "not_directory", "No se encontró la carpeta destino.")
            return
        name = strict_name(data.get("name", [""])[0])
        if name is None:
            self.send_json_error(400, "invalid_name", "Nombre inválido.")
            return
        target = safe_target(parent, name)
        if target is None:
            self.send_json_error(400, "invalid_name", "Nombre inválido.")
            return
        try:
            target.mkdir()
        except FileExistsError:
            self.send_json_error(409, "resource_exists", "Ya existe un recurso con ese nombre.")
            return
        except OSError:
            self.send_json_error(500, "create_failed", "No se pudo crear la carpeta.")
            return
        self.send_json(201, {"created": self.rel_join(relative, target.name), "type": "directory"})

    def api_rename(self, data):
        relative, source = self.resolve_entry(data.get("path", [""])[0])
        if relative is None:
            self.send_json_error(403, "path_forbidden", "Ruta no permitida.")
            return
        if not relative:
            self.send_json_error(400, "cannot_rename_root", "No se puede renombrar la raíz compartida.")
            return
        if source.is_symlink():
            self.send_json_error(400, "symlink_not_allowed", "No se permiten enlaces simbólicos.")
            return
        if not os.path.lexists(source):
            self.send_json_error(404, "not_found", "No se encontró el recurso a renombrar.")
            return
        new_name = strict_name(data.get("new_name", [""])[0])
        if new_name is None:
            self.send_json_error(400, "invalid_name", "Nombre nuevo inválido.")
            return
        target = source.parent / new_name
        if os.path.lexists(target):
            self.send_json_error(409, "resource_exists", "Ya existe un recurso con ese nombre.")
            return
        try:
            source.rename(target)
        except OSError:
            self.send_json_error(500, "rename_failed", "No se pudo renombrar.")
            return
        parent_rel = Path(relative).parent.as_posix()
        if parent_rel == ".":
            parent_rel = ""
        self.send_json(200, {"from": relative, "to": self.rel_join(parent_rel, target.name)})

    def api_delete(self, data):
        relative, target = self.resolve_entry(data.get("path", [""])[0])
        if relative is None:
            self.send_json_error(403, "path_forbidden", "Ruta no permitida.")
            return
        if not relative:
            self.send_json_error(403, "cannot_delete_root", "No se puede borrar la raíz compartida.")
            return
        if target.is_symlink():
            self.send_json_error(400, "symlink_not_allowed", "No se permiten enlaces simbólicos.")
            return
        if not os.path.lexists(target):
            self.send_json_error(404, "not_found", "No se encontró el recurso a borrar.")
            return
        confirm = (data.get("confirm", [""])[0] or "").strip()
        if confirm != target.name:
            self.send_json_error(
                400, "confirmation_mismatch", "El nombre de confirmación no coincide."
            )
            return
        try:
            if target.is_dir():
                if any(target.iterdir()):
                    self.send_json_error(
                        409, "directory_not_empty", "La carpeta no está vacía; no se borra."
                    )
                    return
                target.rmdir()
                kind = "directory"
            else:
                target.unlink()
                kind = "file"
        except OSError:
            self.send_json_error(500, "delete_failed", "No se pudo borrar el recurso.")
            return
        self.send_json(200, {"deleted": relative, "type": kind})

    def send_json(self, status, payload):
        body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json_error(self, status, code, message):
        self.send_response(status)
        body = (json.dumps({"error": code, "message": message}, sort_keys=True) + "\n").encode("utf-8")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def status_payload(self):
        read_only = getattr(self.server, "read_only", False)
        token_required = self.token_configured()
        return {
            "status": "ok" if self.server.started_at else "starting",
            "ready": True,
            "version": __version__,
            "uptime_seconds": int(time.monotonic() - self.server.started_at),
            "directory": str(self.server.directory),
            "bind": self.server.bind_address,
            "port": self.server.server_port,
            "read_only": read_only,
            "token_required": token_required,
            "crud_enabled": token_required and not read_only,
            "max_upload_mb": getattr(self.server, "max_upload_mb", 0),
        }

    def send_index(self):
        self.serve_static_file("index.html")

    def send_static(self, url_path):
        self.serve_static_file(url_path[len("/static/"):])

    def serve_static_file(self, relative):
        safe = lexical_relative(relative)
        if not safe:
            self.send_json_error(404, "not_found", "No se encontró el recurso solicitado.")
            return
        try:
            resolved = (STATIC_DIR / safe).resolve()
        except OSError:
            resolved = None
        if resolved is None or not resolved.is_relative_to(STATIC_DIR.resolve()):
            self.send_json_error(403, "path_forbidden", "Ruta no permitida.")
            return
        if not resolved.is_file():
            self.send_json_error(404, "not_found", "No se encontró el recurso solicitado.")
            return
        try:
            stat = resolved.stat()
            body = resolved.read_bytes()
        except OSError:
            self.send_json_error(500, "read_failed", "No se pudo leer el recurso.")
            return
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        self._allow_cache = True
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        ctype = CONTENT_TYPES.get(Path(safe).suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, params):
        raw_path = params.get("path", [params.get("name", [""])[0]])[0]
        relative = safe_relative(raw_path, self.share_dir)
        if relative is None:
            self.send_json_error(403, "path_forbidden", "Ruta no permitida.")
            return

        path = self.share_dir if not relative else self.share_dir / relative
        if not path.is_file():
            self.send_json_error(404, "not_found", "No se encontró el archivo solicitado.")
            return

        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(path.name)}")
        self.send_header("Content-Length", str(size))
        self.end_headers()

        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                self.wfile.write(chunk)

    def send_list(self, params):
        raw_path = params.get("path", [""])[0]
        relative = safe_relative(raw_path, self.share_dir)
        if relative is None:
            self.send_json_error(403, "path_forbidden", "Ruta no permitida.")
            return

        target = self.share_dir if not relative else self.share_dir / relative
        if not target.is_dir():
            self.send_json_error(404, "not_directory", "No se encontró la carpeta solicitada.")
            return

        try:
            offset = int(params.get("offset", ["0"])[0])
            limit = int(params.get("limit", ["1000"])[0])
        except ValueError:
            self.send_json_error(400, "invalid_query", "offset y limit deben ser enteros.")
            return
        offset = max(0, offset)
        limit = max(1, min(limit, 5000))

        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        items = []
        for entry in entries[offset:offset + limit]:
            try:
                stat = entry.stat()
            except OSError:
                continue
            items.append({
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
                "size": None if entry.is_dir() else stat.st_size,
                "modified": int(stat.st_mtime),
            })

        payload = {
            "path": relative or "/",
            "total": len(entries),
            "offset": offset,
            "limit": limit,
            "entries": items,
        }
        self.send_json(200, payload)

    def send_info(self, params):
        raw_path = params.get("path", [""])[0]
        relative = safe_relative(raw_path, self.share_dir)
        if relative is None:
            self.send_json_error(403, "path_forbidden", "Ruta no permitida.")
            return

        target = self.share_dir if not relative else self.share_dir / relative
        if not target.exists():
            self.send_json_error(404, "not_found", "No se encontró el recurso solicitado.")
            return

        stat = target.stat()
        payload = {
            "path": relative or "/",
            "name": target.name or str(self.share_dir.name),
            "type": "directory" if target.is_dir() else "file",
            "size": None if target.is_dir() else stat.st_size,
            "modified": int(stat.st_mtime),
        }
        if target.is_file():
            payload["download_url"] = f"/api/download?path={urllib.parse.quote(relative, safe='/')}"
        self.send_json(200, payload)

    def send_version(self):
        self.send_json(200, {"version": __version__})

    def drain_request_body(self, limit):
        """Read and discard up to `limit` bytes so over-limit clients can finish
        sending and still read the error response (avoids a connection reset)."""
        if limit <= 0:
            return
        try:
            self.connection.settimeout(5)
        except OSError:
            pass
        remaining = limit
        while remaining > 0:
            try:
                chunk = self.rfile.read(min(65536, remaining))
            except (OSError, ValueError):
                break
            if not chunk:
                break
            remaining -= len(chunk)

    def receive_files(self):
        ctype = self.headers.get("Content-Type", "")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Longitud inválida")
            return

        max_upload = getattr(self.server, "max_upload_bytes", 5 * 1024 * 1024 * 1024)
        if length <= 0:
            self.send_error(400, "Solicitud de subida vacía o sin Content-Length")
            return
        if length > max_upload:
            self.drain_request_body(min(length, UPLOAD_DRAIN_LIMIT))
            self.send_error(
                413,
                f"Subida demasiado grande ({length} bytes); límite: {max_upload} bytes",
            )
            return

        body = self.rfile.read(length)
        boundary_match = re.search(rb'boundary=([^;]+)', ctype.encode())
        if not boundary_match:
            self.send_error(400, "multipart inválido")
            return

        boundary = boundary_match.group(1).strip().strip(b'"')
        parts = [part for part in body.split(b"--" + boundary) if b"\r\n\r\n" in part]

        current_rel = ""
        supplied_token = ""
        for part in parts:
            headers, data = part.split(b"\r\n\r\n", 1)
            if data.endswith(b"\r\n"):
                data = data[:-2]
            field_match = re.search(rb'name="([^"]*)"', headers)
            if not field_match:
                continue
            name_value = field_match.group(1).decode("utf-8", "replace")
            if name_value == "current_path":
                current_rel = safe_relative(data.decode("utf-8", "replace").strip(), self.share_dir) or ""
            elif name_value == "token":
                supplied_token = data.decode("utf-8", "replace").strip()

        if self.token_configured():
            candidate = supplied_token or self.extract_token()
            if not hmac.compare_digest(
                candidate.encode("utf-8"), self.server.write_token.encode("utf-8")
            ):
                self.send_error(403, "Token inválido en la subida")
                return

        if current_rel:
            resolved_dir = (self.share_dir / current_rel).resolve()
            if self.share_dir.resolve() not in resolved_dir.parents or not resolved_dir.is_dir():
                self.send_error(403, "Directorio no permitido")
                return
            target_dir = resolved_dir
        else:
            target_dir = self.share_dir

        uploaded = []
        for part in parts:
            headers, data = part.split(b"\r\n\r\n", 1)
            if data.endswith(b"\r\n"):
                data = data[:-2]

            field_match = re.search(rb'name="([^"]*)"', headers)
            if not field_match or field_match.group(1) != b"file":
                continue

            filename_match = re.search(rb'filename="([^"]*)"', headers)
            if not filename_match:
                continue

            raw_name = filename_match.group(1).decode("utf-8", "replace")
            filename = os.path.basename(raw_name.replace("\\", "/"))
            if not filename or filename in {".", ".."}:
                continue

            target = unique_path(target_dir, filename)
            temporary_name = f".{target.name}.tmp"
            temporary_path = target.parent / temporary_name
            try:
                temporary_path.write_bytes(data)
                os.replace(temporary_path, target)
                uploaded.append(target.name)
            except (OSError, RuntimeError):
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

        if not uploaded:
            self.send_error(400, "No se subió ningún archivo válido")
            return

        redirect = "/?uploaded=1"
        if current_rel:
            redirect = f"/?path={urllib.parse.quote(current_rel, safe='/')}&uploaded=1"

        self.send_response(303)
        self.send_header("Location", redirect)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        if not getattr(self.server, "log_requests", True):
            return
        user_agent = self.headers.get("User-Agent", "-")
        print(f"[webshare] {self.address_string()} - {fmt % args} UA={user_agent!r}")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; img-src 'self' data:; font-src 'self';",
        )
        if not getattr(self, "_allow_cache", False):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
        super().end_headers()


def log_step(message):
    print(f"[webshare] {message}", flush=True)


def self_update():
    repo = REPO_DIR
    log_step("Actualización iniciada.")
    log_step(f"Repositorio: {repo}")
    if not (repo / ".git").exists():
        log_step("No se encontró metadata Git en el repositorio instalado.")
        log_step("Ejecutá el install.sh público o inicializá Git antes de continuar.")
        raise SystemExit(1)

    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    stashed = False
    if (dirty.stdout or "").strip():
        log_step("Hay cambios locales en el repo instalado; se guardan temporalmente para no fallar el update.")
        subprocess.run(
            ["git", "-C", str(repo), "stash", "push", "--include-untracked", "-m", "webshare-self-update"],
            check=True,
        )
        stashed = True

    try:
        log_step("Consultando origen remoto con git fetch --all...")
        try:
            subprocess.run(["git", "-C", str(repo), "fetch", "--all"], check=True, stdout=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            log_step(f"Actualización remota falló: {exc}")
            log_step("Verificá conexión a Internet y que el repositorio exista en GitHub.")
            raise SystemExit(1)

        log_step("Aplicando cambios desde origin/main con git pull --ff-only ...")
        try:
            subprocess.run(["git", "-C", str(repo), "pull", "--ff-only", "origin", "main"], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            log_step(f"git pull falló: {exc}")
            log_step("Verificá la rama local y el remoto, o ejecutá git pull manual.")
            raise SystemExit(1)
    finally:
        if stashed:
            log_step("Restaurando cambios locales temporales.")
            subprocess.run(["git", "-C", str(repo), "stash", "pop"], check=False)

    target = repo / "webshare.py"
    target.chmod(0o755)
    log_step("Permisos de ejecución asegurados en webshare.py.")
    log_step("Actualización completada.")



def self_uninstall(remove_repo=False):
    log_step("Desinstalación iniciada.")
    if BIN_PATH.exists() or BIN_PATH.is_symlink():
        BIN_PATH.unlink()
        log_step(f"Symlink eliminado: {BIN_PATH}")
    else:
        log_step(f"No había symlink en {BIN_PATH}.")

    if remove_repo and INSTALL_ROOT.exists():
        log_step(f"Eliendo repositorio instalado: {INSTALL_ROOT}")
        shutil.rmtree(INSTALL_ROOT)
        log_step("Repositorio instalado eliminado.")
    elif remove_repo:
        log_step("No existía repo instalado para eliminar.")
    else:
        log_step("Para eliminar también el repositorio instalado, usá --uninstall --yes.")

    log_step("Desinstalación completada.")


def main():
    parser = argparse.ArgumentParser(description="Servidor web simple para compartir archivos")
    parser.add_argument("--dir", default=str(Path.home() / "Descargas"), help="carpeta compartida")
    parser.add_argument("--bind", default="0.0.0.0", help="dirección de escucha")
    parser.add_argument("--port", type=int, default=8000, help="puerto HTTP")
    parser.add_argument("--read-only", action="store_true", help="deshabilita subidas y escrituras")
    parser.add_argument("--max-upload-mb", type=int, default=5120, help="límite de subida en MB")
    parser.add_argument("--version", action="store_true", help="mostrar versión")
    parser.add_argument("--update", action="store_true", help="actualiza repositorio y symlink")
    parser.add_argument("--uninstall", action="store_true", help="desinstala symlink y repo opcional")
    parser.add_argument("--yes", action="store_true", help="confirma cambios destructivos")
    parser.add_argument("--quiet", action="store_true", help="solo muestra arranque y errores importantes")
    parser.add_argument("--token", default=os.environ.get("WEBSHARE_TOKEN", ""), help="token requerido para subir (env WEBSHARE_TOKEN)")
    args = parser.parse_args()

    if args.version:
        print(f"WebShare {__version__}")
        return
    if args.update:
        self_update()
        return
    if args.uninstall:
        self_uninstall(remove_repo=args.yes)
        return

    directory = Path(args.dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)

    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    httpd.directory = directory
    httpd.bind_address = args.bind
    httpd.started_at = time.monotonic()
    httpd.started_at_wall = time.time()
    httpd.read_only = args.read_only
    httpd.max_upload_bytes = max(1, args.max_upload_mb) * 1024 * 1024
    httpd.max_upload_mb = max(1, args.max_upload_mb)
    httpd.log_requests = not args.quiet
    httpd.write_token = args.token.strip()

    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except OSError:
        local_ip = "127.0.0.1"

    print("WebShare activo")
    print(f"  URL:   http://{local_ip}:{args.port}")
    print(f"  Bind:  {args.bind}:{args.port}")
    print(f"  Dir:   {directory}")
    print(f"  Max upload: {args.max_upload_mb} MB")
    if args.read_only:
        print("  Modo: solo lectura")
    if args.quiet:
        print("  Logs HTTP: desactivados")
    print("  Ctrl+C para detener.")

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    try:
        server_thread.join()
    except KeyboardInterrupt:
        print("\nDeteniendo WebShare...")
        httpd.shutdown()
        server_thread.join()
    finally:
        httpd.server_close()
    print("WebShare detenido.")


if __name__ == "__main__":
    main()
