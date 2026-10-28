"""A command-owned browser worker; frames remain independent render requests."""

from __future__ import annotations

from importlib.resources import files
import json
import os
from queue import Empty, Full, Queue
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread

from moviestar.spatial_layout import SpatialLayoutError, _chrome_path

_REQUEST_TIMEOUT = 60
_MAX_RESPONSE = 16 * 1024 * 1024


class BrowserSession:
    """Sequential, lazy Chrome reuse with a deadline for every complete request.

    The command owns the worker, its profile, and its process group. Any failed
    request permanently closes the session: a frame is never silently replayed.
    """

    def __init__(self):
        self._process = None
        self._profile = None
        self._closed = False
        self._stderr = b''
        self._messages = Queue(maxsize=1)
        self._stopping = Event()
        self._lock = Lock()
        self._counter = 0
        self._readers = []
        self._writer = None

    def __enter__(self):
        if self._closed:
            raise SpatialLayoutError('browser session is closed')
        return self

    def __exit__(self, kind, error, traceback):
        self.close(abort=kind is not None)

    def _deliver(self, message):
        while not self._stopping.is_set():
            try:
                self._messages.put(message, timeout=.1)
                return
            except Full:
                continue

    def _read_stdout(self):
        try:
            while True:
                line = self._process.stdout.readline(_MAX_RESPONSE + 1)
                if not line:
                    self._deliver(('error', 'browser session driver closed its output'))
                    return
                if len(line) > _MAX_RESPONSE or not line.endswith(b'\n'):
                    self._deliver(('error', 'browser session returned an invalid or oversized response'))
                    return
                self._deliver(('line', line))
        except (OSError, ValueError) as exc:
            self._deliver(('error', f'browser session output failed: {exc}'))

    def _read_stderr(self):
        try:
            while chunk := self._process.stderr.read1(4096):
                self._stderr = (self._stderr + chunk)[-8192:]
        except (OSError, ValueError):
            pass  # Closing an owned pipe can interrupt its reader.

    def _start(self):
        chrome, node = _chrome_path(), shutil.which('node')
        if chrome is None:
            raise SpatialLayoutError('Chrome or chrome-headless-shell is required; set MOVIESTAR_CHROME_PATH to its executable')
        if node is None:
            raise SpatialLayoutError('Node.js 22 or newer is required for browser layout measurement')
        self._profile = TemporaryDirectory(prefix='moviestar-layout-', ignore_cleanup_errors=True)
        script = str(files('moviestar').joinpath('spatial_browser.mjs'))
        self._process = subprocess.Popen([node, script, chrome, self._profile.name, '--session'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=os.name == 'posix')
        for read in (self._read_stdout, self._read_stderr):
            thread = Thread(target=read, daemon=True)
            self._readers.append(thread)
            thread.start()

    def _kill(self):
        if self._process is None:
            return
        try:
            if os.name == 'posix':
                os.killpg(self._process.pid, signal.SIGKILL)
            elif self._process.poll() is None:
                self._process.kill()
        except (ProcessLookupError, PermissionError):
            # Darwin may report EPERM for an already reaped process group.
            if self._process.poll() is None:
                self._process.kill()

    def close(self, *, abort=False):
        if self._closed:
            return
        self._closed = True
        self._stopping.set()
        process = self._process
        shutdown_error = None
        try:
            if process is not None:
                if abort:
                    self._kill()
                else:
                    # EOF asks Node to stop Chrome and remove its profile.
                    try:
                        process.stdin.close()
                    except (OSError, ValueError):
                        pass
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    if not abort:
                        shutdown_error = 'browser session shutdown timed out'
                    self._kill()
                    process.wait(timeout=1)
                finally:
                    # Reap remaining owned descendants even if Node exited first.
                    self._kill()
                if self._writer is not None:
                    self._writer.join(timeout=1)
                for reader in self._readers:
                    reader.join(timeout=1)
                if process.returncode and not abort:
                    detail = self._stderr.decode('utf-8', errors='replace').strip().splitlines()
                    shutdown_error = shutdown_error or ('browser session shutdown failed: '
                        + (detail[-1] if detail else str(process.returncode)))
        finally:
            if process is not None:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass
            if self._profile is not None:
                self._profile.cleanup()
        if shutdown_error:
            raise SpatialLayoutError(shutdown_error)

    def run(self, request: dict) -> dict:
        if self._closed:
            raise SpatialLayoutError('browser session is closed')
        if not self._lock.acquire(blocking=False):
            raise SpatialLayoutError('browser session already has an active request')
        try:
            if self._process is None:
                self._start()
            self._counter += 1
            payload = (json.dumps({'id': self._counter, 'request': request}) + '\n').encode('utf-8')

            def write():
                try:
                    self._process.stdin.write(payload)
                    self._process.stdin.flush()
                except (OSError, ValueError) as exc:
                    self._deliver(('error', f'browser session input failed: {exc}'))

            # Writing a large asset request must not bypass the render deadline
            # when a stalled worker stops consuming its pipe.
            self._writer = Thread(target=write, daemon=True)
            self._writer.start()
            try:
                kind, value = self._messages.get(timeout=_REQUEST_TIMEOUT)
            except Empty as exc:
                raise SpatialLayoutError(f'browser session timed out after {_REQUEST_TIMEOUT} seconds') from exc
            if kind == 'error':
                raise SpatialLayoutError(value)
            try:
                response = json.loads(value)
            except (ValueError, UnicodeError) as exc:
                raise SpatialLayoutError('browser session returned invalid JSON') from exc
            if (not isinstance(response, dict) or type(response.get('id')) is not int
                    or response['id'] != self._counter):
                raise SpatialLayoutError('browser session returned an unexpected response')
            if 'error' in response:
                raise SpatialLayoutError(str(response['error']))
            if not isinstance(response.get('result'), dict):
                raise SpatialLayoutError('browser session returned an invalid response result')
            self._writer.join(timeout=1)
            return response['result']
        except BaseException as exc:
            self.close(abort=True)
            if isinstance(exc, (SpatialLayoutError, OSError)):
                detail = self._stderr.decode('utf-8', errors='replace').strip().splitlines()
                suffix = f': {detail[-1]}' if detail and 'closed its output' in str(exc) else ''
                raise SpatialLayoutError(f'{exc}{suffix}') from exc
            raise
        finally:
            self._lock.release()
