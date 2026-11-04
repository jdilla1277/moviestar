"""Private, atomic replacement intent records outside editing projects."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from moviestar.account import _account_state_path


class StateError(Exception):
    pass


class StateBusy(StateError):
    pass


def _directory(scope: str) -> Path:
    return _account_state_path().parent / "replacement-requests" / hashlib.sha256(scope.encode()).hexdigest()


def has_records(scope: str) -> bool:
    # Old grant-scoped records may exist even when the stable scope is new.
    return _directory(scope).parent.exists()


def _read(path: Path) -> dict | None:
    try:
        if path.is_symlink():
            raise StateError("Unsafe recovery file")
        with path.open("r", encoding="utf-8") as stream:
            text = stream.read(16385)
        value = json.loads(text)
        if len(text) > 16384 or not isinstance(value, dict):
            raise StateError("Invalid recovery file")
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise StateError("Cannot read recovery state") from error


def _write(path: Path, value: dict) -> None:
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".request-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as error:
        raise StateError("Cannot save recovery state") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Cleanup failure must not mask the write's outcome.


class Requests:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise StateError("Invalid recovery key")
        return self.directory / ("request-" + hashlib.sha256(key.encode()).hexdigest() + ".json")

    def read(self, key: str) -> dict | None:
        value = _read(self._path(key))
        if value is not None and value.get("idempotency_key") != key:
            raise StateError("Recovery key mismatch")
        return value

    def current(self) -> dict | None:
        active = _read(self.directory / "current.json")
        if active is None:
            return None
        value = self.read(active.get("key"))
        if value is None:
            raise StateError("Missing active recovery record")
        return value

    def find(self, replacement_id: str) -> dict | None:
        current = self.current()
        if current and current.get("replacement_id") == replacement_id:
            return current
        for index, path in enumerate(self.directory.glob("request-*.json")):
            if index >= 10000:
                raise StateError("Recovery history is too large")
            value = _read(path)
            if value and value.get("replacement_id") == replacement_id:
                if self._path(value.get("idempotency_key")) != path:
                    raise StateError("Recovery key mismatch")
                return value
        return None

    def legacy(self, video: str) -> list[dict]:
        """Find old active intents whose service cannot be inferred from disk.

        Old directories hashed the service together with an ephemeral grant,
        but never stored the service itself. Never guess their origin or silently
        abandon an attempted request. Explicit reconciliation can adopt them.
        """
        records = []
        for index, directory in enumerate(self.directory.parent.iterdir()):
            if index >= 10000:
                raise StateError("Recovery history is too large")
            if directory == self.directory or not directory.is_dir():
                continue
            if directory.is_symlink():
                raise StateError("Unsafe recovery directory")
            record = Requests(directory).current()
            if not record or record.get("video_id") != video or "service_origin" in record:
                continue
            if record.get("reservation_attempted") is False:
                continue  # No request ever reached the service.
            known = self.read(record.get("idempotency_key"))
            if known is not None:
                binding = ("video_id", "idempotency_key", "filename", "size_bytes", "sha256", "expected_version")
                if (any(known.get(field) != record.get(field) for field in binding)
                        or (record.get("replacement_id") is not None and known.get("replacement_id") != record["replacement_id"])):
                    raise StateError("Conflicting recovery records")
                active = self.current()
                reconciled = known.get("reservation_attempted") is False or (
                    known.get("remote_status") in ("replaced", "replacement_closed", "invalid_video")
                    and known.get("cleanup_pending") is False)
                if reconciled or (active and active.get("idempotency_key") == record["idempotency_key"]):
                    continue  # Adopted and still discoverable, or safely resolved.
                # The request file may have landed before its current pointer.
                # Until explicit reconciliation finishes, never allow a fresh key.
            records.append(record)
        return records

    def save(self, record: dict, *, active: bool = False) -> None:
        _write(self._path(record["idempotency_key"]), record)
        if active:
            _write(self.directory / "current.json", {"key": record["idempotency_key"]})


@contextmanager
def locked_requests(scope: str):
    directory = _directory(scope)
    fd = None
    acquired = False
    try:
        for path in (directory.parent, directory):
            if path.is_symlink():
                raise StateError("Unsafe recovery directory")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)
        fd = os.open(directory / "lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            raise StateBusy("Another replacement command is running") from error
    except OSError as error:
        raise StateError("Recovery state is unavailable") from error
    else:
        yield Requests(directory)
    finally:
        if fd is not None:
            if acquired and os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            os.close(fd)
