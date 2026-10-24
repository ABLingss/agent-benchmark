from http.cookiejar import CookieJar
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
import zipfile
from submission.runner import SubmissionError, checked_entries, environment, frontend_summary, unpack, find_project
from submission.server import State, make_server


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as target:
        for name, content in entries:
            target.writestr(name, content)
    return stream.getvalue()


def windows_archive():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as target:
        for name, content in [("张同学\\gateway\\", ""),
                              ("张同学\\gateway\\router.py", "# 中文代码\n"),
                              ("张同学\\gateway\\proxy.py", ""),
                              ("张同学\\gateway\\server.py", "")]:
            entry = zipfile.ZipInfo(name)
            entry.create_system = 0
            entry.external_attr = 0x10 if name.endswith("\\") else 0x20
            target.writestr(entry, content.encode("utf-8"))
    return stream.getvalue()


class ZipValidation(unittest.TestCase):
    def test_windows_zip_with_chinese_paths_and_directory_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "代码.ZIP"
            path.write_bytes(windows_archive())
            unpack(path, root / "unpacked")
            project = find_project(root / "unpacked", "gateway")
            self.assertEqual(project, root / "unpacked/张同学/gateway")
            self.assertEqual((project / "router.py").read_text(), "# 中文代码\n")

    def test_traversal(self):
        for name in ["../outside.py", "/outside.py", "C:/outside.py", "a\\..\\outside.py"]:
            with self.subTest(name=name), zipfile.ZipFile(io.BytesIO(archive([(name, "x")]))) as value:
                with self.assertRaises(SubmissionError):
                    checked_entries(value)

    def test_symlink(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as value:
            entry = zipfile.ZipInfo("link")
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            value.writestr(entry, "/etc/passwd")
        with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as value, self.assertRaises(SubmissionError):
            checked_entries(value)

    def test_dependencies_and_private_files_rejected(self):
        for name in ["project/node_modules/a.js", "project/.git/config", "agent-benchmark-private/test.py", ".submission-data/settings.json"]:
            with self.subTest(name=name), zipfile.ZipFile(io.BytesIO(archive([(name, "x")]))) as value:
                with self.assertRaises(SubmissionError):
                    checked_entries(value)

    def test_safe_unpack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.zip"
            path.write_bytes(archive([("project/gateway/router.py", "hello"), ("__MACOSX/x", "ignore")]))
            unpack(path, root / "unpacked")
            self.assertEqual((root / "unpacked/project/gateway/router.py").read_text(), "hello")
            self.assertFalse((root / "unpacked/__MACOSX").exists())

    def test_environment_does_not_inherit_secrets(self):
        key = "OPENAI_API_KEY"
        previous = os.environ.get(key)
        os.environ[key] = "test-not-a-real-secret"
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = environment(Path(directory))
                self.assertNotIn(key, env)
                self.assertEqual(env["HOME"], str(Path(directory) / "home"))
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    def test_skipped_browser_test_does_not_pass(self):
        report = {"suites": [{"specs": [{"file": "public.spec.cjs", "tests": [
            {"expectedStatus": "passed", "results": [{"status": "skipped"}]}]}]}]}
        result = frontend_summary(report, 0)
        self.assertFalse(result["all_passed"])
        self.assertEqual(result["auto_score"], 0)


class SubmissionHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.state = State(Path(cls.temp.name) / "data", Path(cls.temp.name) / "no-private-tests")
        cls.public = make_server(cls.state, 0)
        cls.admin = make_server(cls.state, 0, admin=True)
        for service in (cls.public, cls.admin):
            threading.Thread(target=service.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.state.close()
        for service in (cls.public, cls.admin):
            service.shutdown()
            service.server_close()
        cls.temp.cleanup()

    def call(self, path, body=None, admin=False, headers=None, authenticated=True):
        service = self.admin if admin else self.public
        origin = f"http://127.0.0.1:{service.server_port}"
        defaults = {}
        if admin and authenticated:
            defaults = {"Cookie": "benchmark_admin=" + self.state.admin_token, "Origin": origin}
        defaults.update(headers or {})
        request = Request(origin + path, body, defaults)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read(), response.headers
        except HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def submit(self):
        query = urlencode({"candidate": "测试同学", "task": "gateway", "filename": "code.zip"})
        status, body, _ = self.call("/api/submit?" + query, archive([("gateway/router.py", "# no execution")] ),
                                    headers={"X-Exam-Code": self.state.settings["exam_code"]})
        self.assertEqual(status, 201, body)
        return json.loads(body)

    def test_upload_is_manual_and_receipt_private(self):
        result = self.submit()
        row = result["submission"]
        self.assertEqual(row["status"], "uploaded")
        self.assertFalse((self.state.data / "work").exists())
        path = "/api/submissions/" + row["id"]
        self.assertEqual(self.call(path)[0], 403)
        self.assertEqual(self.call(path, headers={"X-Receipt-Token": result["receipt"]})[0], 200)
        self.assertEqual(self.call(path + "/archive", headers={"X-Receipt-Token": result["receipt"]})[0], 404)

    def test_windows_upload_and_project_detection(self):
        query = urlencode({"candidate": "Windows 张同学", "task": "gateway", "filename": "C:\\fakepath\\网关代码.ZIP"})
        status, body, _ = self.call("/api/submit?" + query, windows_archive(),
                                   headers={"Content-Type": "application/x-zip-compressed",
                                            "X-Exam-Code": self.state.settings["exam_code"]})
        self.assertEqual(status, 201, body)
        result = json.loads(body)
        self.assertEqual(result["submission"]["filename"], "网关代码.ZIP")
        with tempfile.TemporaryDirectory() as directory:
            unpack(self.state.uploads / (result["submission"]["id"] + ".zip"), Path(directory))
            self.assertEqual(find_project(Path(directory), "gateway").name, "gateway")

    def test_admin_not_available_on_candidate_port(self):
        for path in ["/admin.js", "/api/submissions", "/api/tunnel/start"]:
            self.assertEqual(self.call(path)[0], 404)
        result = self.submit()
        self.assertEqual(self.call("/api/run/" + result["submission"]["id"], b"{}")[0], 404)

    def test_wrong_exam_code(self):
        status, _, _ = self.call("/api/submit?candidate=x&task=gateway", b"abc", headers={"X-Exam-Code": "wrong"})
        self.assertEqual(status, 403)

    def test_invalid_zip(self):
        status, body, _ = self.call("/api/submit?candidate=x&task=gateway", b"not a zip",
                                    headers={"X-Exam-Code": self.state.settings["exam_code"]})
        self.assertEqual(status, 400, body)

    def test_admin_cookie_and_origin_required(self):
        self.assertEqual(self.call("/api/status", admin=True, authenticated=False)[0], 401)
        self.assertEqual(self.call("/api/accepting", b'{"accepting":false}', admin=True,
                                   headers={"Origin": "https://other.invalid"})[0], 403)
        self.assertTrue(self.state.settings["accepting"])

    def test_admin_bootstrap_cookie(self):
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        with opener.open(f"http://127.0.0.1:{self.admin.server_port}/?token={self.state.admin_token}") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("面试官管理台", response.read().decode())

    def test_pause_and_resume(self):
        self.assertEqual(self.call("/api/accepting", b'{"accepting":false}', admin=True)[0], 200)
        try:
            query = urlencode({"candidate":"x", "task":"gateway"})
            status, _, _ = self.call("/api/submit?" + query, archive([("x.py","x")]), headers={"X-Exam-Code":self.state.settings["exam_code"]})
            self.assertEqual(status, 409)
        finally:
            self.call("/api/accepting", b'{"accepting":true}', admin=True)

    def test_missing_private_tests_are_reported(self):
        result = self.submit()
        status, body, _ = self.call("/api/run/" + result["submission"]["id"], b"{}", admin=True)
        self.assertEqual(status, 409, body)

    def test_public_status_does_not_include_exam_code(self):
        status, body, _ = self.call("/api/status")
        self.assertEqual(status, 200)
        self.assertNotIn("exam_code", json.loads(body))


if __name__ == "__main__":
    unittest.main()
