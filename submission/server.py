"""Candidate uploads on one port; examiner controls on a separate loopback port."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import uuid
import webbrowser
from . import runner


WEB = Path(__file__).resolve().parent / "web"
TASKS = {"gateway": "推理网关", "frontend": "点餐结算页"}


def now():
    return datetime.now(timezone.utc).isoformat()


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


class State:
    def __init__(self, data, private):
        self.data, self.private = Path(data).resolve(), Path(private).resolve()
        self.data.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.uploads = self.data / "uploads"
        self.uploads.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.admin_token = secrets.token_urlsafe(32)
        config = self.data / "settings.json"
        self.settings = json.loads(config.read_text()) if config.exists() else {"exam_code": secrets.token_hex(3).upper(), "accepting": True}
        self.save_settings()
        self.db_path = self.data / "submissions.sqlite3"
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY, receipt_hash TEXT NOT NULL, candidate TEXT NOT NULL, task TEXT NOT NULL,
                filename TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, summary TEXT, error TEXT, work TEXT)""")
            db.execute("UPDATE submissions SET status='uploaded',error='服务重启，请重新运行测试。' WHERE status IN ('queued','running')")
        self.cancelled = threading.Event()
        self.jobs = queue.Queue()
        self.worker = threading.Thread(target=self.work_loop, daemon=True)
        self.worker.start()
        self.public_port = 0
        self.tunnel_process = None
        self.tunnel_status = "off"
        self.tunnel_url = ""
        self.tunnel_error = ""

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_settings(self):
        path = self.data / "settings.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.settings))
        temporary.chmod(0o600)
        os.replace(temporary, path)

    def get(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ApiError(404, "未找到提交。")
        with self.db() as db:
            row = db.execute("SELECT * FROM submissions WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ApiError(404, "未找到提交。")
        return dict(row)

    @staticmethod
    def view(row):
        return {key: row[key] for key in ("id", "candidate", "task", "filename", "created_at", "status", "started_at", "finished_at", "error")} | {
            "summary": json.loads(row["summary"]) if row["summary"] else None}

    def all(self):
        with self.db() as db:
            return [self.view(dict(row)) for row in db.execute("SELECT * FROM submissions ORDER BY created_at DESC")]

    def accept(self, candidate, task, filename, body):
        if not self.settings["accepting"]:
            raise ApiError(409, "当前已暂停接收提交，请联系面试官。")
        candidate = candidate.strip()
        if not 1 <= len(candidate) <= 50 or any(ord(c) < 32 for c in candidate):
            raise ApiError(400, "请填写1–50字的姓名或面试编号。")
        if task not in TASKS:
            raise ApiError(400, "请选择题目。")
        with self.db() as db:
            if db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] >= 200:
                raise ApiError(429, "提交数量已达本场上限，请联系面试官。")
        identifier, receipt = uuid.uuid4().hex, secrets.token_urlsafe(24)
        path = self.uploads / (identifier + ".zip")
        try:
            path.write_bytes(body)
            runner.validate_zip(path)
            with self.db() as db:
                db.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL)",
                           (identifier, hashlib.sha256(receipt.encode()).hexdigest(), candidate, task,
                            Path(filename.replace("\\", "/")).name[:150], now(), "uploaded"))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"submission": self.view(self.get(identifier)), "receipt": receipt}

    def enqueue(self, identifier):
        with self.lock:
            row = self.get(identifier)
            if row["status"] in ("queued", "running"):
                raise ApiError(409, "这份提交已经在队列中。")
            if not runner.readiness(self.private)[row["task"]]:
                raise ApiError(409, "对应题目的面试官验收包或依赖未就绪。")
            with self.db() as db:
                db.execute("UPDATE submissions SET status='queued',summary=NULL,error=NULL,started_at=NULL,finished_at=NULL WHERE id=?", (identifier,))
            self.jobs.put(identifier)

    def work_loop(self):
        while not self.cancelled.is_set():
            identifier = self.jobs.get()
            if identifier is None:
                return
            work = self.data / "work" / identifier / uuid.uuid4().hex[:10]
            work.mkdir(parents=True)
            try:
                row = self.get(identifier)
                with self.db() as db:
                    db.execute("UPDATE submissions SET status='running',started_at=?,work=? WHERE id=?", (now(), str(work), identifier))
                report = runner.evaluate(self.uploads / (identifier + ".zip"), row["task"], work, self.private, self.cancelled)
                status = "passed" if report["all_passed"] else "failed"
                with self.db() as db:
                    db.execute("UPDATE submissions SET status=?,finished_at=?,summary=? WHERE id=?",
                               (status, now(), json.dumps(report, ensure_ascii=False), identifier))
            except Exception as exc:
                message = str(exc) if isinstance(exc, runner.SubmissionError) else "测试运行异常，请在管理页查看日志。"
                with (work / "test.log").open("a") as log:
                    log.write(f"\n[提交服务] {type(exc).__name__}: {exc}\n")
                with self.db() as db:
                    db.execute("UPDATE submissions SET status='error',finished_at=?,error=? WHERE id=?", (now(), message, identifier))
            finally:
                self.jobs.task_done()

    def start_tunnel(self):
        with self.lock:
            if self.tunnel_process is not None and self.tunnel_process.poll() is None:
                return
            executable = shutil.which("cloudflared")
            if not executable:
                raise ApiError(409, "未找到cloudflared，请先安装，或手动建立Tunnel。")
            self.tunnel_status, self.tunnel_url, self.tunnel_error = "starting", "", ""
            process = subprocess.Popen([executable, "tunnel", "--url", f"http://127.0.0.1:{self.public_port}", "--no-autoupdate"],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, start_new_session=os.name == "posix")
            self.tunnel_process = process

        def monitor():
            with (self.data / "tunnel.log").open("a") as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    with self.lock:
                        if self.tunnel_process is not process:
                            continue
                        if match:
                            self.tunnel_url = match.group(0)
                        if "Registered tunnel connection" in line:
                            self.tunnel_status = "ready"
            code = process.wait()
            with self.lock:
                if self.tunnel_process is process and not self.cancelled.is_set():
                    self.tunnel_status = "error"
                    self.tunnel_error = f"Tunnel已退出（{code}），可重新开启；详情见本地tunnel.log。"

        threading.Thread(target=monitor, daemon=True).start()

    def stop_tunnel(self):
        with self.lock:
            process, self.tunnel_process = self.tunnel_process, None
            self.tunnel_status, self.tunnel_url, self.tunnel_error = "off", "", ""
        if process is not None:
            runner.stop_process(process)
            process.wait(timeout=5)

    def close(self):
        self.cancelled.set()
        self.stop_tunnel()
        self.jobs.put(None)
        self.worker.join(timeout=6)


def make_server(state, port, admin=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, status, data):
            self.send(status, json.dumps(data, ensure_ascii=False).encode())

        def admin_auth(self, mutation=False):
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                raise ApiError(403, "管理页只接受本机地址。")
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:
                raise ApiError(401, "请使用启动时显示的管理入口。")
            token = cookie.get("benchmark_admin")
            if token is None or not hmac.compare_digest(token.value, state.admin_token):
                raise ApiError(401, "请使用启动时显示的管理入口。")
            if mutation and self.headers.get("Origin") != f"http://{host}":
                raise ApiError(403, "请从本机管理页操作。")

        def body(self, limit):
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ApiError(400, "请求大小无效。")
            if not 0 < size <= limit:
                raise ApiError(413, "文件不能超过20MB。" if limit == runner.MAX_UPLOAD else "请求内容过大或为空。")
            self.connection.settimeout(25)
            content = self.rfile.read(size)
            if len(content) != size:
                raise ApiError(400, "上传未完成，请重新提交。")
            return content

        def do_GET(self):
            try:
                self.get_request()
            except ApiError as exc:
                self.json(exc.status, {"error": exc.message})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_HEAD(self):
            self.do_GET()

        def get_request(self):
            url = urlsplit(self.path)
            path, query = url.path, parse_qs(url.query)
            if admin and path == "/" and "token" in query:
                if self.headers.get("Host") not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                    raise ApiError(403, "请使用本机管理入口。")
                if not hmac.compare_digest(query["token"][0], state.admin_token):
                    raise ApiError(401, "管理入口已失效，请使用本次启动显示的链接。")
                self.send(303, b"", headers={"Location": "/", "Set-Cookie": f"benchmark_admin={state.admin_token}; HttpOnly; SameSite=Strict; Path=/"})
                return
            if admin:
                self.admin_auth()
            assets = {"/": "admin.html" if admin else "index.html", "/site.css": "site.css",
                      "/admin.js" if admin else "/app.js": "admin.js" if admin else "app.js"}
            if path in assets:
                name = assets[path]
                kind = "text/html; charset=utf-8" if name.endswith("html") else "text/css; charset=utf-8" if name.endswith("css") else "text/javascript; charset=utf-8"
                self.send(200, (WEB / name).read_bytes(), kind)
                return
            if path == "/api/status":
                if admin:
                    self.json(200, {"exam_code": state.settings["exam_code"], "accepting": state.settings["accepting"],
                                    "ready": runner.readiness(state.private), "public_port": state.public_port,
                                    "tunnel": {"status": state.tunnel_status, "url": state.tunnel_url, "error": state.tunnel_error}})
                else:
                    self.json(200, {"service": "agent-benchmark-submission", "accepting": state.settings["accepting"], "tasks": TASKS})
                return
            if admin and path == "/api/submissions":
                self.json(200, {"submissions": state.all()})
                return
            match = re.fullmatch(r"/api/submissions/([a-f0-9]{32})(?:/(log|archive))?", path)
            if match:
                row = state.get(match.group(1))
                if not admin:
                    if match.group(2):
                        raise ApiError(404, "未找到页面。")
                    supplied = hashlib.sha256(self.headers.get("X-Receipt-Token", "").encode()).hexdigest()
                    if not hmac.compare_digest(supplied, row["receipt_hash"]):
                        raise ApiError(403, "回执无效，请使用提交成功时保存的回执。")
                if match.group(2) == "log":
                    log = Path(row["work"]) / "test.log" if row["work"] else None
                    content = log.read_bytes()[-150000:] if log and log.is_file() else b"No test log yet."
                    self.send(200, content, "text/plain; charset=utf-8")
                elif match.group(2) == "archive":
                    self.send(200, (state.uploads / (row["id"] + ".zip")).read_bytes(), "application/zip",
                              {"Content-Disposition": f'attachment; filename="submission-{row["id"][:8]}.zip"'})
                else:
                    self.json(200, {"submission": state.view(row)})
                return
            raise ApiError(404, "未找到页面。")

        def do_POST(self):
            try:
                self.post_request()
            except ApiError as exc:
                self.json(exc.status, {"error": exc.message})
            except runner.SubmissionError as exc:
                self.json(400, {"error": str(exc)})
            except (ValueError, UnicodeError):
                self.json(400, {"error": "请求内容无效。"})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def post_request(self):
            url = urlsplit(self.path)
            path, query = url.path, parse_qs(url.query)
            if not admin:
                if path != "/api/submit":
                    raise ApiError(404, "未找到页面。")
                if not hmac.compare_digest(self.headers.get("X-Exam-Code", "").encode(), state.settings["exam_code"].encode()):
                    raise ApiError(403, "提交口令不正确，请向面试官确认。")
                get = lambda key, default="": query.get(key, [default])[0]
                result = state.accept(get("candidate"), get("task"), get("filename", "submission.zip"), self.body(runner.MAX_UPLOAD))
                self.json(201, result)
                return
            self.admin_auth(mutation=True)
            if path.startswith("/api/run/"):
                state.enqueue(path.rsplit("/", 1)[1])
                self.json(202, {"queued": True})
            elif path == "/api/tunnel/start":
                state.start_tunnel()
                self.json(202, {"starting": True})
            elif path == "/api/tunnel/stop":
                state.stop_tunnel()
                self.json(200, {"stopped": True})
            elif path == "/api/accepting":
                value = json.loads(self.body(1000)).get("accepting")
                if type(value) is not bool:
                    raise ApiError(400, "状态无效。")
                with state.lock:
                    state.settings["accepting"] = value
                    state.save_settings()
                self.json(200, {"accepting": value})
            else:
                raise ApiError(404, "未找到页面。")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    if not admin:
        state.public_port = server.server_port
    return server


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--admin-port", type=int, default=8788)
    parser.add_argument("--data-dir", type=Path, default=root / ".submission-data")
    parser.add_argument("--private-dir", type=Path, default=Path(os.environ.get("BENCHMARK_PRIVATE_DIR", str(root.parent / "agent-benchmark-private"))))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--tunnel", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("需要Python 3.10或更新版本。")
    runtime_file = args.data_dir.resolve() / "runtime.json"
    if runtime_file.is_file():
        try:
            running = json.loads(runtime_file.read_text())
            os.kill(running["pid"], 0)
            with urlopen(f"http://127.0.0.1:{running['public_port']}/api/status", timeout=1) as response:
                valid = json.loads(response.read()).get("service") == "agent-benchmark-submission"
            if valid:
                print(f"提交服务已在运行。\n本机管理入口：{running['admin_url']}", flush=True)
                if args.tunnel:
                    parsed = urlsplit(running["admin_url"])
                    token = parse_qs(parsed.query)["token"][0]
                    origin = f"http://{parsed.netloc}"
                    request = Request(origin + "/api/tunnel/start", b"{}", {"Cookie": "benchmark_admin=" + token, "Origin": origin})
                    with urlopen(request, timeout=3):
                        pass
                if not args.no_browser:
                    webbrowser.open(running["admin_url"])
                return
        except (OSError, ValueError, KeyError):
            pass
    state = State(args.data_dir, args.private_dir)
    public = admin = None
    try:
        public = make_server(state, args.port)
        admin = make_server(state, args.admin_port, admin=True)
    except OSError as exc:
        if public:
            public.server_close()
        state.close()
        parser.exit(1, f"启动失败：端口被占用或不可用（{exc}）。可用--port与--admin-port指定其他端口。\n")
    for service in (public, admin):
        threading.Thread(target=service.serve_forever, daemon=True).start()
    admin_url = f"http://127.0.0.1:{admin.server_port}/?token={state.admin_token}"
    runtime_file.write_text(json.dumps({"pid": os.getpid(), "public_port": public.server_port, "admin_url": admin_url}))
    runtime_file.chmod(0o600)
    print(f"\n候选人本地入口：http://127.0.0.1:{public.server_port}", flush=True)
    print(f"提交口令：{state.settings['exam_code']}", flush=True)
    print(f"本机管理入口：{admin_url}", flush=True)
    print("管理页可开启Cloudflare外网链接；请只把候选人入口和提交口令发给候选人。", flush=True)
    print("停止服务：按Ctrl+C。提交与报告会保留。\n", flush=True)
    if args.tunnel:
        try:
            state.start_tunnel()
        except ApiError as exc:
            print(exc.message, flush=True)
    if not args.no_browser:
        webbrowser.open(admin_url)
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    try:
        stopped.wait()
    finally:
        state.close()
        for service in (public, admin):
            service.shutdown()
            service.server_close()
        runtime_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
