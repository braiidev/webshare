import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import webshare


class Paths(unittest.TestCase):
    def test_safe_relative_blocks_escape(self):
        root = Path(tempfile.mkdtemp())
        self.assertIsNone(webshare.safe_relative("../secret", root))
        self.assertEqual(webshare.safe_relative("", root), "")
        self.assertEqual(webshare.safe_relative("a/b", root), "a/b")

    def test_safe_target_rejects_names(self):
        root = Path(tempfile.mkdtemp())
        root.mkdir(exist_ok=True)
        self.assertEqual(webshare.safe_target(root, "../x"), root.resolve() / "x")
        self.assertIsNone(webshare.safe_target(root, "."))
        target = webshare.safe_target(root, "file.txt")
        self.assertEqual(target, root.resolve() / "file.txt")

    def test_unique_path_does_not_overwrite(self):
        root = Path(tempfile.mkdtemp())
        root.mkdir(exist_ok=True)
        first = root / "data.txt"
        first.touch()
        second = webshare.unique_path(root, "data.txt")
        self.assertFalse(second.exists())
        self.assertEqual(second.name, "data_1.txt")

    def test_clean_filename_drops_control_chars(self):
        self.assertEqual(webshare.Handler.clean_filename(b"a\x00b.txt"), "ab.txt")
        self.assertEqual(webshare.Handler.clean_filename(b"a\r\nb.txt"), "ab.txt")
        self.assertEqual(webshare.Handler.clean_filename(b"../evil.txt"), "evil.txt")
        self.assertEqual(webshare.Handler.clean_filename(b"..\\evil.txt"), "evil.txt")

    def test_redact_query_token(self):
        self.assertEqual(
            webshare.redact_query_token("GET /api/list?token=abc123 HTTP/1.1"),
            "GET /api/list?token=<redacted> HTTP/1.1",
        )
        self.assertEqual(
            webshare.redact_query_token("POST /api/x?a=1&token=zz HTTP/1.1"),
            "POST /api/x?a=1&token=<redacted> HTTP/1.1",
        )
        self.assertEqual(
            webshare.redact_query_token("GET /api/list?path=a HTTP/1.1"),
            "GET /api/list?path=a HTTP/1.1",
        )

    def test_server_header_hides_python_version(self):
        self.assertEqual(webshare.Handler.sys_version, "")
        self.assertTrue(webshare.Handler.server_version.startswith("webshare/"))

    def test_scan_directory_caps_and_flags(self):
        root = Path(tempfile.mkdtemp())
        for i in range(5):
            (root / f"f{i}.txt").write_text("x", encoding="utf-8")
        entries, truncated = webshare.scan_directory(root, 3)
        self.assertEqual(len(entries), 3)
        self.assertTrue(truncated)
        entries, truncated = webshare.scan_directory(root, 10)
        self.assertEqual(len(entries), 5)
        self.assertFalse(truncated)




import http.client
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class IntegrationWebShare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8177
        cls.directory = tempfile.mkdtemp(prefix="webshare-test-")
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                cls.directory,
                "--port",
                str(cls.port),
                "--max-upload-mb",
                "1",
                "--allow-anonymous-upload",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                http.client.HTTPConnection("127.0.0.1", cls.port, timeout=0.2).connect()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)

    def http_connection(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def test_status_reports_anonymous_upload(self):
        conn = self.http_connection()
        conn.request("GET", "/api/status")
        response = conn.getresponse()
        status = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertTrue(status["upload_enabled"])
        self.assertTrue(status["anonymous_upload"])

    def test_health_json(self):
        conn = self.http_connection()
        conn.request("GET", "/health")
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn("version", body)

    def test_version_endpoint(self):
        conn = self.http_connection()
        conn.request("GET", "/version")
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertIn("version", body)

    def test_security_headers(self):
        conn = self.http_connection()
        conn.request("GET", "/")
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        csp = response.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("base-uri 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_traversal_download_is_blocked(self):
        conn = self.http_connection()
        conn.request("GET", "/download?path=../secret")
        response = conn.getresponse()
        response.read()
        self.assertIn(response.status, (400, 403, 404))

    def test_upload_small_and_over_limit(self):
        conn = self.http_connection()
        boundary = "testboundary0123456789"
        content = b"hello webshare\n"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="tiny.txt"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("ascii") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        conn.request("POST", "/upload", body=body, headers=headers)
        response = conn.getresponse()
        response.read()
        self.assertIn(response.status, (200, 302, 303))
        uploaded = Path(self.directory, "tiny.txt")
        self.assertTrue(uploaded.exists())

        # 2 MiB exceeds the 1 MiB configured limit.
        conn2 = self.http_connection()
        large_content = b"x" * (2 * 1024 * 1024)
        body2 = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="too-large.bin"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("ascii") + large_content + f"\r\n--{boundary}--\r\n".encode("ascii")
        headers2 = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body2)),
        }
        conn2.request("POST", "/upload", body=body2, headers=headers2)
        response2 = conn2.getresponse()
        response2.read()
        self.assertEqual(response2.status, 413)
        self.assertFalse(Path(self.directory, "too-large.bin").exists())

class ApiIntegrationWebShare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8141
        cls.directory = tempfile.mkdtemp(prefix="webshare-api-test-")
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                cls.directory,
                "--port",
                str(cls.port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                http.client.HTTPConnection("127.0.0.1", cls.port, timeout=0.2).connect()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)

    def test_api_info_status_list_and_download(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/status")
        response = conn.getresponse()
        status = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertEqual(status["status"], "ok")
        # El estado no debe exponer la ruta absoluta del share.
        self.assertIn("root_name", status)
        self.assertNotIn("directory", status)
        self.assertEqual(status["root_name"], Path(self.directory).name)

        payload = b"small API content"
        (Path(self.directory) / "api-smoke.txt").write_bytes(payload)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/info?path=api-smoke.txt")
        response = conn.getresponse()
        info = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertEqual(info["name"], "api-smoke.txt")
        self.assertEqual(info["size"], len(payload))
        self.assertEqual(info["download_url"], "/api/download?path=api-smoke.txt")

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/list?path=.")
        response = conn.getresponse()
        listing = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertTrue(any(item["name"] == "api-smoke.txt" for item in listing["entries"]))
        self.assertFalse(listing["truncated"])

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/download?path=api-smoke.txt")
        response = conn.getresponse()
        downloaded = response.read()
        response.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(downloaded, payload)

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/info?path=../secret")
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "path_forbidden")

    def test_not_found_is_json(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/info?path=no-such-file")
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_crud_disabled_without_token(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/mkdir?path=&name=x")
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "crud_disabled")
        self.assertFalse((Path(self.directory) / "x").exists())

class PathSafety(unittest.TestCase):
    def test_encoded_reversal_path(self):
        root = Path(tempfile.mkdtemp())
        root.mkdir(exist_ok=True)
        self.assertIsNone(webshare.safe_relative("%2e%2e/secret", root))
        self.assertIsNone(webshare.safe_relative("%2e%2e%2f%2e%2e", root))
        self.assertIsNone(webshare.safe_relative("braian\x00file", root))

    def test_reverse_path_in_query(self):
        root = Path(tempfile.mkdtemp())
        root.mkdir(exist_ok=True)
        self.assertIsNone(webshare.safe_relative("a/../../b", root))

    def test_safe_target_rejects_controls_and_travel(self):
        root = Path(tempfile.mkdtemp())
        root.mkdir(exist_ok=True)
        self.assertIsNone(webshare.safe_target(root, ".."))
        self.assertIsNone(webshare.safe_target(root, "file\x00.txt"))
        self.assertEqual(webshare.safe_target(root, "../x"), root.resolve() / "x")
        self.assertEqual(webshare.safe_target(root, "subdir\\file.txt"), root.resolve() / "file.txt")
        self.assertEqual(webshare.safe_target(root, "allowed.txt"), root.resolve() / "allowed.txt")

    def test_sandbox_root_stays_fixed(self):
        root = Path(tempfile.mkdtemp())
        root.mkdir(exist_ok=True)
        self.assertEqual(webshare.safe_relative("/", root), "")
        self.assertEqual(webshare.safe_relative(".", root), "")
        self.assertIsNone(webshare.safe_relative("../../../", root))


class CrudIntegrationWebShare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8142
        cls.directory = tempfile.mkdtemp(prefix="webshare-crud-test-")
        cls.token = "s3cret-token"
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                cls.directory,
                "--port",
                str(cls.port),
                "--token",
                cls.token,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                http.client.HTTPConnection("127.0.0.1", cls.port, timeout=0.2).connect()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)

    def request(self, method, path, token=True):
        headers = {}
        if token is True:
            headers["Authorization"] = f"Bearer {self.token}"
        elif isinstance(token, str):
            headers["Authorization"] = f"Bearer {token}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        body = response.read()
        return response, json.loads(body.decode("utf-8"))

    def test_crud_rejects_before_reading_body(self):
        # La autenticacion ocurre antes de leer el cuerpo: un Content-Length
        # enorme sin token debe fallar rapido en vez de bufferizar el payload.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/mkdir?path=&name=huge")
        conn.putheader("Content-Type", "application/x-www-form-urlencoded")
        conn.putheader("Content-Length", str(200 * 1024 * 1024))
        conn.putheader("Connection", "close")
        conn.endheaders()
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        conn.close()
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "token_invalid")
        self.assertFalse((Path(self.directory) / "huge").exists())

    def test_crud_lifecycle(self):
        response, body = self.request("POST", "/api/mkdir?path=&name=docs")
        self.assertEqual(response.status, 201)
        self.assertEqual(body["created"], "docs")
        self.assertEqual(body["type"], "directory")
        self.assertTrue((Path(self.directory) / "docs").is_dir())

        response, body = self.request("POST", "/api/mkdir?path=&name=docs")
        self.assertEqual(response.status, 409)
        self.assertEqual(body["error"], "resource_exists")

        (Path(self.directory) / "docs" / "hola.txt").write_text("contenido", encoding="utf-8")
        self.assertTrue((Path(self.directory) / "docs" / "hola.txt").is_file())

        response, body = self.request("POST", "/api/rename?path=docs/hola.txt&new_name=chau.txt")
        self.assertEqual(response.status, 200)
        self.assertEqual(body["from"], "docs/hola.txt")
        self.assertEqual(body["to"], "docs/chau.txt")
        self.assertFalse((Path(self.directory) / "docs" / "hola.txt").exists())
        self.assertTrue((Path(self.directory) / "docs" / "chau.txt").exists())

        (Path(self.directory) / "docs" / "otro.txt").touch()
        response, body = self.request("POST", "/api/rename?path=docs/chau.txt&new_name=otro.txt")
        self.assertEqual(response.status, 409)
        self.assertEqual(body["error"], "resource_exists")

        response, body = self.request("DELETE", "/api/delete?path=docs/otro.txt&confirm=nope")
        self.assertEqual(response.status, 400)
        self.assertEqual(body["error"], "confirmation_mismatch")

        response, body = self.request("DELETE", "/api/delete?path=docs&confirm=docs")
        self.assertEqual(response.status, 409)
        self.assertEqual(body["error"], "directory_not_empty")

        response, body = self.request("DELETE", "/api/delete?path=docs/otro.txt&confirm=otro.txt")
        self.assertEqual(response.status, 200)
        self.assertEqual(body["deleted"], "docs/otro.txt")
        response, body = self.request("DELETE", "/api/delete?path=docs/chau.txt&confirm=chau.txt")
        self.assertEqual(response.status, 200)

        response, body = self.request("DELETE", "/api/delete?path=docs&confirm=docs")
        self.assertEqual(response.status, 200)
        self.assertEqual(body["type"], "directory")
        self.assertFalse((Path(self.directory) / "docs").exists())

    def test_token_required_for_crud(self):
        response, body = self.request("POST", "/api/mkdir?path=&name=nope", token=False)
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "token_invalid")
        response, body = self.request("POST", "/api/mkdir?path=&name=nope", token="wrong")
        self.assertEqual(response.status, 403)
        self.assertFalse((Path(self.directory) / "nope").exists())

    def test_delete_root_and_traversal_blocked(self):
        response, body = self.request("DELETE", "/api/delete?path=&confirm=")
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "cannot_delete_root")

        response, body = self.request("POST", "/api/mkdir?path=../outside&name=x")
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "path_forbidden")

        response, body = self.request("DELETE", "/api/delete?path=../secret&confirm=secret")
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "path_forbidden")

    def test_post_delete_alias(self):
        (Path(self.directory) / "alias.txt").write_text("x", encoding="utf-8")
        response, body = self.request("POST", "/api/delete?path=alias.txt&confirm=alias.txt")
        self.assertEqual(response.status, 200)
        self.assertFalse((Path(self.directory) / "alias.txt").exists())

    def test_create_endpoint_removed(self):
        response, body = self.request("POST", "/api/create?path=&name=nope.txt")
        self.assertEqual(response.status, 404)
        self.assertEqual(body["error"], "not_found")
        self.assertFalse((Path(self.directory) / "nope.txt").exists())

    def test_invalid_names_rejected(self):
        for name in ("a/b", "..", "a\\b", "con:punto", "  espacio  "):
            response, body = self.request("POST", f"/api/mkdir?path=&name={urllib.parse.quote(name)}")
            self.assertEqual(response.status, 400, name)
            self.assertEqual(body["error"], "invalid_name", name)

        (Path(self.directory) / "origen.txt").write_text("x", encoding="utf-8")
        response, body = self.request(
            "POST", f"/api/rename?path=origen.txt&new_name={urllib.parse.quote('a/b')}"
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(body["error"], "invalid_name")

    def test_symlinks_rejected_in_delete_and_rename(self):
        real = Path(self.directory) / "real.txt"
        real.write_text("x", encoding="utf-8")
        link = Path(self.directory) / "link.txt"
        link.symlink_to(real)

        response, body = self.request("DELETE", "/api/delete?path=link.txt&confirm=link.txt")
        self.assertEqual(response.status, 400)
        self.assertEqual(body["error"], "symlink_not_allowed")
        self.assertTrue(link.is_symlink())

        response, body = self.request("POST", "/api/rename?path=link.txt&new_name=otro.txt")
        self.assertEqual(response.status, 400)
        self.assertEqual(body["error"], "symlink_not_allowed")

    def test_auth_endpoint(self):
        response, body = self.request("GET", "/api/auth")
        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])

        response, body = self.request("GET", "/api/auth", token=False)
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "token_invalid")

        response, body = self.request("GET", "/api/auth", token="wrong")
        self.assertEqual(response.status, 403)
        self.assertEqual(body["error"], "token_invalid")

    def test_status_reports_crud_flags(self):
        response, body = self.request("GET", "/api/status")
        self.assertEqual(response.status, 200)
        self.assertTrue(body["token_required"])
        self.assertTrue(body["crud_enabled"])
        self.assertFalse(body["read_only"])
        self.assertEqual(body["version"], webshare.__version__)

    def test_static_assets_served(self):
        base = f"http://127.0.0.1:{self.port}"
        for path, content_type in (
            ("/", "text/html"),
            ("/static/css/style.css", "text/css"),
            ("/static/js/main.js", "text/javascript"),
            ("/static/vendor/bootstrap.min.css", "text/css"),
        ):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                self.assertEqual(response.status, 200, path)
                self.assertIn(content_type, response.headers.get("Content-Type", ""), path)

        for attempt in ("/static/../webshare.py", "/static/%2e%2e/webshare.py"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + attempt, timeout=5)
            self.assertEqual(ctx.exception.code, 404, attempt)


class FlowWebShare(unittest.TestCase):
    """Reproduce, por HTTP, el mismo recorrido que hace la interfaz web.

    No usa navegador: simula las llamadas que dispara el JS (clave en
    `X-Webshare-Token`, parámetros por query, multipart en `/upload`).
    """

    @classmethod
    def setUpClass(cls):
        cls.port = 8195
        cls.directory = tempfile.mkdtemp(prefix="webshare-flow-test-")
        cls.token = "clave-de-flujo"
        cls.root = Path(__file__).resolve().parents[1]
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(cls.root / "webshare.py"),
                "--dir",
                cls.directory,
                "--port",
                str(cls.port),
                "--token",
                cls.token,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                http.client.HTTPConnection("127.0.0.1", cls.port, timeout=0.2).connect()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)

    def request(self, method, path, token=None, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        sent = dict(headers or {})
        if token:
            sent["X-Webshare-Token"] = token
        conn.request(method, path, body=body, headers=sent)
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        response_headers = dict(response.getheaders())
        conn.close()
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        return status, response_headers, payload, data

    @staticmethod
    def multipart(current_path, filename, content, token=None):
        boundary = "----webshareFlowBoundary"
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="current_path"\r\n\r\n'
            f"{current_path}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = head + content + tail
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        if token:
            headers["X-Webshare-Token"] = token
        return body, headers

    def test_browser_journey(self):
        # 1. El shell carga y conoce los flags del servidor.
        status, _, _, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        status, _, _, info = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(info["token_required"])
        self.assertTrue(info["crud_enabled"])

        # 2. La UI no puede editar hasta "ingresar la clave".
        status, _, _, error = self.request("GET", "/api/auth")
        self.assertEqual(status, 403)
        self.assertEqual(error["error"], "token_invalid")

        status, _, _, accepted = self.request("GET", "/api/auth", token=self.token)
        self.assertEqual(status, 200)
        self.assertTrue(accepted["ok"])

        # 3. Nueva carpeta.
        status, _, _, created = self.request(
            "POST", "/api/mkdir?path=&name=viaje", token=self.token
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["created"], "viaje")

        status, _, _, listing = self.request("GET", "/api/list?path=viaje")
        self.assertEqual(status, 200)
        self.assertEqual(listing["entries"], [])

        # 4. Subida a la carpeta actual (multipart + token por cabecera).
        content = b"contenido de prueba\n"
        body, headers = self.multipart("viaje", "hola.txt", content, token=self.token)
        status, _, _, _ = self.request("POST", "/upload", body=body, headers=headers)
        self.assertIn(status, (200, 303))
        self.assertTrue((Path(self.directory) / "viaje" / "hola.txt").is_file())

        # 5. La lista ya muestra el archivo y se puede descargar.
        status, _, _, listing = self.request("GET", "/api/list?path=viaje")
        self.assertEqual([item["name"] for item in listing["entries"]], ["hola.txt"])
        status, _, downloaded, _ = self.request("GET", "/api/download?path=viaje/hola.txt")
        self.assertEqual(status, 200)
        self.assertEqual(downloaded, content)

        # 6. Renombrar.
        status, _, _, renamed = self.request(
            "POST", "/api/rename?path=viaje/hola.txt&new_name=chau.txt", token=self.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(renamed["to"], "viaje/chau.txt")
        self.assertTrue((Path(self.directory) / "viaje" / "chau.txt").is_file())
        self.assertFalse((Path(self.directory) / "viaje" / "hola.txt").exists())

        # 7. Borrar el archivo (confirmación por nombre) y luego la carpeta.
        status, _, _, deleted = self.request(
            "DELETE", "/api/delete?path=viaje/chau.txt&confirm=chau.txt", token=self.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(deleted["type"], "file")
        self.assertFalse((Path(self.directory) / "viaje" / "chau.txt").exists())

        status, _, _, deleted = self.request(
            "DELETE", "/api/delete?path=viaje&confirm=viaje", token=self.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(deleted["type"], "directory")
        self.assertFalse((Path(self.directory) / "viaje").exists())

    def test_wrong_key_rejected_everywhere(self):
        status, _, _, error = self.request("GET", "/api/auth", token="clave-mala")
        self.assertEqual(status, 403)
        self.assertEqual(error["error"], "token_invalid")

        status, _, _, error = self.request(
            "POST", "/api/mkdir?path=&name=intruso", token="clave-mala"
        )
        self.assertEqual(status, 403)
        self.assertEqual(error["error"], "token_invalid")
        self.assertFalse((Path(self.directory) / "intruso").exists())

        body, headers = self.multipart("", "intruso.txt", b"x", token="clave-mala")
        status, _, _, _ = self.request("POST", "/upload", body=body, headers=headers)
        self.assertEqual(status, 403)
        self.assertFalse((Path(self.directory) / "intruso.txt").exists())

    def test_shell_references_assets_and_hides_token(self):
        status, headers, payload, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = payload.decode("utf-8")
        self.assertIn("/static/js/main.js", html)
        self.assertIn("/static/css/style.css", html)
        self.assertIn("/static/favicon.svg", html)
        self.assertIn("data-bs-theme", html)
        self.assertNotIn(self.token, html)

    def test_static_assets_and_csp(self):
        for path, needle in (
            ("/static/css/style.css", "text/css"),
            ("/static/js/main.js", "text/javascript"),
            ("/static/js/ui.js", "text/javascript"),
            ("/static/vendor/bootstrap.min.css", "text/css"),
            ("/static/favicon.svg", "image/svg+xml"),
        ):
            status, headers, payload, _ = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(needle, headers.get("Content-Type", ""), path)
            self.assertTrue(payload, path)

        status, headers, _, _ = self.request("GET", "/")
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_static_asset_supports_conditional_get(self):
        status, headers, _, _ = self.request("GET", "/static/css/style.css")
        etag = headers.get("ETag")
        self.assertEqual(status, 200)
        self.assertTrue(etag)
        status, _, _, _ = self.request(
            "GET", "/static/css/style.css", headers={"If-None-Match": etag}
        )
        self.assertEqual(status, 304)

    def test_ui_has_no_emoji(self):
        ranges = [
            (0x1F000, 0x1FAFF),
            (0x2600, 0x26FF),
            (0x2700, 0x27BF),
            (0x2B00, 0x2BFF),
            (0xFE0F, 0xFE0F),
        ]

        def has_emoji(text):
            return sorted({ch for ch in text if any(a <= ord(ch) <= b for a, b in ranges)})

        targets = [self.root / "static" / "index.html", self.root / "static" / "css" / "style.css"]
        targets += sorted((self.root / "static" / "js").glob("*.js"))
        for target in targets:
            found = has_emoji(target.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f"{target.name} contiene emoji: {found}")


class MissingDirectoryWebShare(unittest.TestCase):
    """El directorio de `--dir` se crea al arrancar cuando todavía no existe."""

    def test_missing_directory_is_created(self):
        with tempfile.TemporaryDirectory(prefix="webshare-missing-test-") as base:
            target = Path(base) / "no" / "existe" / "todavia"
            self.assertFalse(target.exists())
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "webshare.py"),
                    "--dir",
                    str(target),
                    "--port",
                    "8196",
                    "--quiet",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(
                            "http://127.0.0.1:8196/api/status", timeout=0.5
                        ) as response:
                            self.assertEqual(response.status, 200)
                        break
                    except (urllib.error.URLError, OSError):
                        time.sleep(0.1)
                else:
                    self.fail("WebShare did not start")

                self.assertTrue(target.is_dir())
                self.assertTrue(target.parent.is_dir())
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


class UploadStreamWebShare(unittest.TestCase):
    """Subida en streaming: orden libre, límite previo y sin temporales colgados."""

    @classmethod
    def setUpClass(cls):
        cls.port = 8197
        cls.directory = tempfile.mkdtemp(prefix="webshare-upload-test-")
        cls.token = "token-de-subida"
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                cls.directory,
                "--port",
                str(cls.port),
                "--token",
                cls.token,
                "--max-upload-mb",
                "1",
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/api/status", timeout=0.5
                ):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)

    @staticmethod
    def build_multipart(parts, boundary):
        body = bytearray()
        for part in parts:
            body += f"--{boundary}\r\n".encode("ascii")
            if part.get("filename") is not None:
                body += (
                    f'Content-Disposition: form-data; name="{part["name"]}"; '
                    f'filename="{part["filename"]}"\r\n'
                    f"Content-Type: application/octet-stream\r\n\r\n"
                ).encode("ascii")
            else:
                body += (
                    f'Content-Disposition: form-data; name="{part["name"]}"\r\n\r\n'
                ).encode("ascii")
            body += part["content"]
            body += b"\r\n"
        body += f"--{boundary}--\r\n".encode("ascii")
        return bytes(body)

    def post_upload(self, body, boundary, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        if token:
            headers["X-Webshare-Token"] = token
        conn.request("POST", "/upload", body=body, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        conn.close()
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        return status, data

    def leftovers(self):
        return sorted(p.name for p in Path(self.directory).glob(".webshare-upload-*"))

    def test_field_order_independent_and_multiple_files(self):
        sub = Path(self.directory) / "sub"
        sub.mkdir()
        boundary = "bnd-order"
        body = self.build_multipart(
            [
                {"name": "file", "filename": "uno.txt", "content": b"uno"},
                {"name": "file", "filename": "dos.txt", "content": b"dos"},
                {"name": "current_path", "content": b"sub"},
            ],
            boundary,
        )
        status, _ = self.post_upload(body, boundary, token=self.token)
        self.assertEqual(status, 303)
        self.assertEqual((sub / "uno.txt").read_bytes(), b"uno")
        self.assertEqual((sub / "dos.txt").read_bytes(), b"dos")
        self.assertEqual(self.leftovers(), [])

    def test_token_as_form_field_without_header(self):
        boundary = "bnd-field"
        body = self.build_multipart(
            [
                {"name": "token", "content": self.token.encode("utf-8")},
                {"name": "file", "filename": "campo.txt", "content": b"x"},
            ],
            boundary,
        )
        status, _ = self.post_upload(body, boundary)
        self.assertEqual(status, 303)
        self.assertTrue((Path(self.directory) / "campo.txt").is_file())

    def test_token_field_after_file_is_rejected_before_spooling(self):
        # Con token configurado, el archivo no se escribe hasta autenticar; un
        # campo token despues del archivo no alcanza a validar.
        boundary = "bnd-late"
        body = self.build_multipart(
            [
                {"name": "file", "filename": "tarde.txt", "content": b"x" * (256 * 1024)},
                {"name": "token", "content": self.token.encode("utf-8")},
            ],
            boundary,
        )
        status, payload = self.post_upload(body, boundary)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "token_invalid")
        self.assertFalse((Path(self.directory) / "tarde.txt").exists())
        self.assertEqual(self.leftovers(), [])

    def test_missing_token_is_rejected_before_spooling(self):
        boundary = "bnd-notoken"
        body = self.build_multipart(
            [{"name": "file", "filename": "colado.txt", "content": b"x" * (256 * 1024)}],
            boundary,
        )
        status, payload = self.post_upload(body, boundary)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "token_invalid")
        self.assertFalse((Path(self.directory) / "colado.txt").exists())
        self.assertEqual(self.leftovers(), [])

    def test_bad_token_cleans_temporary_file(self):
        boundary = "bnd-bad"
        body = self.build_multipart(
            [{"name": "file", "filename": "intruso.txt", "content": b"x"}],
            boundary,
        )
        status, payload = self.post_upload(body, boundary, token="nope")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "token_invalid")
        self.assertFalse((Path(self.directory) / "intruso.txt").exists())
        self.assertEqual(self.leftovers(), [])

    def test_oversized_boundary_is_rejected(self):
        boundary = "b" * 300
        body = self.build_multipart(
            [{"name": "file", "filename": "x.txt", "content": b"x"}],
            boundary,
        )
        status, payload = self.post_upload(body, boundary, token=self.token)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_multipart")
        self.assertFalse((Path(self.directory) / "x.txt").exists())
        self.assertEqual(self.leftovers(), [])

    def test_truncated_multipart_leaves_no_temporary(self):
        boundary = "bnd-trunc"
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="cortado.txt"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("ascii")
        body = head + b"datos sin cierre"
        status, payload = self.post_upload(body, boundary, token=self.token)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_multipart")
        self.assertFalse((Path(self.directory) / "cortado.txt").exists())
        self.assertEqual(self.leftovers(), [])

    def test_missing_boundary_is_json_error(self):
        body = b"no soy multipart"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            "/upload",
            body=body,
            headers={"Content-Type": "text/plain", "Content-Length": str(len(body))},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"], "invalid_multipart")

    def test_oversized_upload_reports_json_and_leaves_nothing(self):
        boundary = "bnd-big"
        body = self.build_multipart(
            [{"name": "file", "filename": "grande.bin", "content": b"x" * (2 * 1024 * 1024)}],
            boundary,
        )
        status, payload = self.post_upload(body, boundary, token=self.token)
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"], "upload_too_large")
        self.assertFalse((Path(self.directory) / "grande.bin").exists())
        self.assertEqual(self.leftovers(), [])

    def test_only_fields_is_rejected_without_files(self):
        boundary = "bnd-fields"
        body = self.build_multipart([{"name": "current_path", "content": b""}], boundary)
        status, payload = self.post_upload(body, boundary, token=self.token)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "no_files")
        self.assertEqual(self.leftovers(), [])


class ReadOnlyWebShare(unittest.TestCase):
    """En solo lectura, la subida responde 403 JSON sin dejar colgado al cliente."""

    @classmethod
    def setUpClass(cls):
        cls.port = 8198
        cls.directory = tempfile.mkdtemp(prefix="webshare-readonly-test-")
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                cls.directory,
                "--port",
                str(cls.port),
                "--read-only",
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/api/status", timeout=0.5
                ):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)

    def test_upload_rejected_as_json(self):
        boundary = "bnd-ro"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="x.txt"\r\n\r\n'
        ).encode("ascii") + b"x" + f"\r\n--{boundary}--\r\n".encode("ascii")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            "/upload",
            body=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"], "read_only")
        self.assertFalse((Path(self.directory) / "x.txt").exists())

    def test_mkdir_rejected_as_json(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/mkdir?path=&name=x")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"], "read_only")


class SymlinkWebShare(unittest.TestCase):
    """Bordes con enlaces simbólicos: nunca se sale del sandbox (`--dir`)."""

    @classmethod
    def setUpClass(cls):
        cls.port = 8199
        cls.token = "symlink-token"
        cls.directory = Path(tempfile.mkdtemp(prefix="webshare-symlink-test-"))
        cls.outside = Path(tempfile.mkdtemp(prefix="webshare-symlink-outside-"))
        (cls.outside / "secret.txt").write_text("SECRET", encoding="utf-8")
        (cls.outside / "secretdir").mkdir()
        (cls.outside / "secretdir" / "deep.txt").write_text("DEEP", encoding="utf-8")
        (cls.directory / "inside.txt").write_text("inside", encoding="utf-8")
        (cls.directory / "escape.txt").symlink_to(cls.outside / "secret.txt")
        (cls.directory / "escape_dir").symlink_to(cls.outside / "secretdir")
        (cls.directory / "internal_link.txt").symlink_to(cls.directory / "inside.txt")
        (cls.directory / "loop").symlink_to(cls.directory / "loop")
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                str(cls.directory),
                "--port",
                str(cls.port),
                "--token",
                cls.token,
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/api/status", timeout=0.5
                ):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        shutil.rmtree(cls.directory, ignore_errors=True)
        shutil.rmtree(cls.outside, ignore_errors=True)

    def request(self, method, path, token=True):
        headers = {"X-Webshare-Token": self.token} if token else {}
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        status = response.status
        conn.close()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = raw
        return status, body

    def test_escaping_symlink_download_blocked(self):
        status, body = self.request("GET", "/api/download?path=escape.txt")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "path_forbidden")

    def test_internal_symlink_download_allowed(self):
        status, raw = self.request("GET", "/api/download?path=internal_link.txt")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"inside")

    def test_escaping_symlink_directory_not_listed(self):
        status, body = self.request("GET", "/api/list?path=escape_dir")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "path_forbidden")

    def test_escaping_symlink_info_blocked(self):
        status, body = self.request("GET", "/api/info?path=escape.txt")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "path_forbidden")

    def test_delete_and_rename_reject_symlinks(self):
        status, body = self.request("DELETE", "/api/delete?path=escape.txt&confirm=escape.txt")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "symlink_not_allowed")
        status, body = self.request("POST", "/api/rename?path=escape.txt&new_name=zz.txt")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "symlink_not_allowed")
        self.assertTrue((self.directory / "escape.txt").is_symlink())
        self.assertTrue((self.outside / "secret.txt").exists())

    def test_delete_through_escaping_symlink_dir_blocked(self):
        status, body = self.request(
            "DELETE", "/api/delete?path=escape_dir/deep.txt&confirm=deep.txt"
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "path_forbidden")
        self.assertTrue((self.outside / "secretdir" / "deep.txt").exists())

    def test_mkdir_through_escaping_symlink_forbidden(self):
        status, body = self.request("POST", "/api/mkdir?path=escape_dir&name=nuevo")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "path_forbidden")
        self.assertFalse((self.outside / "secretdir" / "nuevo").exists())

    def test_upload_through_escaping_symlink_forbidden(self):
        boundary = "sym-up"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="current_path"\r\n\r\nescape_dir\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="up.txt"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + b"x" + f"\r\n--{boundary}--\r\n".encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            "/upload",
            body=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
                "X-Webshare-Token": self.token,
            },
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"], "path_forbidden")
        self.assertFalse((self.outside / "secretdir" / "up.txt").exists())

    def test_symlink_loop_does_not_break_server(self):
        status, _ = self.request("GET", "/api/download?path=loop")
        self.assertIn(status, (400, 403, 404, 500))
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["ready"])


@unittest.skipIf(os.geteuid() == 0, "los permisos de archivo no aplican como root")
class PermissionsWebShare(unittest.TestCase):
    """Permisos denegados producen errores JSON limpios, sin cortar la conexión."""

    @classmethod
    def setUpClass(cls):
        cls.port = 8200
        cls.token = "perm-token"
        cls.directory = Path(tempfile.mkdtemp(prefix="webshare-perm-test-"))
        (cls.directory / "noread.txt").write_text("secret", encoding="utf-8")
        (cls.directory / "noread.txt").chmod(0o000)
        (cls.directory / "noread_dir").mkdir()
        (cls.directory / "noread_dir" / "x.txt").write_text("x", encoding="utf-8")
        (cls.directory / "noread_dir").chmod(0o000)
        cls.ro = cls.directory / "ro"
        cls.ro.mkdir()
        (cls.ro / "keep.txt").write_text("keep", encoding="utf-8")
        cls.ro.chmod(0o555)
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                str(cls.directory),
                "--port",
                str(cls.port),
                "--token",
                cls.token,
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/api/status", timeout=0.5
                ):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        for path, mode in (
            (cls.directory / "noread.txt", 0o644),
            (cls.directory / "noread_dir", 0o755),
            (cls.ro, 0o755),
        ):
            try:
                path.chmod(mode)
            except OSError:
                pass
        shutil.rmtree(cls.directory, ignore_errors=True)

    def request(self, method, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, headers={"X-Webshare-Token": self.token})
        response = conn.getresponse()
        raw = response.read()
        status = response.status
        conn.close()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = raw
        return status, body

    def test_unreadable_file_download_is_json_error(self):
        status, body = self.request("GET", "/api/download?path=noread.txt")
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "read_failed")

    def test_unreadable_directory_listing_is_json_error(self):
        status, body = self.request("GET", "/api/list?path=noread_dir")
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "read_failed")

    def test_mkdir_in_readonly_directory_fails_cleanly(self):
        status, body = self.request("POST", "/api/mkdir?path=ro&name=nuevo")
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "create_failed")
        self.assertFalse((self.ro / "nuevo").exists())

    def test_delete_in_readonly_directory_fails_cleanly(self):
        status, body = self.request("DELETE", "/api/delete?path=ro/keep.txt&confirm=keep.txt")
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "delete_failed")
        self.assertTrue((self.ro / "keep.txt").exists())

    def test_server_survives_permission_errors(self):
        self.request("GET", "/api/list?path=noread_dir")
        self.request("GET", "/api/download?path=noread.txt")
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["ready"])


class SpoolHygieneWebShare(unittest.TestCase):
    """Los temporales internos de subida nunca se exponen por la API."""

    @classmethod
    def setUpClass(cls):
        cls.port = 8201
        cls.token = "spool-token"
        cls.directory = Path(tempfile.mkdtemp(prefix="webshare-spool-test-"))
        (cls.directory / "visible.txt").write_text("v", encoding="utf-8")
        (cls.directory / "sub").mkdir()
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                str(cls.directory),
                "--port",
                str(cls.port),
                "--token",
                cls.token,
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/api/status", timeout=0.5
                ):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError("WebShare did not start")
        # Se crean despues del arranque: el servidor barre los huerfanos al
        # iniciar, asi que estos quedan como temporales "vivos" a ocultar.
        (cls.directory / ".webshare-upload-leftover.part").write_text("x", encoding="utf-8")
        (cls.directory / "sub" / ".webshare-upload-leftover.part").write_text(
            "x", encoding="utf-8"
        )

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        shutil.rmtree(cls.directory, ignore_errors=True)

    def request(self, method, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, headers={"X-Webshare-Token": self.token})
        response = conn.getresponse()
        raw = response.read()
        status = response.status
        conn.close()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = raw
        return status, body

    def test_reserved_name_detection(self):
        self.assertTrue(webshare.is_upload_temp(".webshare-upload-abc.part"))
        self.assertFalse(webshare.is_upload_temp(".webshare-upload-abc.txt"))
        self.assertFalse(webshare.is_upload_temp("abc.part"))

    def test_spool_files_are_hidden_from_listing(self):
        status, body = self.request("GET", "/api/list?path=")
        self.assertEqual(status, 200)
        names = [item["name"] for item in body["entries"]]
        self.assertNotIn(".webshare-upload-leftover.part", names)
        self.assertIn("visible.txt", names)
        self.assertEqual(body["total"], len(names))
        status, body = self.request("GET", "/api/list?path=sub")
        self.assertEqual(status, 200)
        self.assertEqual(body["entries"], [])

    def test_spool_files_are_not_accessible(self):
        reserved = ".webshare-upload-leftover.part"
        cases = (
            ("GET", f"/api/download?path={reserved}"),
            ("GET", f"/api/info?path={reserved}"),
            ("DELETE", f"/api/delete?path={reserved}&confirm={reserved}"),
        )
        for method, path in cases:
            status, body = self.request(method, path)
            self.assertEqual(status, 403, (method, path, body))
            self.assertEqual(body["error"], "path_forbidden")

    def test_reserved_names_cannot_be_created(self):
        status, body = self.request("POST", "/api/mkdir?path=&name=.webshare-upload-x.part")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_name")
        status, body = self.request(
            "POST", "/api/rename?path=visible.txt&new_name=.webshare-upload-x.part"
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_name")

    def test_upload_with_reserved_filename_is_ignored(self):
        boundary = "spool-up"
        payload = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="current_path"\r\n\r\n\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename=".webshare-upload-fake.part"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\ndata\r\n--{boundary}--\r\n"
        ).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            "/upload",
            body=payload,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(payload)),
                "X-Webshare-Token": self.token,
            },
        )
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        conn.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(body["error"], "no_files")
        self.assertFalse((self.directory / ".webshare-upload-fake.part").exists())


class OrphanSweepWebShare(unittest.TestCase):
    """Al arrancar, se borran los temporales de subida huerfanos."""

    @classmethod
    def setUpClass(cls):
        cls.port = 8202
        cls.directory = Path(tempfile.mkdtemp(prefix="webshare-sweep-test-"))
        (cls.directory / "visible.txt").write_text("v", encoding="utf-8")
        (cls.directory / ".webshare-upload-orphan.part").write_text("x", encoding="utf-8")
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                str(cls.directory),
                "--port",
                str(cls.port),
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/api/status", timeout=0.5
                ):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        shutil.rmtree(cls.directory, ignore_errors=True)

    def test_orphan_temp_is_removed_on_startup(self):
        self.assertFalse((self.directory / ".webshare-upload-orphan.part").exists())
        self.assertTrue((self.directory / "visible.txt").exists())

    def test_connection_timeout_configured(self):
        self.assertEqual(webshare.Handler.timeout, webshare.CONNECTION_TIMEOUT)
        self.assertGreater(webshare.MAX_CONNECTIONS, 0)


class UploadDisabledWebShare(unittest.TestCase):
    """Sin token y sin --allow-anonymous-upload, las subidas quedan bloqueadas."""

    @classmethod
    def setUpClass(cls):
        cls.port = 8203
        cls.directory = tempfile.mkdtemp(prefix="webshare-upload-off-")
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                cls.directory,
                "--port",
                str(cls.port),
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/api/status", timeout=0.5
                ):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        shutil.rmtree(cls.directory, ignore_errors=True)

    def test_status_disables_upload(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/status")
        status = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        self.assertFalse(status["upload_enabled"])
        self.assertFalse(status["anonymous_upload"])
        self.assertFalse(status["crud_enabled"])

    def test_upload_returns_upload_disabled(self):
        boundary = "bnd-off"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="x.txt"\r\n\r\n'
        ).encode("ascii") + b"x" + f"\r\n--{boundary}--\r\n".encode("ascii")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            "/upload",
            body=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"], "upload_disabled")
        self.assertFalse((Path(self.directory) / "x.txt").exists())


class HostValidationWebShare(unittest.TestCase):
    """Solo se aceptan Host con IP literal, localhost o --bind (anti DNS rebinding)."""

    @classmethod
    def setUpClass(cls):
        cls.port = 8204
        cls.directory = tempfile.mkdtemp(prefix="webshare-host-")
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "webshare.py"),
                "--dir",
                cls.directory,
                "--port",
                str(cls.port),
                "--quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/api/status", timeout=0.5
                ):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError("WebShare did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        shutil.rmtree(cls.directory, ignore_errors=True)

    def request_with_host(self, host):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("GET", "/api/status", skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host)
        conn.endheaders()
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, payload

    def test_ip_literal_is_accepted(self):
        status, _ = self.request_with_host(f"127.0.0.1:{self.port}")
        self.assertEqual(status, 200)

    def test_localhost_is_accepted(self):
        status, _ = self.request_with_host(f"localhost:{self.port}")
        self.assertEqual(status, 200)

    def test_foreign_domain_is_rejected(self):
        for host in ("evil.example", f"evil.example:{self.port}", "attacker.test:80"):
            status, payload = self.request_with_host(host)
            self.assertEqual(status, 400, host)
            self.assertEqual(json.loads(payload.decode("utf-8"))["error"], "invalid_host")


if __name__ == "__main__":
    unittest.main()
