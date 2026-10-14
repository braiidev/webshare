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




import http.client
import json
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
        self.assertIn("Content-Security-Policy", response.headers)

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
        self.assertIn("data-bs-theme", html)
        self.assertNotIn(self.token, html)

    def test_static_assets_and_csp(self):
        for path, needle in (
            ("/static/css/style.css", "text/css"),
            ("/static/js/main.js", "text/javascript"),
            ("/static/js/ui.js", "text/javascript"),
            ("/static/vendor/bootstrap.min.css", "text/css"),
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


if __name__ == "__main__":
    unittest.main()
