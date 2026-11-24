"""Bounded, acknowledged raw-frame writes to a job-owned encoder.

The producer cannot request another frame until the current write completes.
The pipe and FFmpeg's one-packet input queue apply backpressure; no movie-sized
Python queue exists. Native codec lookahead/reference buffers remain additional
memory and are measured separately by the resource harness.
"""
from collections import deque
import os
import re
from queue import Empty, Full, Queue
import signal
import subprocess
import sys
from threading import Event, Thread
from time import monotonic

import numpy as np

_IO_TIMEOUT = 60
_STDERR_LIMIT = 65536


class FrameEncoder:
    def __init__(self, command, *, own_process_group=True):
        self.command = command
        self.own_process_group = own_process_group
        self.process = None
        self._pending = Queue(maxsize=1)
        self._errors = deque(maxlen=2)
        self._tail = deque(maxlen=8)
        self._last_activity = monotonic()
        self._writer = self._reader = None
        self.closed = False

    def __enter__(self):
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, bufsize=0,
            start_new_session=os.name == 'posix' and self.own_process_group)
        def read():
            partial = b''
            positions = {}
            try:
                while chunk := self.process.stderr.read(8192):
                    self._tail.append(chunk)
                    partial = (partial+chunk)[-16384:]
                    for match in re.finditer(rb'(?:^|\n)(frame|out_time_us)=(-?[0-9]+)\r?\n', partial):
                        key, value = match[1], int(match[2])
                        if key not in positions or value > positions[key]:
                            positions[key] = value
                            self._last_activity = monotonic()
                    partial = partial.rsplit(b'\n', 1)[-1][-8192:]
            except (OSError, ValueError) as exc:
                if not self.closed:
                    self._errors.append(exc)
        def write():
            while True:
                item = self._pending.get()
                if item is None:
                    return
                data, completed = item
                try:
                    view = memoryview(data).cast('B')
                    while view:
                        count = self.process.stdin.write(view)
                        if not count:
                            raise BrokenPipeError('encoder closed its input')
                        view = view[count:]
                except (OSError, ValueError) as exc:
                    self._errors.append(exc)
                finally:
                    # Release the frame before acknowledging consumption.
                    del data, item
                    view = None
                    completed.set()
        self._reader = Thread(target=read, daemon=True)
        self._writer = Thread(target=write, daemon=True)
        try:
            self._reader.start(); self._writer.start()
        except BaseException:
            self.close()
            raise
        return self

    def _failure(self, reason):
        detail = b''.join(self._tail)[-_STDERR_LIMIT:].decode('utf-8', errors='replace').strip()
        return RuntimeError(f'ffmpeg frame stream {reason}' + (f': {detail}' if detail else ''))

    def write(self, array):
        if self.closed:
            raise RuntimeError('frame encoder is closed')
        completed = Event()
        self._pending.put_nowait((array, completed))
        if not completed.wait(_IO_TIMEOUT):
            raise self._failure(f'write timed out after {_IO_TIMEOUT}s')
        if self._errors or self.process.poll() not in (None, 0):
            # The error can arrive just after a broken pipe; let the bounded
            # stderr reader retain the encoder's actual explanation.
            self._reader.join(timeout=.1)
            raise self._failure('failed')

    def finish(self):
        self.process.stdin.close()
        self._last_activity = monotonic()
        while self.process.poll() is None:
            try:
                self.process.wait(timeout=.1)
            except subprocess.TimeoutExpired:
                if monotonic()-self._last_activity > _IO_TIMEOUT:
                    raise self._failure(f'finish timed out after {_IO_TIMEOUT}s without progress')
        self._reader.join(timeout=1)
        if self.process.returncode or self._errors:
            raise self._failure('failed')

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process is None:
            return
        try:
            if os.name == 'posix' and self.own_process_group:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    if self.process.poll() is None:
                        self.process.kill()
            elif self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
        finally:
            # Killing the owned group unblocks a writer even when an encoder
            # child inherited the read end. Never wait on a locked buffered pipe.
            if self._writer is not None and self._writer.ident is not None:
                try:
                    self._pending.put_nowait(None)
                except Full:
                    try:
                        abandoned = self._pending.get_nowait()
                        if abandoned is not None:
                            abandoned[1].set()
                    except Empty:
                        pass
                    self._pending.put_nowait(None)
                self._writer.join(timeout=2)
            for pipe in (self.process.stdin, self.process.stderr):
                pipe.close()
            if self._reader is not None and self._reader.ident is not None:
                self._reader.join(timeout=2)
        if (self._writer and self._writer.is_alive()) or (self._reader and self._reader.is_alive()):
            raise RuntimeError('frame encoder I/O thread did not stop')

    def __exit__(self, kind, error, traceback):
        try:
            self.close()
        except Exception:
            if kind is None:
                raise


def encode_frames(command, frames, canvas, progress_cb=None):
    from av import VideoFrame
    width, height = canvas
    iterator = iter(frames)
    try:
        with FrameEncoder(command) as encoder:
            last_progress = 0
            count = 0
            for index, frame in enumerate(iterator):
                if not isinstance(frame, VideoFrame):
                    raise ValueError('frame stream requires VideoFrame pixels')
                if (frame.width, frame.height) != (width, height):
                    raise ValueError('stream frame dimensions do not match canvas')
                array = np.ascontiguousarray(frame.to_ndarray(format='rgb24'))
                encoder.write(array)
                count += 1
                del array, frame
                if progress_cb and monotonic()-last_progress >= 1:
                    progress_cb(f'submitted streaming frame {index+1}')
                    last_progress = monotonic()
            if not count:
                raise ValueError('frame stream is empty')
            encoder.finish()
    finally:
        primary = sys.exc_info()[0] is not None
        close = getattr(iterator, 'close', None)
        if close is not None:
            try:
                close()
            except Exception:
                if not primary:
                    raise
