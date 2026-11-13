"""Durable Cloud creation retries, isolated from local editing and credentials."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from moviestar.account import _account_state_path


class CreationStateError(Exception):
    pass


class CreationBusy(CreationStateError):
    pass


def _read(path: Path, title: str) -> dict | None:
    try:
        if path.is_symlink():
            raise CreationStateError("Unsafe creation recovery file")
        with path.open(encoding="utf-8") as stream:
            text = stream.read(4097)
        record = json.loads(text)
        if (len(text) > 4096 or not isinstance(record, dict) or record.get("title") != title
                or record.get("state") not in ("pending", "resolved")
                or not isinstance(record.get("idempotency_key"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", record["idempotency_key"])):
            raise CreationStateError("Invalid creation recovery file")
        return record
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise CreationStateError("Cannot read creation recovery file") from error


def _write(path: Path, record: dict) -> None:
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".creation-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
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
        raise CreationStateError("Cannot save creation recovery file") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class Creation:
    def __init__(self, path: Path, title: str, explicit_key: str | None):
        self.path = path
        record = _read(path, title)
        pending = record if record and record["state"] == "pending" else None
        self.key = explicit_key or (pending["idempotency_key"] if pending else str(uuid4()))
        self.recovering = pending is not None and pending["idempotency_key"] == self.key
        # A deliberate new request must not discard an earlier unknown outcome.
        # Its explicit key remains available to the caller for manual recovery.
        self.managed = pending is None or self.recovering
        self.record = {"title": title, "idempotency_key": self.key, "state": "pending"}

    def save(self):
        if self.managed and not self.recovering:
            _write(self.path, self.record)

    def resolve(self):
        if self.managed:
            _write(self.path, {**self.record, "state": "resolved"})


@contextmanager
def creation_request(endpoint: str, credential: str, title: str, explicit_key: str | None, *, recovery_directory: str = "cloud-project-requests"):
    if recovery_directory not in ("cloud-project-requests", "cloud-probe-requests"):
        raise CreationStateError("Invalid recovery directory")
    # A new installation credential cannot inherit another account's pending
    # request. Neither the credential nor its hash is stored in the record.
    scope = hashlib.sha256(json.dumps([endpoint, credential]).encode()).hexdigest()
    directory = _account_state_path().parent / recovery_directory / scope
    title_hash = hashlib.sha256(title.encode()).hexdigest()
    fd = None
    acquired = False
    try:
        for path in (directory.parent, directory):
            if path.is_symlink():
                raise CreationStateError("Unsafe creation recovery directory")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)
        fd = os.open(directory / f"{title_hash}.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
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
            raise CreationBusy("Another creation of this title is running") from error
    except OSError as error:
        raise CreationStateError("Creation recovery is unavailable") from error
    else:
        yield Creation(directory / f"{title_hash}.json", title, explicit_key)
    finally:
        if fd is not None:
            if acquired and os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            os.close(fd)
