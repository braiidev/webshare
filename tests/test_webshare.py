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


if __name__ == "__main__":
    unittest.main()
