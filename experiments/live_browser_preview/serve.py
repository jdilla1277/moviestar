"""Local-only live browser feasibility server; the installed preview CLI is unchanged."""

import argparse
from hashlib import sha256
from http.server import ThreadingHTTPServer
from importlib.resources import files
import json
import mimetypes
from pathlib import Path
import re
import secrets
import signal
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

from moviestar.local_preview import PreviewQueue, _Handler, read_snapshot
from packet import compile_snapshot

HERE = Path(__file__).resolve().parent


class LiveSession:
    def __init__(self, root, directory, start=0, end=5):
        self.root, self.directory = Path(root).resolve(), Path(directory)
        self.start, self.end = start, end
        self.queue = PreviewQueue()
        self.identity = None
        self.assets = {}
        self.lock = threading.RLock()
        self.stopped = threading.Event()

    def refresh(self):
        with self.lock:
            try:
                snapshot = read_snapshot(self.root, self.start, self.end)
                identity = sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
                if identity != self.identity:
                    self.identity = identity
                    self.queue.submit(snapshot)
            except (ValueError, OSError, RuntimeError) as exc:
                identity = 'error:' + str(exc)
                if identity != self.identity:
                    self.identity = identity
                    self.queue.reject(str(exc))

    def tick(self):
        self.refresh()
        request = self.queue.take()
        if request is None:
            return
        request_id = request['request_id']
        path = self.directory / (request_id + '.json')
        try:
            packet, assets = compile_snapshot(request)
            packet.update(request_id=request_id, requested_at=request['requested_at'])
            path.write_text(json.dumps(packet, separators=(',', ':')))
            self.refresh()  # A saved edit during compilation supersedes this packet.
            with self.lock:
                if self.queue.finish(request_id, media=str(path)):
                    self.assets.update(assets)
                else:
                    path.unlink(missing_ok=True)
            self.prune_assets()
        except (ValueError, OSError) as exc:
            path.unlink(missing_ok=True)
            self.queue.finish(request_id, error=str(exc))

    def prune_assets(self):
        with self.lock, self.queue.lock:
            active = set()
            for item in self.queue.requests.values():
                if item.get('media'):
                    try:
                        packet = json.loads(Path(item['media']).read_text())
                        active.update(media['asset'] for media in packet['media'])
                    except FileNotFoundError:
                        pass
            self.assets = {key: value for key, value in self.assets.items() if key in active}

    def run(self):
        while not self.stopped.is_set():
            self.tick()
            self.stopped.wait(.05)


class Handler(_Handler):
    def _reply(self, *args, **kwargs):
        try:
            super()._reply(*args, **kwargs)
        except (BrokenPipeError, ConnectionResetError):
            pass  # An obsolete iframe may cancel an in-flight packet response.

    def do_GET(self):
        if not self._allowed():
            self._reply(403)
            return
        parsed = urlsplit(self.path)
        prefix = f'/projects/{self.server.token}/'
        if not parsed.path.startswith(prefix):
            self._reply(404)
            return
        route = parsed.path[len(prefix):]
        session = self.server.session
        if route == '':
            page = files('moviestar').joinpath('preview_viewer.html').read_text().split('<script type="module">')[0]
            page = page.replace('LOCAL PREVIEW', 'LIVE PREVIEW EXPERIMENT')
            page = page.replace('<div id="stage">', '<p><button id="play">Play</button> <button id="pause">Pause</button> <button id="restart">Restart</button> <span id="clock"></span></p><div id="stage">')
            page += '<style>iframe{border:0;position:absolute;inset:0;transform-origin:0 0}iframe.pending{visibility:hidden}button{font:inherit;color:#fff;background:#16307d;border:1px solid #b5c1e166;border-radius:8px;padding:7px 18px;cursor:pointer}</style><script type="module" src="runtime.mjs"></script></body></html>'
            self._reply(200, page.encode(), 'text/html; charset=utf-8')
        elif route == 'stage':
            self._reply(200, b'<!doctype html><html><head><meta charset="utf-8"></head><body><script type="module" src="stage.mjs"></script></body></html>', 'text/html; charset=utf-8')
        elif route in {'runtime.mjs', 'stage.mjs', 'meter.mjs'}:
            self._reply(200, (HERE / route).read_bytes(), 'text/javascript')
        elif route == 'spatial_dom.mjs':
            self._reply(200, files('moviestar').joinpath(route).read_bytes(), 'text/javascript')
        elif route in {'fonts/DMSerifDisplay-Regular.ttf', 'fonts/Inter-Regular.ttf', 'fonts/Inter-Bold.ttf'}:
            self._reply(200, files('moviestar').joinpath(route).read_bytes(), 'font/ttf')
        elif route == 'state':
            session.refresh()
            self._reply(200, json.dumps(session.queue.state()).encode())
        elif route == 'packet':
            key = parse_qs(parsed.query).get('request', [''])[0]
            request = session.queue.get(key)
            try:
                if not request or not request.get('media'):
                    raise FileNotFoundError()
                self._reply(200, Path(request['media']).read_bytes())
            except FileNotFoundError:
                self._reply(404)
        elif re.fullmatch(r'assets/[a-f0-9]{24}', route):
            with session.lock:
                asset = session.assets.get(route.split('/')[1])
            try:
                if asset is None:
                    raise FileNotFoundError()
                mime = mimetypes.guess_type(str(asset['path']))[0] or 'application/octet-stream'
                self._media(asset['path'], content_type=mime, expected_identity=asset['identity'])
            except FileNotFoundError:
                self._reply(404)
        else:
            self._reply(404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--from', dest='start', type=float, default=0)
    parser.add_argument('--to', dest='end', type=float, default=5)
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    # Validate the supported subset before opening a port or browser.
    compile_snapshot(read_snapshot(args.project.resolve(), args.start, args.end))
    with tempfile.TemporaryDirectory(prefix='moviestar-live-') as directory:
        session = LiveSession(args.project, directory, args.start, args.end)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        server.daemon_threads = True
        server.token, server.session = secrets.token_hex(24), session
        worker = threading.Thread(target=session.run, daemon=True)
        worker.start()
        print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}/projects/{server.token}/',
                          'transport': 'experimental-native-browser', 'account': 'not-required'}), flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:
            pass
        finally:
            session.stopped.set()
            worker.join(timeout=5)
            server.server_close()


if __name__ == '__main__':
    main()
