#!/usr/bin/env python3
import argparse
import errno
import hmac
import ipaddress
import json
import os
import re
import socket
import subprocess
import shutil
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_DIR = Path.home() / "Descargas"

__version__ = "0.13.1"
REPO_DIR = Path(__file__).resolve().parent
STATIC_DIR = REPO_DIR / "static"
INSTALL_ROOT = Path.home() / ".local/share/webshare"
BIN_PATH = Path.home() / ".local/bin/webshare"

# Upper bound of request body we are willing to drain when rejecting an
# oversized upload, so the client can read the 413 instead of a reset.
UPLOAD_DRAIN_LIMIT = 8 * 1024 * 1024

# Fallback upload ceiling when the server object has no limit configured.
DEFAULT_MAX_UPLOAD = 5 * 1024 * 1024 * 1024

# Largest urlencoded CRUD body we will buffer. CRUD arguments normally travel in
# the query string, so the body only exists for clients that still post a form.
MAX_FORM_BYTES = 64 * 1024

# Largest multipart boundary we accept (RFC 2046 recommends <= 70 octets).
MAX_BOUNDARY_BYTES = 200

# How long a connection may stay idle (no bytes) before it is closed. Bounds
# slowloris-style connections that dribble headers or bodies.
CONNECTION_TIMEOUT = 30

# Upper bound of concurrent client connections handled at once.
MAX_CONNECTIONS = 64

# Most directory entries we scan/sort for one listing. Directories larger than
# this are reported with `truncated: true` instead of materializing everything.
MAX_LIST_SCAN = 50000

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


class MultipartError(Exception):
    """A multipart body could not be parsed."""


class UploadTooLarge(Exception):
    """The upload grew past the configured limit while being streamed."""


class LimitedReader:
    """Wrap a stream so that no more than `length` bytes are ever read.

    Keeps the parser from reading into a pipelined request and lets a truncated
    body surface as EOF instead of blocking forever.
    """

    def __init__(self, stream, length):
        self.stream = stream
        self.remaining = max(0, length)

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        if size is None or size < 0:
            size = self.remaining
        chunk = self.stream.read(min(size, self.remaining))
        self.remaining -= len(chunk)
        return chunk


class MultipartPart:
    """A single part of a multipart body, streamed from the scanner."""

    def __init__(self, scanner, headers):
        self._scanner = scanner
        self._drained = False
        self.headers = headers

    def _disposition(self, key):
        disposition = self.headers.get(b"content-disposition", b"")
        match = re.search(rb'(?:^|;)\s*' + key + rb'="([^"]*)"', disposition)
        return match.group(1) if match else None

    @property
    def name(self):
        return self._disposition(b"name")

    @property
    def filename(self):
        return self._disposition(b"filename")

    def iter_bytes(self):
        if self._drained:
            return
        self._drained = True
        yield from self._scanner.iter_content()

    def value(self, limit):
        data = bytearray()
        for chunk in self.iter_bytes():
            data += chunk
            if len(data) > limit:
                raise MultipartError("campo multipart demasiado grande")
        return bytes(data)

    def save_to(self, handle):
        written = 0
        for chunk in self.iter_bytes():
            handle.write(chunk)
            written += len(chunk)
        return written


class MultipartScanner:
    """Incremental scanner for `multipart/form-data` bodies.

    Deliberately keeps only a small tail of the body in memory, so file parts
    are streamed rather than buffered whole. The whole read stays bounded by the
    limit enforced in `_pull`.
    """

    CHUNK = 65536
    MAX_LINE = 16 * 1024

    def __init__(self, stream, boundary, max_bytes):
        self.stream = stream
        self.boundary = bytes(boundary)
        self.marker = b"--" + self.boundary
        self.delimiter = b"\r\n--" + self.boundary
        self.max_bytes = max_bytes
        self.buffer = bytearray()
        self.eof = False
        self.closed = False
        self.consumed = 0

    def _pull(self):
        if self.eof:
            return False
        chunk = self.stream.read(self.CHUNK)
        if not chunk:
            self.eof = True
            return False
        self.consumed += len(chunk)
        if self.consumed > self.max_bytes:
            raise UploadTooLarge("la subida superó el límite permitido")
        self.buffer += chunk
        return True

    def _read_line(self):
        while True:
            index = self.buffer.find(b"\r\n")
            if index != -1:
                line = bytes(self.buffer[:index])
                del self.buffer[: index + 2]
                return line
            if len(self.buffer) > self.MAX_LINE:
                raise MultipartError("línea multipart demasiado larga")
            if not self._pull():
                line = bytes(self.buffer)
                self.buffer.clear()
                return line

    def open(self):
        """Consume the preamble until the first boundary line."""
        while True:
            line = self._read_line()
            if line.startswith(self.marker):
                if line[len(self.marker):].startswith(b"--"):
                    raise MultipartError("cuerpo multipart vacío")
                return
            if self.eof and not line:
                raise MultipartError("no se encontró el boundary inicial")

    def read_headers(self):
        headers = {}
        while True:
            line = self._read_line()
            if not line:
                return headers
            if line[:1] in (b" ", b"\t"):
                continue
            if b":" not in line:
                raise MultipartError("cabecera multipart inválida")
            name, _, value = line.partition(b":")
            headers[name.strip().lower()] = value.strip()

    def iter_content(self):
        """Yield the current part body, stopping right after its boundary."""
        keep = len(self.delimiter)
        while True:
            index = self.buffer.find(self.delimiter)
            if index != -1:
                if index:
                    yield bytes(self.buffer[:index])
                del self.buffer[: index + len(self.delimiter)]
                self.closed = self._read_line().startswith(b"--")
                return
            if len(self.buffer) > keep:
                emit = len(self.buffer) - keep
                yield bytes(self.buffer[:emit])
                del self.buffer[:emit]
            if not self._pull():
                raise MultipartError("multipart truncado")


def iter_multipart(stream, boundary, max_bytes):
    """Yield `MultipartPart`s from a multipart body, draining each one."""
    scanner = MultipartScanner(stream, boundary, max_bytes)
    scanner.open()
    while not scanner.closed:
        part = MultipartPart(scanner, scanner.read_headers())
        yield part
        for _ in part.iter_bytes():
            pass


UPLOAD_TEMP_PREFIX = ".webshare-upload-"
UPLOAD_TEMP_SUFFIX = ".part"


def is_upload_temp(name):
    """Identify the internal spool files, which must never surface in the API."""
    return str(name).startswith(UPLOAD_TEMP_PREFIX) and str(name).endswith(UPLOAD_TEMP_SUFFIX)


def redact_query_token(text):
    """Mask any `token=` value in a query string so it never reaches the logs."""
    return re.sub(r"(?i)([?&]token=)[^&\s]+", r"\1<redacted>", str(text))


def scan_directory(directory, max_scan):
    """List a directory sorted, without materializing more than `max_scan` entries.

    Returns `(entries, truncated)`. Sorted files-first, then by lowercase name.
    """
    entries = []
    truncated = False
    for entry in directory.iterdir():
        if is_upload_temp(entry.name):
            continue
        if len(entries) >= max_scan:
            truncated = True
            break
        entries.append(entry)
    entries.sort(key=lambda p: (p.is_file(), p.name.lower()))
    return entries, truncated


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
        if is_upload_temp(part):
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
    if is_upload_temp(name):
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
        if is_upload_temp(part):
            return None
        parts.append(part)
    return "/".join(parts)


class Handler(BaseHTTPRequestHandler):
    server_version = f"webshare/{__version__}"
    # Ocultar la versión de Python en el header `Server` (fingerprinting).
    sys_version = ""
    # Close sockets that sit idle; stops slowloris-style connections.
    timeout = CONNECTION_TIMEOUT

    def version_string(self):
        return self.server_version

    def host_allowed(self):
        """Accept only IP literals, localhost, or the configured bind address.

        A missing `Host` is tolerated (HTTP/1.0). Anything else is rejected, so a
        hostile domain resolving to this machine (DNS rebinding) is not served.
        """
        host = self.headers.get("Host")
        if not host:
            return True
        hostname = host.strip()
        if hostname.startswith("["):
            end = hostname.find("]")
            hostname = hostname[1:end] if end != -1 else hostname
        elif hostname.count(":") == 1:
            hostname = hostname.split(":", 1)[0]
        hostname = hostname.strip().lower()
        if not hostname:
            return True
        if hostname == "localhost":
            return True
        bind = str(getattr(self.server, "bind_address", "")).strip().lower()
        if bind and bind not in ("0.0.0.0", "::") and hostname == bind:
            return True
        try:
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            return False

    def reject_bad_host(self):
        if self.host_allowed():
            return False
        self.close_connection = True
        self.send_json_error(400, "invalid_host", "Host no permitido.")
        return True

    @property
    def share_dir(self):
        return Path(self.server.directory)

    def do_GET(self):
        if self.reject_bad_host():
            return
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
        if self.reject_bad_host():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/upload":
            if not self.upload_allowed():
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    length = 0
                self.drain_request_body(min(length, UPLOAD_DRAIN_LIMIT))
                if getattr(self.server, "read_only", False):
                    self.send_json_error(403, "read_only", "Servidor en modo solo lectura.")
                else:
                    self.send_json_error(
                        403,
                        "upload_disabled",
                        "Subidas deshabilitadas: iniciá el servidor con --token "
                        "o --allow-anonymous-upload.",
                    )
                return
            self.receive_files()
            return
        if path in ("/api/mkdir", "/api/rename", "/api/delete"):
            self.handle_crud(path)
            return
        self.send_json_error(404, "not_found", "No se encontró el recurso solicitado.")

    def do_DELETE(self):
        if self.reject_bad_host():
            return
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
        if length > MAX_FORM_BYTES:
            # Refuse oversized form bodies instead of buffering them. Closing the
            # connection avoids keeping the unread body around for the next request.
            self.close_connection = True
            length = 0
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

    def upload_allowed(self):
        if getattr(self.server, "read_only", False):
            return False
        # Secure by default: uploads need a token, unless the operator explicitly
        # opted into anonymous uploads for a trusted LAN.
        return self.token_configured() or getattr(self.server, "allow_anonymous_upload", False)

    def anonymous_upload(self):
        return (
            not getattr(self.server, "read_only", False)
            and not self.token_configured()
            and getattr(self.server, "allow_anonymous_upload", False)
        )

    def token_authenticated(self, supplied):
        if not self.token_configured() or not supplied:
            return False
        return hmac.compare_digest(
            supplied.encode("utf-8"), self.server.write_token.encode("utf-8")
        )

    def require_crud_token(self, data=None):
        if not self.token_configured():
            self.send_json_error(
                403, "crud_disabled", "CRUD deshabilitado: iniciá el servidor con --token."
            )
            return False
        if not self.token_authenticated(self.extract_token(data)):
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
        # Authenticate before reading the body: an unauthenticated client must not
        # be able to make the server buffer an unbounded payload.
        if not self.require_crud_token():
            return
        data = self.request_data()
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
            "root_name": self.server.directory.name or "/",
            "bind": self.server.bind_address,
            "port": self.server.server_port,
            "read_only": read_only,
            "token_required": token_required,
            "crud_enabled": token_required and not read_only,
            "upload_enabled": self.upload_allowed(),
            "anonymous_upload": self.anonymous_upload(),
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

        # Open before answering: a permission error must become a clean JSON reply
        # instead of a 200 with a truncated body.
        try:
            handle = path.open("rb")
        except OSError:
            self.send_json_error(500, "read_failed", "No se pudo leer el archivo.")
            return

        with handle:
            try:
                size = os.fstat(handle.fileno()).st_size
            except OSError:
                size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(path.name)}")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            while chunk := handle.read(1024 * 1024):
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

        try:
            entries, truncated = scan_directory(target, MAX_LIST_SCAN)
        except OSError:
            self.send_json_error(500, "read_failed", "No se pudo leer la carpeta.")
            return
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
            "truncated": truncated,
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
        try:
            stat = target.stat()
        except OSError:
            self.send_json_error(404, "not_found", "No se encontró el recurso solicitado.")
            return

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

    # Largest non-file multipart field we are willing to buffer in memory.
    MAX_FIELD_BYTES = 64 * 1024

    def reject_upload(self, status, code, message, remaining):
        """Drain what is left of a rejected upload (bounded) and answer JSON."""
        self.drain_request_body(min(max(remaining, 0), UPLOAD_DRAIN_LIMIT))
        self.send_json_error(status, code, message)

    @staticmethod
    def cleanup_paths(paths):
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def clean_filename(raw):
        name = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        name = os.path.basename(name.replace("\\", "/"))
        # NUL and control characters are invalid in filenames and would raise
        # ValueError deep inside pathlib/os; drop them before anything else.
        return "".join(ch for ch in name if ord(ch) >= 32 and ch != "\x7f").strip()

    def spool_part(self, part):
        """Stream a file part to a temporary file in the shared root.

        The destination folder is only known after every field is parsed (parts
        may arrive in any order), so files spool here first and are then renamed
        into place in the same filesystem. The temporary name is reserved: it is
        hidden from listings and rejected by every endpoint.
        """
        fd, temp_name = tempfile.mkstemp(
            prefix=UPLOAD_TEMP_PREFIX, suffix=UPLOAD_TEMP_SUFFIX, dir=self.share_dir
        )
        temp_path = Path(temp_name)
        try:
            handle = os.fdopen(fd, "wb")
        except OSError:
            os.close(fd)
            self.cleanup_paths([temp_path])
            raise
        try:
            with handle:
                part.save_to(handle)
        except BaseException:
            self.cleanup_paths([temp_path])
            raise
        return temp_path

    @staticmethod
    def move_into_place(temp_path, target):
        try:
            os.replace(temp_path, target)
        except OSError as exc:
            if getattr(exc, "errno", None) != errno.EXDEV:
                raise
            shutil.copyfile(temp_path, target)
            temp_path.unlink(missing_ok=True)

    def receive_files(self):
        content_type = self.headers.get("Content-Type", "")
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self.send_json_error(400, "invalid_length", "Longitud de subida inválida.")
            return

        max_upload = getattr(self.server, "max_upload_bytes", DEFAULT_MAX_UPLOAD)
        if length <= 0:
            self.send_json_error(
                400, "empty_upload", "Solicitud de subida vacía o sin Content-Length."
            )
            return
        if length > max_upload:
            self.reject_upload(
                413,
                "upload_too_large",
                f"Subida demasiado grande ({length} bytes); límite: {max_upload} bytes.",
                length,
            )
            return

        boundary_match = re.search(rb'boundary=([^;]+)', content_type.encode("utf-8", "replace"))
        boundary = boundary_match.group(1).strip().strip(b'"') if boundary_match else b""
        if not boundary or len(boundary) > MAX_BOUNDARY_BYTES:
            self.reject_upload(
                400, "invalid_multipart", "multipart inválido o sin boundary.", length
            )
            return

        reader = LimitedReader(self.rfile, length)
        require_token = self.token_configured()
        authenticated = self.token_authenticated(self.extract_token())
        fields = {}
        pending_files = []
        temporary_paths = []
        try:
            for part in iter_multipart(reader, boundary, max_upload):
                name = part.name
                if name is None:
                    continue
                field_name = name.decode("utf-8", "replace")
                filename = part.filename
                if filename is None:
                    value = part.value(self.MAX_FIELD_BYTES).decode("utf-8", "replace").strip()
                    fields[field_name] = value
                    if field_name == "token" and not authenticated:
                        # A token sent as a form field only counts if it arrives
                        # before any file part, so nothing is spooled before auth.
                        authenticated = self.token_authenticated(value)
                        if not authenticated:
                            self.reject_upload(
                                403, "token_invalid", "Token inválido o ausente.", reader.remaining
                            )
                            return
                    continue
                if require_token and not authenticated:
                    # Reject before spooling: an unauthenticated client must not be
                    # able to force the (potentially huge) body onto disk.
                    self.reject_upload(
                        403, "token_invalid", "Token inválido o ausente.", reader.remaining
                    )
                    return
                temp_path = self.spool_part(part)
                temporary_paths.append(temp_path)
                pending_files.append((temp_path, self.clean_filename(filename)))
        except UploadTooLarge:
            self.cleanup_paths(temporary_paths)
            self.reject_upload(
                413, "upload_too_large", "La subida superó el límite permitido.", reader.remaining
            )
            return
        except MultipartError as exc:
            self.cleanup_paths(temporary_paths)
            self.reject_upload(400, "invalid_multipart", str(exc), reader.remaining)
            return
        except OSError:
            self.cleanup_paths(temporary_paths)
            self.reject_upload(
                500, "upload_failed", "No se pudo procesar la subida.", reader.remaining
            )
            return

        if require_token and not authenticated:
            self.cleanup_paths(temporary_paths)
            self.send_json_error(403, "token_invalid", "Token inválido o ausente.")
            return

        current_rel = safe_relative(fields.get("current_path", ""), self.share_dir)
        if current_rel is None:
            self.cleanup_paths(temporary_paths)
            self.send_json_error(403, "path_forbidden", "Ruta no permitida.")
            return
        if current_rel:
            resolved_dir = (self.share_dir / current_rel).resolve()
            if self.share_dir.resolve() not in resolved_dir.parents or not resolved_dir.is_dir():
                self.cleanup_paths(temporary_paths)
                self.send_json_error(403, "path_forbidden", "Directorio no permitido.")
                return
            target_dir = resolved_dir
        else:
            target_dir = self.share_dir

        uploaded = []
        try:
            for temp_path, filename in pending_files:
                if not filename or filename in {".", ".."} or is_upload_temp(filename):
                    temp_path.unlink(missing_ok=True)
                    continue
                target = unique_path(target_dir, filename)
                self.move_into_place(temp_path, target)
                uploaded.append(target.name)
        except OSError:
            self.cleanup_paths(temporary_paths)
            self.send_json_error(500, "upload_failed", "No se pudo guardar el archivo.")
            return
        finally:
            self.cleanup_paths(temporary_paths)

        if not uploaded:
            self.send_json_error(400, "no_files", "No se subió ningún archivo válido.")
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
        message = redact_query_token(fmt % args)
        user_agent = self.headers.get("User-Agent", "-")
        print(f"[webshare] {self.address_string()} - {message} UA={user_agent!r}")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; img-src 'self' data:; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self';",
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


class BoundedHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a cap on simultaneous connections.

    Every accepted socket otherwise spawns a thread, so a flood of slow
    connections can exhaust FDs, threads and memory. Excess connections are
    dropped immediately.
    """

    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                self.shutdown_request(request)
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def sweep_upload_temps(directory):
    """Remove orphaned upload spool files left behind by a crashed process."""
    removed = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not is_upload_temp(entry.name):
                    continue
                try:
                    if entry.is_file(follow_symlinks=False):
                        os.unlink(entry.path)
                        removed += 1
                except OSError:
                    continue
    except OSError:
        return removed
    return removed


def main():
    parser = argparse.ArgumentParser(description="Servidor web simple para compartir archivos")
    parser.add_argument("--dir", default=str(Path.home() / "Descargas"), help="carpeta compartida")
    parser.add_argument("--bind", default="0.0.0.0", help="dirección de escucha")
    parser.add_argument("--port", type=int, default=8000, help="puerto HTTP")
    parser.add_argument("--read-only", action="store_true", help="deshabilita subidas y escrituras")
    parser.add_argument(
        "--allow-anonymous-upload",
        action="store_true",
        help="permite subir sin token (por defecto, sin token no se puede subir)",
    )
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
    swept = sweep_upload_temps(directory)

    httpd = BoundedHTTPServer((args.bind, args.port), Handler)
    httpd.directory = directory
    httpd.bind_address = args.bind
    httpd.started_at = time.monotonic()
    httpd.started_at_wall = time.time()
    httpd.read_only = args.read_only
    httpd.max_upload_bytes = max(1, args.max_upload_mb) * 1024 * 1024
    httpd.max_upload_mb = max(1, args.max_upload_mb)
    httpd.log_requests = not args.quiet
    httpd.write_token = args.token.strip()
    httpd.allow_anonymous_upload = args.allow_anonymous_upload

    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except OSError:
        local_ip = "127.0.0.1"

    print("WebShare activo")
    if swept:
        print(f"  Temporales de subida huérfanos eliminados: {swept}")
    print(f"  URL:   http://{local_ip}:{args.port}")
    print(f"  Bind:  {args.bind}:{args.port}")
    print(f"  Dir:   {directory}")
    print(f"  Max upload: {args.max_upload_mb} MB")
    if args.read_only:
        print("  Modo: solo lectura")
    elif not args.token.strip() and args.allow_anonymous_upload:
        print("  Modo: subidas anónimas habilitadas")
    elif not args.token.strip():
        print("  Subidas: deshabilitadas (usá --token o --allow-anonymous-upload)")
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
