"""Bounded I/O and private, content-addressed evidence. No task database."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / ".local/state/ops/execution"


class Blocked(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def reject_sensitive(value):
    """Reject common credential-bearing evidence; never silently alter a packet."""
    forbidden = {"access_token", "refresh_token", "api_key", "apikey", "password", "client_secret", "authorization_header"}
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in forbidden and item:
                raise Blocked("credential-bearing content cannot enter compact execution evidence")
            reject_sensitive(item)
    elif isinstance(value, list):
        for item in value:
            reject_sensitive(item)
    elif isinstance(value, str) and re.search(r"(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9_.-]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.)", value):
        raise Blocked("secret-shaped content cannot enter compact execution evidence")


def command(argv, *, cwd=None, timeout=30, env=None) -> str:
    try:
        p = subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise Blocked(f"{Path(argv[0]).name}: observation unavailable ({type(exc).__name__})") from exc
    if p.returncode:
        # stderr and raw provider output can contain credentials.
        raise Blocked(f"{Path(argv[0]).name}: command failed (exit {p.returncode}); no retry was sent")
    return p.stdout


def json_command(argv, **kwargs):
    try:
        return json.loads(command(argv, **kwargs))
    except ValueError as exc:
        raise Blocked(f"{Path(argv[0]).name}: malformed observation") from exc


def git(path, *args):
    return command(["git", "-C", str(path), *args]).strip()


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value):
        raise Blocked("invalid durable identity")
    return value


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise Blocked("evidence directory is a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise Blocked("evidence directory must be owned by this user with mode 0700")
    return path


def exclusive_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


class Store:
    def __init__(self, root=STATE):
        self.root = Path(root)
        if self.root.exists():
            private_dir(self.root)

    def put(self, value):
        reject_sensitive(value)
        raw = encoded(value)
        return self.put_bytes(raw)

    def put_bytes(self, raw):
        private_dir(self.root)
        sha = digest(raw)
        path = private_dir(self.root / "objects") / (sha + ".blob")
        try:
            exclusive_write(path, raw)
        except FileExistsError:
            if path.is_symlink() or digest(path.read_bytes()) != sha:
                raise Blocked("immutable evidence collision")
        return {"sha256": sha, "path": str(path)}

    def read(self, reference):
        sha = reference["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise Blocked("invalid evidence hash")
        path = self.root / "objects" / (sha + ".blob")
        if path.is_symlink():
            raise Blocked("evidence must not be a symlink")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise Blocked("source evidence unavailable") from exc
        if digest(raw) != sha:
            raise Blocked("source evidence changed")
        return json.loads(raw)

    def event(self, attempt, kind, data):
        private_dir(self.root)
        reject_sensitive(data)
        safe_id(attempt)
        folder = private_dir(self.root / "attempts" / attempt)
        with self.lock("event:" + attempt):
            prior = sorted(folder.glob("*.json"))
            event = {"schema_version": 1, "attempt_id": attempt, "sequence": len(prior),
                     "recorded_at": time.time(), "kind": kind, "data": data,
                     "previous_sha256": digest(prior[-1].read_bytes()) if prior else None}
            from .policy import validate_schema
            validate_schema(event, json.loads((ROOT / "schemas/run-event-v1.json").read_text()))
            exclusive_write(folder / f"{len(prior):06}.json", encoded(event))
        return self.put(event)

    def events(self, attempt):
        safe_id(attempt)
        if (self.root / "attempts").is_symlink() or (self.root / "attempts" / attempt).is_symlink():
            raise Blocked("attempt evidence directory must not be a symlink")
        previous = None
        out = []
        for i, path in enumerate(sorted((self.root / "attempts" / attempt).glob("*.json"))):
            raw = path.read_bytes()
            event = json.loads(raw)
            if path.is_symlink() or event["sequence"] != i or event["previous_sha256"] != previous or event["attempt_id"] != attempt:
                raise Blocked("execution evidence chain is corrupt")
            out.append(event)
            previous = digest(raw)
        return out

    @contextlib.contextmanager
    def lock(self, key):
        private_dir(self.root)
        path = private_dir(self.root / "locks") / digest(key.encode())
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Blocked("competing execution or integration owner") from exc
            yield
        finally:
            os.close(fd)
