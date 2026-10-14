#!/usr/bin/env python3
import argparse
import html
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

__version__ = "0.6.0"
REPO_DIR = Path(__file__).resolve().parent
INSTALL_ROOT = Path.home() / ".local/share/webshare"
BIN_PATH = Path.home() / ".local/bin/webshare"


def human_size(size):
    value = float(size)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def fmt_elapsed(seconds):
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def safe_relative(raw_path, root):
    if raw_path is None:
        return ""
    raw_path = urllib.parse.unquote(raw_path).strip().replace("\\", "/")
    raw_path = raw_path.strip("/")
    if raw_path in {"", "."}:
        return ""
    candidate = (root / raw_path).resolve()
    try:
        relative = candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return relative.as_posix()


def safe_target(directory, filename):
    filename = os.path.basename(filename.replace("\\", "/"))
    if not filename or filename in {".", ".."}:
        return None
    target = (directory / filename).resolve()
    if target.parent != directory.resolve():
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


class Handler(BaseHTTPRequestHandler):
    server_version = "webshare/3.0"

    @property
    def share_dir(self):
        return Path(self.server.directory)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(url.query)

        if url.path == "/":
            self.send_page(params)
        elif url.path in ("/health", "/readyz"):
            payload = {
                "status": "ok" if self.server.started_at else "starting",
                "ready": True,
                "version": __version__,
                "uptime_seconds": int(time.monotonic() - self.server.started_at),
                "directory": str(self.server.directory),
                "bind": self.server.bind_address,
                "port": self.server.server_port,
                "read_only": getattr(self.server, "read_only", False),
                "max_upload_mb": getattr(self.server, "max_upload_mb", 0),
            }
            if url.path == "/readyz":
                payload["path"] = "readyz"
            body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/version":
            body = (json.dumps({"version": __version__}, sort_keys=True) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/download":
            self.send_file(params)
        else:
            self.send_error(404, "No encontrado")

    def do_POST(self):
        if getattr(self.server, "read_only", False):
            self.send_error(403, "Servidor en modo solo lectura")
            return
        if urllib.parse.urlsplit(self.path).path != "/upload":
            self.send_error(404, "No encontrado")
            return
        self.receive_files()

    def send_page(self, params):
        current = safe_relative(params.get("path", [""])[0], self.share_dir)
        if current is None:
            self.send_error(403, "Ruta no permitida")
            return

        target = self.share_dir if not current else self.share_dir / current
        if not target.is_dir():
            self.send_error(404, "Carpeta no encontrada")
            return

        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        rows = []
        if current:
            parent = str(Path(current).parent)
            if parent == ".":
                parent = ""
            rows.append(
                f'<li class="dir">'
                f'<a href="/?path={urllib.parse.quote(parent, safe="/")}">..</a>'
                f'<span class="meta">carpeta superior</span></li>'
            )

        for entry in entries:
            rel = f"{current}/{entry.name}" if current else entry.name
            if entry.is_dir():
                rows.append(
                    f'<li class="dir">'
                    f'<a href="/?path={urllib.parse.quote(rel, safe="/")}">📁 {html.escape(entry.name)}</a>'
                    f'<span class="meta">carpeta</span></li>'
                )
            elif entry.is_file():
                rows.append(
                    f'<li class="file">'
                    f'<a class="name" href="/download?path={urllib.parse.quote(rel, safe="/")}">{html.escape(entry.name)}</a>'
                    f'<span class="meta">{human_size(entry.stat().st_size)}</span></li>'
                )

        rows_html = "".join(rows) or '<li class="empty">Carpeta vacía</li>'

        uploaded = params.get("uploaded", [""])[0] == "1"
        status = '<div class="ok" id="status">Subida completada.</div>' if uploaded else '<div id="status" role="status"></div>'

        body = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>WebShare</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f1115;
      --panel: #181b23;
      --panel-2: #222633;
      --text: #f3f5fa;
      --muted: #b8bfd1;
      --border: #3a4052;
      --accent: #4f8cff;
      --accent-2: #76a5ff;
      --ok: #35c46c;
      --drop: rgba(79,140,255,.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at top, #171a22 0%, var(--bg) 68%);
      color: var(--text);
      font: 15px/1.45 system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    }}
    .shell {{
      width: min(980px, calc(100vw - 24px));
      margin: 20px auto;
      display: grid;
      gap: 16px;
    }}
    .hero, .card {{
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 12px 40px rgba(0,0,0,.18);
    }}
    .hero {{
      padding: 16px 18px;
      background: linear-gradient(135deg, #1d2230, #12151d);
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: 1.55rem;
    }}
    .hero .facts {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
      color: var(--muted);
      font-size: .95rem;
    }}
    .hero .facts strong {{ color: var(--text); }}
    .grid {{
      display: grid;
      grid-template-columns: 1.35fr .95fr;
      gap: 16px;
    }}
    @media (max-width: 780px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
    .card {{ padding: 18px; }}
    h2 {{ margin: 0 0 12px; font-size: 1.08rem; }}
    .path {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 12px;
      color: var(--muted);
      font-size: .92rem;
    }}
    .path code {{
      color: var(--text);
      background: rgba(255,255,255,.06);
      padding: 2px 6px;
      border-radius: 6px;
    }}
    .toolbar {{
      display: flex;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .search {{
      flex: 1;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: var(--panel-2);
      color: var(--text);
    }}
    ul {{
      list-style: none;
      margin: 0;
      padding: 0;
      max-height: 54vh;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(255,255,255,.02);
    }}
    li {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255,255,255,.06);
    }}
    li:last-child {{ border-bottom: 0; }}
    .name {{
      color: var(--accent-2);
      text-decoration: none;
      overflow-wrap: anywhere;
    }}
    .name:hover {{ text-decoration: underline; }}
    .meta {{ color: var(--muted); white-space: nowrap; font-size: .86rem; }}
    .empty {{ color: var(--muted); }}
    .dir a {{ color: var(--accent-2); text-decoration: none; }}
    .dir a:hover {{ text-decoration: underline; }}
    .dropzone {{
      position: relative;
      min-height: 150px;
      padding: 20px;
      border: 2px dashed var(--border);
      border-radius: 14px;
      background: var(--drop);
      display: grid;
      place-items: center;
      text-align: center;
      cursor: pointer;
      transition: .15s ease;
    }}
    .dropzone:hover, .dropzone.dragover {{
      border-color: var(--accent);
      background: rgba(79,140,255,.18);
      transform: translateY(-1px);
    }}
    .dropzone input {{
      position: absolute;
      inset: 0;
      opacity: 0;
      cursor: pointer;
    }}
    .dropzone strong {{ display: block; margin-bottom: 4px; }}
    .info {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 12px;
      color: var(--muted);
    }}
    button {{
      border: 0;
      border-radius: 10px;
      padding: 10px 16px;
      font-weight: 700;
      color: white;
      background: linear-gradient(135deg, var(--accent), #6ea0ff);
      cursor: pointer;
    }}
    button:disabled {{
      opacity: .6;
      cursor: wait;
    }}
    #progressWrap {{
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 8px;
      align-items: center;
      margin-bottom: 10px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: rgba(255,255,255,.03);
    }}
    #progressWrap.hidden {{ display: none !important; }}
    progress {{
      width: 100%;
      height: 8px;
      accent-color: var(--accent);
    }}
    .ok {{
      margin-bottom: 10px;
      padding: 10px 12px;
      border-radius: 10px;
      background: rgba(53,196,108,.16);
      border: 1px solid rgba(53,196,108,.45);
      color: #c8ffd8;
    }}
    .tip {{ color: var(--muted); font-size: .88rem; margin-top: 10px; }}
    [hidden] {{ display: none !important; }}
  </style>
</head>
<body>
  <div class="shell">
    <header class="hero">
      <h1>WebShare</h1>
      <div class="facts">
        <span><strong>Directorio:</strong> {html.escape(str(target))}</span>
        <span><strong>Puerto:</strong> {self.server.server_port}</span>
        <span><strong>Activo:</strong> <span id="uptime">00:00:00</span></span>
        {f'<span><strong>Token:</strong> activo en subidas</span>' if getattr(self.server, "write_token", "") else ""}
      </div>
    </header>

    <div class="grid">
      <section class="card">
        <h2>Carpeta compartida</h2>
        <div class="path">
          <span>Ruta actual:</span>
          <code>{html.escape("/" + current if current else "/")}</code>
          {f'<a class="name" href="/?path={urllib.parse.quote("" if Path(current).parent == Path(".") else str(Path(current).parent), safe="/")}">Volver</a>' if current else ""}
        </div>
        <div class="toolbar">
          <input id="filter" class="search" type="search" placeholder="Filtrar archivos y carpetas...">
        </div>
        <ul id="fileList">{rows_html}</ul>
      </section>

      <section class="card">
        <h2>Subir para la contraparte</h2>
        {status}
        <form id="uploadForm" method="post" action="/upload" enctype="multipart/form-data">
          <input type="hidden" name="current_path" value="{html.escape(current)}">
          {f'<input type="hidden" name="token" value="{html.escape(self.server.write_token)}">' if getattr(self.server, "write_token", "") else ""}
          <label class="dropzone" id="dropzone">
            <span id="dropText">Arrastra aquí o haz clic para elegir archivos</span>
            <input id="fileInput" type="file" name="file" multiple required>
          </label>
          <div id="progressWrap" hidden>
            <progress id="progress" max="100" value="0"></progress>
            <span id="progressText">0%</span>
            <span id="progressBytes" class="meta"></span>
          </div>
          <div class="info">
            <span id="selectedCount">Ningún archivo seleccionado</span>
            <button id="submitBtn" type="submit">Subir</button>
          </div>
        </form>
        <p class="tip">Los archivos subidos se guardan en la carpeta actual. Para videos grandes, mejor usar SSH/SCP.</p>
      </section>
    </div>
  </div>

  <script>
    const startedAt = {int(self.server.started_at_wall * 1000)};
    const uptimeEl = document.getElementById('uptime');
    function formatElapsedWhole(totalSeconds) {{
      totalSeconds = Math.max(0, Math.floor(totalSeconds));
      const days = Math.floor(totalSeconds / 86400);
      let rest = totalSeconds % 86400;
      const hours = Math.floor(rest / 3600);
      rest = rest % 3600;
      const minutes = Math.floor(rest / 60);
      const seconds = rest % 60;
      if (days) return `${{days}}d ${{String(hours).padStart(2, '0')}}:${{String(minutes).padStart(2, '0')}}:${{String(seconds).padStart(2, '0')}}`;
      return `${{String(hours).padStart(2, '0')}}:${{String(minutes).padStart(2, '0')}}:${{String(seconds).padStart(2, '0')}}`;
    }}
    function updateUptime() {{
      uptimeEl.textContent = formatElapsedWhole((Date.now() - startedAt) / 1000);
    }}
    updateUptime();
    setInterval(updateUptime, 1000);

    const filter = document.getElementById('filter');
    const list = document.getElementById('fileList');
    const form = document.getElementById('uploadForm');
    const fileInput = document.getElementById('fileInput');
    const dropzone = document.getElementById('dropzone');
    const dropText = document.getElementById('dropText');
    const selectedCount = document.getElementById('selectedCount');
    const submitBtn = document.getElementById('submitBtn');
    const status = document.getElementById('status');
    const progressWrap = document.getElementById('progressWrap');
    const progress = document.getElementById('progress');
    const progressText = document.getElementById('progressText');
    const progressBytes = document.getElementById('progressBytes');


    function updateSelected() {{
      const n = fileInput.files.length;
      selectedCount.textContent = n ? n + ' archivo(s) seleccionado(s)' : 'Ningún archivo seleccionado';
      dropText.textContent = n ? n + ' archivo(s) listos para subir' : 'Arrastra aquí o haz clic para elegir archivos';
    }}

    filter?.addEventListener('input', () => {{
      list.querySelectorAll('li').forEach(li => {{
        li.style.display = !filter.value.trim() || li.textContent.toLowerCase().includes(filter.value.toLowerCase()) ? '' : 'none';
      }});
    }});

    dropzone.addEventListener('dragover', e => {{ e.preventDefault(); dropzone.classList.add('dragover'); }});
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', e => {{
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (e.dataTransfer.files.length) {{
        fileInput.files = e.dataTransfer.files;
        updateSelected();
        form.dispatchEvent(new Event('submit', {{ cancelable: true, bubbles: true }}));
      }}
    }});
    fileInput.addEventListener('change', updateSelected);

    form.addEventListener('submit', (event) => {{
      event.preventDefault();
      if (submitBtn.disabled) return;
      const files = fileInput.files;
      if (!files || !files.length) {{
        if (status) status.textContent = 'Selecciona al menos un archivo.';
        return;
      }}
      submitBtn.disabled = true;
      submitBtn.textContent = 'Subiendo...';
      if (status) status.textContent = 'Subiendo archivo(s)...';
      progressWrap.hidden = false;
      progress.value = 0;
      progressText.textContent = '0%';
      progressBytes.textContent = '0 B / 0 B';

      const xhr = new XMLHttpRequest();
      xhr.open('POST', form.action, true);
      xhr.upload.onprogress = (event) => {{
        if (event.lengthComputable) {{
          const percent = Math.round((event.loaded / event.total) * 100);
          progress.value = percent;
          progressText.textContent = percent + '%';
          progressBytes.textContent = bytesLabel(event.loaded) + ' / ' + bytesLabel(event.total);
        }}
      }};
      xhr.onload = () => {{
        if (xhr.status >= 200 && xhr.status < 400) {{
          window.location.assign(xhr.responseURL || ('/?path=' + encodeURIComponent(document.querySelector('input[name=current_path]').value) + '&uploaded=1'));
        }} else {{
          resetBusy('Subida fallida: ' + (xhr.statusText || xhr.status));
          if (status) status.textContent = 'Error al subir (HTTP ' + xhr.status + ').';
        }}
      }};
      xhr.onerror = () => resetBusy('Error de red durante la subida.');
      xhr.send(new FormData(form));
    }});

    function resetBusy(message) {{
      submitBtn.disabled = false;
      submitBtn.textContent = 'Subir';
      if (status) status.textContent = message;
    }}
    function bytesLabel(bytes) {{
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KiB';
      return (bytes / (1024 * 1024)).toFixed(1) + ' MiB';
    }}


    const params = new URLSearchParams(location.search);
    if (status && params.get('uploaded') === '1') {{
      status.textContent = 'Subida completada.';
      setTimeout(() => status.textContent = '', 4000);
    }}
  </script>
</body>
</html>""".encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, params):
        raw_path = params.get("path", [params.get("name", [""])[0]])[0]
        relative = safe_relative(raw_path, self.share_dir)
        if relative is None:
            self.send_error(403, "Ruta no permitida")
            return

        path = self.share_dir if not relative else self.share_dir / relative
        if not path.is_file():
            self.send_error(404, "No encontrado")
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

    def get_write_token(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return ""
        # Token may come from headers or multipart. For simplicity, check Authorization first.
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):]
        # Parse query as fallback token=...
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        token = query.get("token", [""])[0]
        if token:
            return token
        # Multipart token is resolved later by receive_files; here empty means unresolved.
        return ""

    def receive_files(self):
        url = urllib.parse.urlsplit(self.path)
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

        if getattr(self.server, "write_token", ""):
            candidate = supplied_token or self.get_write_token()
            if candidate != self.server.write_token:
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
            "script-src 'self' 'unsafe-inline'; img-src 'self' data:;",
        )
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
