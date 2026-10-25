"""Authenticated loopback-only review page. No workspace write endpoints."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from scm import Repository, ReviewError, file_diff, list_changes, save_json, state_root

ASSETS = Path(__file__).resolve().parent.parent / "assets"


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, workspace, token, snapshot_id=None):
        self.workspace = workspace
        self.token = token
        self.snapshot_id = snapshot_id
        self.last_access = time.monotonic()
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, data, mime="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'self'; base-uri 'none'; object-src 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        expected_host = "127.0.0.1:" + str(self.server.server_port)
        if self.headers.get("Host") != expected_host:
            self.send(403, {"error": "无效 Host。"})
            return
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + expected_host:
            self.send(403, {"error": "禁止跨站访问。"})
            return
        parsed = urllib.parse.urlsplit(self.path)
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if parsed.path in assets:
            name, mime = assets[parsed.path]
            self.send(200, (ASSETS / name).read_bytes(), mime)
            return
        provided = self.headers.get("Authorization", "")
        if not hmac.compare_digest(provided, "Bearer " + self.server.token):
            self.send(401, {"error": "访问凭证已失效，请从插件重新打开差异页。"})
            return
        self.server.last_access = time.monotonic()
        try:
            query = urllib.parse.parse_qs(parsed.query)
            scm = query.get("scm", ["auto"])[0]
            scope = query.get("scope", ["workspace"])[0]
            snapshot_id = self.server.snapshot_id
            repo = Repository(self.server.workspace, scm)
            if parsed.path == "/api/status":
                self.send(200, list_changes(repo, scope, snapshot_id))
            elif parsed.path == "/api/diff":
                name = query.get("path", [""])[0]
                changes = list_changes(repo, scope, snapshot_id)
                item = next((item for item in changes["files"] if item["path"] == name), None)
                if item is None:
                    raise ReviewError("文件不在当前改动列表中，请刷新。")
                self.send(200, file_diff(repo, item, scope, snapshot_id))
            elif parsed.path == "/api/health":
                self.send(200, {"ok": True, "root": str(repo.root)})
            else:
                self.send(404, {"error": "未找到接口。"})
        except (ReviewError, OSError, ValueError, subprocess.SubprocessError) as exc:
            self.send(400, {"error": str(exc)})


def launch(workspace, snapshot_id=None):
    """Create a separate viewer; token is never passed on the process command line."""
    import time
    import uuid
    job = state_root() / "servers" / (uuid.uuid4().hex + ".json")
    token = secrets.token_urlsafe(32)
    save_json(job, {"workspace": str(Path(workspace).resolve()), "token": token,
                    "snapshotId": snapshot_id})
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(job)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=True,
    )
    for _ in range(100):
        data = json.loads(job.read_text(encoding="utf-8"))
        if "port" in data:
            url = "http://127.0.0.1:" + str(data["port"])
            request = urllib.request.Request(url + "/api/health", headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(request, timeout=5) as response:
                json.load(response)
            return {"url": url + "/#" + token, "pid": process.pid,
                    "snapshotId": snapshot_id, "readOnly": True}
        if process.poll() is not None:
            raise ReviewError("差异服务启动失败。")
        time.sleep(.05)
    process.terminate()
    raise ReviewError("差异服务启动超时。")


if __name__ == "__main__":
    job = Path(sys.argv[1])
    settings = json.loads(job.read_text(encoding="utf-8"))
    server = ReviewServer(settings["workspace"], settings["token"], settings.get("snapshotId"))
    save_json(job, {**settings, "port": server.server_port})
    def expire():
        while time.monotonic() - server.last_access < 1800:
            time.sleep(30)
        server.shutdown()
    threading.Thread(target=expire, daemon=True).start()
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()
        job.unlink(missing_ok=True)
