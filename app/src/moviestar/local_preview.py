"""One local, read-only review page; one active render and one latest request.

This is a completed-MP4 transport, not a faster renderer or a render cache.
Project edits and undo continue to use the existing CLI and revision journal.
"""

from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

POLL_SECONDS = .2
CANCEL_SECONDS = 6.0
RETAINED_REQUESTS = 8
PROFILE = {"name": "review-720p", "max_long_edge": 1280, "max_short_edge": 720,
           "video_codec": "h264", "pixel_format": "yuv420p", "crf": 23,
           "preset": "fast", "audio_codec": "aac", "audio_sample_rate": 48000}


class PreviewQueue:
    """Request identity fences both publication and the browser's selection."""

    def __init__(self):
        self.lock = threading.RLock()
        self.requests = {}
        self.latest = self.active = self.pending = self.displayed = None

    def submit(self, snapshot):
        with self.lock:
            if self.pending:
                self.pending["status"] = "superseded"
                for key in ("spec", "project", "root", "input_identity"):
                    self.pending.pop(key, None)
            request = {**deepcopy(snapshot), "request_id": secrets.token_hex(12),
                       "requested_at": time.time(), "status": "queued"}
            self.requests[request["request_id"]] = request
            self.latest = self.pending = request
            self.prune()
            return deepcopy(request)

    def reject(self, error):
        with self.lock:
            request = self.submit({"state_id": None, "range": None, "profile": PROFILE})
            self.pending = None
            self.latest.update(status="failed", error=str(error))
            return request

    def take(self):
        with self.lock:
            if self.active or not self.pending:
                return None
            self.active, self.pending = self.pending, None
            self.active.update(status="rendering", started_at=time.time())
            return deepcopy(self.active)

    def finish(self, request_id, *, media=None, error=None):
        with self.lock:
            request = self.requests.get(request_id)
            # Ignore duplicate, out-of-order and already retired notifications.
            if not request or request is not self.active:
                return False
            self.active = None
            request["finished_at"] = time.time()
            request["generation_ms"] = round((time.time() - request["started_at"]) * 1000)
            for key in ("spec", "project", "root", "input_identity"):
                request.pop(key, None)
            if request is not self.latest:
                request["status"] = "superseded"
                return False
            if error or media is None:
                request.update(status="failed", error=str(error or "Preview unavailable"))
                return False
            request.update(status="ready", media=str(media))
            self.displayed = request
            return True

    def get(self, request_id):
        with self.lock:
            return deepcopy(self.requests.get(request_id))

    def state(self):
        with self.lock:
            def public(request):
                if request is None:
                    return None
                return {key: deepcopy(value) for key, value in request.items()
                        if key not in {"media", "spec", "project", "root", "input_identity"}}
            return {"latest": public(self.latest), "active": public(self.active),
                    "pending": public(self.pending), "displayed": public(self.displayed),
                    "is_current": self.displayed is not None and self.displayed is self.latest,
                    "history": [public(item) for item in self.requests.values() if item.get("media")]}

    def prune(self):
        with self.lock:
            protected = {id(item) for item in (self.latest, self.active, self.pending, self.displayed)}
            removable = [key for key, item in self.requests.items() if id(item) not in protected]
            for key in removable[:max(0, len(self.requests) - RETAINED_REQUESTS)]:
                request = self.requests.pop(key)
                if request.get("media"):
                    path = Path(request["media"])
                    path.unlink(missing_ok=True)
                    try:
                        path.parent.rmdir()
                    except OSError:
                        pass


def read_snapshot(root, start, end):
    """Pin parsed project state; source files stay in place and must stay unchanged."""
    from moviestar.project import load_project
    from moviestar.resolved import resolve_project
    from moviestar.spatial_model import spatial_state_id
    from moviestar.spec import load_spec

    spec = load_spec(root)
    project = load_project(root)
    if spec is None or not (spec.get("spatial") or {}).get("scenes"):
        raise ValueError("Local preview currently requires a saved spatial scene project.")
    timeline = resolve_project(project, spec)
    if not 0 <= start < end <= timeline.duration_s:
        raise ValueError(f"Preview range must be within 0–{timeline.duration_s:g} seconds; restart with a new range.")
    canvas = timeline.canvas
    width, height = canvas["width"], canvas["height"]
    factor = min(1, 1280 / max(width, height), 720 / min(width, height))
    profile = {**PROFILE, "width": max(2, int(width * factor) // 2 * 2),
               "height": max(2, int(height * factor) // 2 * 2), "fps": timeline.output_fps}
    paths = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "path" and isinstance(item, str):
                    paths.add(str((root / item).resolve()))
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(spec)
    collect(project)
    inputs = []
    for path in sorted(paths):
        try:
            stat = Path(path).stat()
            inputs.append((path, stat.st_size, stat.st_mtime_ns, stat.st_ino))
        except FileNotFoundError:
            inputs.append((path, None))
    input_identity = hashlib.sha256(json.dumps(inputs).encode()).hexdigest()
    return {"state_id": spatial_state_id(spec), "input_identity": input_identity,
            "root": str(root), "spec": spec,
            "project": project, "range": {"from": start, "to": end},
            "profile": profile, "logical_canvas": canvas}


class PreviewSession:
    """Foreground session that watches durable edits, including undo.

    Each worker owns a POSIX process group including its browser and FFmpeg.
    Obsolete work gets SIGINT for cleanup, then SIGKILL after a six-second grace
    period. A new worker starts only after that group has been stopped.
    """

    def __init__(self, root, start, end, directory):
        self.root, self.start, self.end = Path(root), start, end
        self.directory = Path(directory)
        self.queue = PreviewQueue()
        self.process = None
        self.active = None
        self.cancel_at = None
        self.identity = None
        self.log = None
        self.stopped = threading.Event()

    def refresh(self):
        from moviestar.resolved import ResolvedProjectError
        try:
            snapshot = read_snapshot(self.root, self.start, self.end)
            identity = json.dumps(snapshot, sort_keys=True)
            if identity != self.identity:
                self.identity = identity
                self.queue.submit(snapshot)
        except (ValueError, OSError, RuntimeError, ResolvedProjectError) as exc:
            identity = "error:" + str(exc)
            if identity != self.identity:
                self.identity = identity
                self.queue.reject(str(exc))

    def _signal(self, sig):
        # Only a group created by this session's live Popen, never a saved PID.
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can report EPERM for an already reaped process group.
            if self.process.poll() is None:
                self.process.send_signal(sig)

    def tick(self):
        self.refresh()
        if self.process is not None:
            latest = self.queue.state()["latest"]
            obsolete = latest["request_id"] != self.active["request_id"]
            if obsolete and self.cancel_at is None:
                self.cancel_at = time.monotonic()
                self._signal(signal.SIGINT)
            if self.cancel_at is not None and time.monotonic() - self.cancel_at >= CANCEL_SECONDS:
                self._signal(signal.SIGKILL)
            code = self.process.poll()
            if code is None:
                return
            self._signal(signal.SIGKILL)  # Reap owned descendants, even after a successful leader exit.
            self.log.close()
            request_id = self.active["request_id"]
            folder = self.directory / request_id
            media = folder / "preview.mp4"
            error = None
            if code or not media.exists():
                error = (folder / "worker.log").read_text(errors="replace")[-2000:] or "Preview generation stopped."
            published = self.queue.finish(request_id, media=str(media) if media.exists() else None, error=error)
            self.process = self.active = self.cancel_at = None
            # All intermediates are session-owned and disposable; retain only completed media.
            for path in folder.iterdir():
                if path.name != "preview.mp4" or not published:
                    if path.is_dir():
                        import shutil
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            if not published:
                folder.rmdir()
            self.queue.prune()
        request = self.queue.take()
        if request is not None:
            folder = self.directory / request["request_id"]
            folder.mkdir()
            (folder / "request.json").write_text(json.dumps(request))
            temporary = folder / "tmp"
            temporary.mkdir()
            self.log = (folder / "worker.log").open("wb")
            self.active = request
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "moviestar.preview_worker", str(folder / "request.json")],
                    cwd=self.root, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True,
                    env={**os.environ, "TMPDIR": str(temporary)})
            except OSError as exc:
                self.log.close()
                self.queue.finish(request["request_id"], error=str(exc))
                self.active = None

    def run(self):
        try:
            while not self.stopped.is_set():
                self.tick()
                self.stopped.wait(POLL_SECONDS)
        except Exception as exc:
            self.queue.reject(f"Preview session stopped: {exc}")
        finally:
            if self.process:
                self._signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=CANCEL_SECONDS)
                except subprocess.TimeoutExpired:
                    self._signal(signal.SIGKILL)
                    self.process.wait(timeout=1)
                self._signal(signal.SIGKILL)
                self.log.close()


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, queue, *, token, port):
        self.queue, self.token = queue, token
        super().__init__(("127.0.0.1", port), _Handler)
        self.url = f"http://127.0.0.1:{self.server_port}/projects/{token}/"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _allowed(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        return (self.headers.get("Host") == origin.removeprefix("http://")
                and self.headers.get("Origin", origin) == origin
                and self.headers.get("Sec-Fetch-Site") != "cross-site")

    def _reply(self, code, body=b"", content_type="application/json", **headers):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), str(value))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._allowed():
            self._reply(403)
            return
        path = urlsplit(self.path).path
        prefix = f"/projects/{self.server.token}/"
        if not path.startswith(prefix):
            self._reply(404)
            return
        route = path[len(prefix):]
        if route == "":
            self._reply(200, files("moviestar").joinpath("preview_viewer.html").read_bytes(), "text/html; charset=utf-8")
        elif route in {"fonts/DMSerifDisplay-Regular.ttf", "fonts/Inter-Regular.ttf", "fonts/Inter-Bold.ttf"}:
            self._reply(200, files("moviestar").joinpath(route).read_bytes(), "font/ttf")
        elif route == "state":
            self._reply(200, json.dumps(self.server.queue.state()).encode())
        elif re.fullmatch(r"media/[a-f0-9]{24}", route):
            request = self.server.queue.get(route.split("/")[1])
            if not request or not request.get("media") or not Path(request["media"]).is_file():
                self._reply(404)
                return
            try:
                self._media(Path(request["media"]))
            except FileNotFoundError:
                self._reply(404)
        else:
            self._reply(404)

    def _media(self, path):
        with path.open("rb") as media:
            size = os.fstat(media.fileno()).st_size
            start, end, code = 0, size - 1, 200
            requested = self.headers.get("Range")
            if requested:
                match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", requested)
                if match and any(match.groups()):
                    left, right = match.groups()
                    start = int(left) if left else max(0, size - int(right))
                    end = min(size - 1, int(right)) if left and right else size - 1
                else:
                    start = size
                if not 0 <= start <= end < size:
                    self._reply(416, Content_Range=f"bytes */{size}")
                    return
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            media.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    data = media.read(min(65536, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
            except (BrokenPipeError, ConnectionResetError):
                pass


def serve(root, start, end, *, port, open_browser, ready):
    """Keep the service alive in the foreground; closing it stops owned work."""
    if os.name != "posix":
        raise ValueError("The first local preview viewer supports macOS and Linux.")
    read_snapshot(root, start, end)  # Fail before opening a port or writing state.
    from moviestar.project import get_project_dir
    state_dir = get_project_dir(root) / "local-preview"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    import fcntl
    with (state_dir / "session.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("A local preview session is already running for this project; use its URL or stop it first.") from exc
        _serve_session(root, start, end, state_dir, port=port, open_browser=open_browser, ready=ready)


def _serve_session(root, start, end, state_dir, *, port, open_browser, ready):
    token_file = state_dir / "token"
    if not token_file.exists():
        token_file.write_text(secrets.token_hex(24))
    token = token_file.read_text().strip()
    if not re.fullmatch("[a-f0-9]{48}", token):
        raise ValueError("Invalid local viewer token; remove moviestar/local-preview/token and retry.")
    with tempfile.TemporaryDirectory(prefix="session-", dir=state_dir) as directory:
        session = PreviewSession(root, start, end, directory)
        server = PreviewServer(session.queue, token=token, port=port)
        worker = threading.Thread(target=session.run, daemon=True)
        worker.start()
        try:
            ready({"status": "serving", "url": server.url, "range": {"from": start, "to": end},
                   "profile": PROFILE, "transport": "completed-mp4", "updates": "saved-project-edits",
                   "hint": "Keep this process running. Open the URL locally; Ctrl-C stops the viewer and its preview worker."})
            if open_browser:
                import webbrowser
                webbrowser.open(server.url)
            server.serve_forever(poll_interval=POLL_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            session.stopped.set()
            worker.join(timeout=CANCEL_SECONDS + 2)
