"""One local, read-only review page; one active render and one latest request.

Rendered MP4 and opt-in native playback share request fencing and cleanup.
Project edits and undo continue to use the existing CLI and revision journal.
"""

from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import hashlib
import mimetypes
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
from urllib.parse import parse_qs, urlsplit

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


def _input_paths(root, *documents):
    paths = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "path" and isinstance(item, str):
                    path = root / item
                    # Watch the declared alias too: a symlink can be retargeted
                    # while its previous resolved target stays unchanged.
                    paths.update((str(path.absolute()), str(path.resolve())))
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    for document in documents:
        collect(document)
    return sorted(paths)


def _file_stamps(paths):
    result = []
    for path in paths:
        try:
            stat = Path(path).stat()
            result.append((str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except FileNotFoundError:
            result.append((str(path), None))
    return result


def _input_identity(inputs):
    return hashlib.sha256(json.dumps(inputs).encode()).hexdigest()


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
    # Rendering consumes the current edit, never undo payloads. Keep history
    # on disk; omit it from dependency scans, queue copies and worker packets.
    spec = {**spec, "revisions": []}
    timeline = resolve_project(project, spec)
    if not 0 <= start < end <= timeline.duration_s:
        raise ValueError(f"Preview range must be within 0–{timeline.duration_s:g} seconds; restart with a new range.")
    canvas = timeline.canvas
    width, height = canvas["width"], canvas["height"]
    factor = min(1, 1280 / max(width, height), 720 / min(width, height))
    profile = {**PROFILE, "width": max(2, int(width * factor) // 2 * 2),
               "height": max(2, int(height * factor) // 2 * 2), "fps": timeline.output_fps}
    inputs = _file_stamps(_input_paths(root, spec, project))
    return {"state_id": spatial_state_id(spec), "input_identity": _input_identity(inputs),
            "root": str(root), "spec": spec,
            "project": project, "range": {"from": start, "to": end},
            "profile": profile, "logical_canvas": canvas}


def _profile_snapshot(snapshot, mode):
    if mode == "native":
        from moviestar.native_preview import PROFILE as NATIVE_PROFILE
        snapshot["profile"] = {**NATIVE_PROFILE, **snapshot["logical_canvas"],
                               "fps": snapshot["profile"]["fps"]}
    return snapshot


class PreviewSession:
    """Foreground session that watches durable edits, including undo.

    Each worker owns a POSIX process group including its browser and FFmpeg.
    Obsolete work gets SIGINT for cleanup, then SIGKILL after a six-second grace
    period. A new worker starts only after that group has been stopped.
    """

    def __init__(self, root, start, end, directory, *, mode="rendered"):
        self.root, self.start, self.end = Path(root), start, end
        self.directory = Path(directory)
        self.mode = mode
        self.assets = {}
        self.queue = PreviewQueue()
        self.process = None
        self.active = None
        self.cancel_at = None
        self.identity = None
        self.watched_paths = ()
        self.watched_stamps = None
        self.log = None
        self.stopped = threading.Event()

    def refresh(self):
        from moviestar.project import get_project_dir, PROJECT_FILE
        from moviestar.resolved import ResolvedProjectError
        from moviestar.spec import SPEC_FILE
        try:
            if self.watched_paths and _file_stamps(self.watched_paths) == self.watched_stamps:
                return
            self.watched_stamps = None
            directory = get_project_dir(self.root)
            documents = [directory / SPEC_FILE, directory / PROJECT_FILE]
            before = _file_stamps(documents)
            snapshot = _profile_snapshot(read_snapshot(self.root, self.start, self.end), self.mode)
            if "spec" in snapshot and "project" in snapshot:
                paths = _input_paths(self.root, snapshot["spec"], snapshot["project"])
                after = _file_stamps([*documents, *paths])
                # Never let a concurrent save or asset replacement hide behind
                # the idle guard. Retry next tick unless this read was stable.
                if before == after[:2] and _input_identity(after[2:]) == snapshot["input_identity"]:
                    self.watched_paths, self.watched_stamps = [*documents, *paths], after
            identity = json.dumps(snapshot, sort_keys=True)
            if identity != self.identity:
                self.identity = identity
                self.queue.submit(snapshot)
        except (ValueError, OSError, RuntimeError, ResolvedProjectError) as exc:
            self.watched_stamps = None
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
            media = folder / ("preview.json" if self.mode == "native" else "preview.mp4")
            error = None
            if code or not media.exists():
                error = (folder / "worker.log").read_text(errors="replace")[-2000:] or "Preview generation stopped."
            with self.queue.lock:
                assets = {}
                if not error and self.mode == "native":
                    try:
                        assets = json.loads((folder / "assets.json").read_text())
                    except (ValueError, OSError) as exc:
                        error = f"Native preview assets unavailable: {exc}"
                published = self.queue.finish(request_id, media=str(media) if media.exists() else None, error=error)
                if published:
                    self.assets.update(assets)
            self.process = self.active = self.cancel_at = None
            # All intermediates are session-owned and disposable; retain only completed media.
            for path in folder.iterdir():
                if path != media or not published:
                    if path.is_dir():
                        import shutil
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            if not published:
                folder.rmdir()
            self.queue.prune()
            self.prune_assets()
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
                    [sys.executable, "-m", "moviestar.native_preview" if self.mode == "native" else "moviestar.preview_worker", str(folder / "request.json")],
                    cwd=self.root, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True,
                    env={**os.environ, "TMPDIR": str(temporary)})
            except OSError as exc:
                self.log.close()
                self.queue.finish(request["request_id"], error=str(exc))
                self.active = None
                import shutil
                shutil.rmtree(folder)
                self.queue.prune()
                self.prune_assets()

    def prune_assets(self):
        if self.mode != "native":
            return
        with self.queue.lock:
            referenced = set()
            for item in self.queue.requests.values():
                if item.get("media"):
                    packet = json.loads(Path(item["media"]).read_text())
                    referenced.update(media["asset"] for media in packet["media"])
            self.assets = {key: value for key, value in self.assets.items() if key in referenced}

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

    def __init__(self, queue, *, token, port, mode="rendered", session=None):
        self.queue, self.token = queue, token
        self.mode, self.session = mode, session
        self.native_video_delivery = {"requests": 0, "active_requests": 0, "bytes_written": 0, "validated_heads": 0}
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
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._allowed():
            self._reply(403)
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        prefix = f"/projects/{self.server.token}/"
        if not path.startswith(prefix):
            self._reply(404)
            return
        route = path[len(prefix):]
        if route == "":
            page = files("moviestar").joinpath("preview_viewer.html").read_text()
            page = page.replace('data-preview-mode="rendered"', f'data-preview-mode="{self.server.mode}"')
            self._reply(200, page.encode(), "text/html; charset=utf-8")
        elif self.server.mode == "native" and route in {
                "preview_native.mjs", "preview_native_stage.mjs", "preview_native_meter.mjs", "spatial_dom.mjs",
                "preview_native_worker.mjs", "preview_native_buffer.mjs", "preview_native_cuts.mjs",
                "preview_native_cut_meter.mjs"}:
            self._reply(200, files("moviestar").joinpath(route).read_bytes(), "text/javascript")
        elif self.server.mode == "native" and route == "stage":
            self._reply(200, b'<!doctype html><html><head><meta charset="utf-8"></head><body><script type="module" src="preview_native_stage.mjs"></script></body></html>', "text/html; charset=utf-8")
        elif self.server.mode == "native" and route == "packet":
            query = parse_qs(parsed.query, keep_blank_values=True)
            key = query.get("request", [""])[0]
            request = self.server.queue.get(key)
            try:
                if not request or not request.get("media"):
                    raise FileNotFoundError()
                body = Path(request["media"]).read_bytes()
                if "segment" in query:
                    indices = query["segment"]
                    packet = json.loads(body)
                    if (len(indices) != 1 or not re.fullmatch("[0-2]", indices[0])
                            or int(indices[0]) >= len(packet.get("segments", []))):
                        raise FileNotFoundError()
                    child = packet["segments"][int(indices[0])]["packet"]
                    body = json.dumps({**child, "request_id": packet["request_id"],
                        "requested_at": packet["requested_at"], "cut_segment": True}).encode()
                self._reply(200, body)
            except FileNotFoundError:
                self._reply(404)
        elif self.server.mode == "native" and re.fullmatch(r"assets/[a-f0-9]{24}", route):
            with self.server.queue.lock:
                asset = self.server.session.assets.get(route.split("/")[1])
            try:
                if asset is None:
                    raise FileNotFoundError()
                mime = mimetypes.guess_type(asset["path"])[0] or "application/octet-stream"
                self._media(Path(asset["path"]), content_type=mime, expected_identity=asset["identity"])
            except FileNotFoundError:
                self._reply(404)
        elif route in {"fonts/DMSerifDisplay-Regular.ttf", "fonts/Inter-Regular.ttf", "fonts/Inter-Bold.ttf"}:
            self._reply(200, files("moviestar").joinpath(route).read_bytes(), "font/ttf")
        elif route == "state":
            state = self.server.queue.state()
            state["mode"] = self.server.mode
            if self.server.mode == "native":
                with self.server.queue.lock:
                    state["native_video_delivery"] = dict(self.server.native_video_delivery)
            self._reply(200, json.dumps(state).encode())
        elif self.server.mode == "rendered" and re.fullmatch(r"media/[a-f0-9]{24}", route):
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

    def _media(self, path, *, content_type="video/mp4", expected_identity=None):
        with path.open("rb") as media:
            stat = os.fstat(media.fileno())
            identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if expected_identity is not None and identity != tuple(expected_identity):
                self._reply(409)
                return
            size = stat.st_size
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
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            native_video = expected_identity is not None and content_type.startswith("video/")
            # Only the byte-buffer transport opts into private, revalidated
            # memory reuse. Its fetches bypass the browser's HTTP/disk cache.
            memory_buffer = native_video and self.headers.get("X-Moviestar-Source-Buffer") == "memory"
            self.send_header("Cache-Control", "private, no-cache" if memory_buffer else "no-store")
            if native_video:
                self.send_header("Vary", "X-Moviestar-Source-Buffer")
            self.send_header("X-Content-Type-Options", "nosniff")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                if expected_identity is not None and content_type.startswith("video/"):
                    with self.server.queue.lock:
                        self.server.native_video_delivery["validated_heads"] += 1
                return
            media.seek(start)
            remaining = end - start + 1
            count_video = expected_identity is not None and content_type.startswith("video/")
            if count_video:
                with self.server.queue.lock:
                    self.server.native_video_delivery["requests"] += 1
                    self.server.native_video_delivery["active_requests"] += 1
            try:
                while remaining:
                    data = media.read(min(65536, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    if count_video:
                        # Completed body writes accepted by the socket, excluding
                        # headers and any partial write that raises on disconnect.
                        with self.server.queue.lock:
                            self.server.native_video_delivery["bytes_written"] += len(data)
                    remaining -= len(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if count_video:
                    with self.server.queue.lock:
                        self.server.native_video_delivery["active_requests"] -= 1


def serve(root, start, end, *, port, open_browser, ready, mode="rendered"):
    """Keep the service alive in the foreground; closing it stops owned work."""
    if os.name != "posix":
        raise ValueError("The first local preview viewer supports macOS and Linux.")
    if mode not in {"native", "rendered"}:
        raise ValueError("Preview mode must be native or rendered.")
    snapshot = _profile_snapshot(read_snapshot(root, start, end), mode)
    if mode == "native":
        from moviestar.native_preview import compile_snapshot
        compile_snapshot(snapshot)  # Reject unsupported states before opening a port or writing state.
    from moviestar.project import get_project_dir
    state_dir = get_project_dir(root) / "local-preview"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    import fcntl
    with (state_dir / "session.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("A local preview session is already running for this project; use its URL or stop it first.") from exc
        _serve_session(root, start, end, state_dir, port=port, open_browser=open_browser, ready=ready, mode=mode)


def _serve_session(root, start, end, state_dir, *, port, open_browser, ready, mode="rendered"):
    token_file = state_dir / "token"
    if not token_file.exists():
        token_file.write_text(secrets.token_hex(24))
    token = token_file.read_text().strip()
    if not re.fullmatch("[a-f0-9]{48}", token):
        raise ValueError("Invalid local viewer token; remove moviestar/local-preview/token and retry.")
    with tempfile.TemporaryDirectory(prefix="session-", dir=state_dir) as directory:
        session = PreviewSession(root, start, end, directory, mode=mode)
        server = PreviewServer(session.queue, token=token, port=port, mode=mode, session=session)
        worker = threading.Thread(target=session.run, daemon=True)
        previous_terminate = None
        def terminate(_signal, _frame):
            raise KeyboardInterrupt
        if threading.current_thread() is threading.main_thread():
            previous_terminate = signal.signal(signal.SIGTERM, terminate)
        try:
            worker.start()
            ready({"status": "serving", "url": server.url, "range": {"from": start, "to": end},
                   "profile": _profile_snapshot(read_snapshot(root, start, end), mode)["profile"],
                   "mode": mode, "transport": "native-browser" if mode == "native" else "completed-mp4", "updates": "saved-project-edits",
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
            if worker.ident is not None:
                worker.join(timeout=CANCEL_SECONDS + 2)
            if previous_terminate is not None:
                signal.signal(signal.SIGTERM, previous_terminate)
