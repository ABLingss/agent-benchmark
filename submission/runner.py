"""Manual, local evaluation. Uses examiner-owned tests, never submitted test scripts."""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile


MAX_UPLOAD = 20 * 1024 * 1024
MAX_EXPANDED = 60 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024


class SubmissionError(ValueError):
    pass


def checked_entries(archive):
    entries = archive.infolist()
    if len(entries) > 1000:
        raise SubmissionError("文件过多，请移除node_modules、.git和缓存后重新压缩。")
    total = 0
    seen = set()
    safe = []
    for entry in entries:
        name = entry.filename.replace("\\", "/")
        parts = name.split("/")
        if name.startswith("/") or any(p == ".." or ":" in p for p in parts) or "\x00" in name:
            raise SubmissionError("压缩包包含非法路径。")
        mode = (entry.external_attr >> 16) & 0o170000
        if mode not in (0, 0o100000, 0o040000):
            raise SubmissionError("不接受符号链接或特殊文件。")
        if any(p in ("node_modules", ".git", ".submission-data", "agent-benchmark-private") for p in parts):
            raise SubmissionError("压缩包不能包含依赖、Git、本机提交数据或面试官验收包，请移除后重新压缩。")
        if any(p in ("__MACOSX", "__pycache__", ".DS_Store") for p in parts):
            continue
        if not name.rstrip("/"):
            continue
        normalized = "/".join(p for p in parts if p and p != ".")
        if normalized in seen:
            raise SubmissionError("压缩包包含重复路径。")
        seen.add(normalized)
        if entry.file_size > MAX_FILE:
            raise SubmissionError("单个文件不能超过8MB。")
        total += entry.file_size
        if total > MAX_EXPANDED:
            raise SubmissionError("解压后大小不能超过60MB。")
        if entry.flag_bits & 1:
            raise SubmissionError("请提交未加密的ZIP文件。")
        safe.append((entry, normalized))
    if not safe:
        raise SubmissionError("压缩包为空。")
    return safe


def validate_zip(path):
    try:
        with zipfile.ZipFile(path) as archive:
            checked_entries(archive)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise SubmissionError("无法读取ZIP，请重新压缩。") from exc


def unpack(path, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for entry, relative in checked_entries(archive):
            output = destination / relative
            if entry.is_dir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, output.open("xb") as target:
                    shutil.copyfileobj(source, target)


def find_project(root, task):
    marker = "router.py" if task == "gateway" else "index.html"
    matches = []
    for entry in root.rglob(marker):
        parent = entry.parent
        required = ("proxy.py", "server.py") if task == "gateway" else ("menu.json",)
        if all((parent / name).is_file() for name in required):
            matches.append(parent)
    if len(matches) != 1:
        hint = "router.py、proxy.py、server.py" if task == "gateway" else "index.html、menu.json"
        raise SubmissionError(f"请提交一份完整项目：需在同一目录找到{hint}；当前找到{len(matches)}份。")
    return matches[0]


def environment(work):
    # Do not pass the host's API keys, GitHub credentials or admin tokens to submissions.
    allowed = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS")
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    home = work / "home"
    temporary = work / "tmp"
    home.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    env.update({"HOME": str(home), "USERPROFILE": str(home), "TMPDIR": str(temporary), "TMP": str(temporary),
                "TEMP": str(temporary), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"})
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    elif sys.platform == "darwin":
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / "Library/Caches/ms-playwright")
    elif sys.platform != "win32":
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / ".cache/ms-playwright")
    else:
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright")
    return env


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def run_command(command, cwd, env, log, timeout, cancelled):
    with log.open("wb") as output:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name == "posix")
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancelled.is_set():
                    raise SubmissionError("服务已停止，本次测试取消。")
                if time.monotonic() > deadline:
                    raise SubmissionError(f"测试超过{timeout}秒，已停止本次测试。")
                if log.stat().st_size > 8 * 1024 * 1024:
                    raise SubmissionError("测试输出超过8MB，已停止本次测试。")
                time.sleep(0.1)
            return process.returncode
        finally:
            stop_process(process)
            process.wait(timeout=5)


@contextmanager
def static_site(project):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def list_directory(self, path):
            self.send_error(404)
            return None

    service = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(project)))
    service.daemon_threads = True
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{service.server_port}"
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=2)


def frontend_summary(report, exit_code):
    groups = {"public": {"group": "公开验收", "passed": 0, "total": 0, "max": 15},
              "hidden": {"group": "隐藏验收", "passed": 0, "total": 0, "max": 35}}

    def visit(suite):
        for spec in suite.get("specs", []):
            group = groups["public" if Path(spec.get("file", "")).name == "public.spec.cjs" else "hidden"]
            for test in spec.get("tests", []):
                group["total"] += 1
                results = test.get("results", [])
                if results and results[-1].get("status") == "passed" and test.get("expectedStatus") == "passed":
                    group["passed"] += 1
        for child in suite.get("suites", []):
            visit(child)

    for suite in report.get("suites", []):
        visit(suite)
    rows = list(groups.values())
    for row in rows:
        row["score"] = round(row["max"] * row["passed"] / row["total"], 2) if row["total"] else 0
    total = sum(r["total"] for r in rows)
    return {"groups": rows, "auto_score": round(sum(r["score"] for r in rows), 2), "auto_max": 50,
            "manual_max": 50, "all_passed": exit_code == 0 and total > 0 and all(r["total"] and r["passed"] == r["total"] for r in rows)}


def readiness(private):
    return {"gateway": (private / "evaluate_gateway.py").is_file(),
            "frontend": (private / "frontend_checks/public.spec.cjs").is_file()
            and (private / "frontend_checks/hidden.spec.cjs").is_file()
            and (private / "node_modules/@playwright/test/cli.js").is_file() and bool(shutil.which("node"))}


def evaluate(archive, task, work, private, cancelled):
    if not readiness(private)[task]:
        raise SubmissionError("面试官验收包或依赖未就绪，请在管理页检查环境。")
    project_root = work / "source"
    unpack(archive, project_root)
    project = find_project(project_root, task)
    env = environment(work)
    log = work / "test.log"
    if task == "gateway":
        result = work / "result.json"
        code = run_command([sys.executable, str(private / "evaluate_gateway.py"), str(project), "--report", str(result)],
                           work, env, log, 90, cancelled)
        if not result.is_file():
            raise SubmissionError("网关验收未生成报告，请在管理页查看测试日志。")
        report = json.loads(result.read_text())
        report["all_passed"] = bool(report.get("all_passed")) and code == 0
    else:
        result = work / "playwright.json"
        config = work / "playwright.config.cjs"
        with static_site(project) as address:
            settings = {"testDir": str(private / "frontend_checks"), "workers": 1, "timeout": 12000,
                        "outputDir": str(work / "browser-artifacts"), "reporter": [["json", {"outputFile": str(result)}]],
                        "use": {"baseURL": address, "headless": True, "viewport": {"width": 1440, "height": 1000}}}
            config.write_text("module.exports=" + json.dumps(settings) + ";\n")
            code = run_command([shutil.which("node"), str(private / "node_modules/@playwright/test/cli.js"),
                                "test", "--config", str(config)], private, env, log, 210, cancelled)
        if not result.is_file():
            raise SubmissionError("浏览器验收未生成报告，请检查Playwright浏览器是否安装，并查看测试日志。")
        raw_report = json.loads(result.read_text())
        def log_suite(suite, output):
            for spec in suite.get("specs", []):
                for test in spec.get("tests", []):
                    results = test.get("results", [])
                    last = results[-1] if results else {}
                    output.write(f"\n[{last.get('status', 'unknown')}] {spec.get('title', '')}\n")
                    for error in last.get("errors", []):
                        output.write(error.get("message", str(error)) + "\n")
            for child in suite.get("suites", []):
                log_suite(child, output)
        with log.open("a") as output:
            for error in raw_report.get("errors", []):
                output.write(error.get("message", str(error)) + "\n")
            for suite in raw_report.get("suites", []):
                log_suite(suite, output)
        report = frontend_summary(raw_report, code)
        (work / "result.json").write_text(json.dumps(report, ensure_ascii=False))
    groups = [{key: row[key] for key in ("group", "passed", "total", "max", "score") if key in row}
              for row in report.get("groups", [])]
    return {"groups": groups, "auto_score": report.get("auto_score", 0),
            "auto_max": report.get("auto_max", 70), "manual_max": report.get("manual_max", 30),
            "all_passed": bool(report.get("all_passed"))}
