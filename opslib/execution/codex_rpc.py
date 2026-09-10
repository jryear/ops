"""Bounded App Server discovery transport. It cannot submit a model turn.

Notifications are preserved in order, including ones arriving before a reply.
Only compact, whitelisted observations may leave this transport for evidence.
"""
from __future__ import annotations

import json
import selectors
import subprocess
import time

from .common import Blocked, encoded

READ_METHODS = frozenset({"initialize", "account/read", "account/rateLimits/read",
                          "model/list", "config/read"})
MAX_FRAME = 2 * 1024 * 1024
MAX_NOTIFICATIONS = 1024


class CodexRPC:
    def __init__(self, executable, cwd, *, overrides=(), env=None, timeout=20):
        argv = [executable, "app-server", "--stdio"]
        for value in overrides:
            argv.extend(["-c", value])
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.buffer = b""
        self.sequence = 0
        self.timeout = timeout
        self.notifications = []

    def notify_initialized(self):
        self._write({"method": "initialized"})

    def _write(self, value):
        try:
            self.process.stdin.write(encoded(value))
        except (OSError, ValueError) as exc:
            raise Blocked("Codex App Server transport closed") from exc

    def _read(self, deadline):
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                if len(raw) > MAX_FRAME:
                    raise Blocked("Codex App Server frame exceeds observation bound")
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError) as exc:
                    raise Blocked("Codex App Server returned malformed JSON") from exc
                if not isinstance(value, dict):
                    raise Blocked("Codex App Server returned a non-object frame")
                return value
            if len(self.buffer) > MAX_FRAME:
                raise Blocked("Codex App Server frame exceeds observation bound")
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                if not selector.select(min(1, max(0, deadline - time.monotonic()))):
                    continue
            chunk = self.process.stdout.read(65536)
            if not chunk:
                raise Blocked("Codex App Server observation unavailable; process exited or transport closed")
            self.buffer += chunk
        raise Blocked("Codex App Server observation timeout")

    def request(self, method, params):
        if method not in READ_METHODS:
            raise Blocked("discovery transport forbids inference and account mutations")
        if method == "account/read" and params.get("refreshToken") is not False:
            raise Blocked("discovery must not request credential refresh")
        self.sequence += 1
        request_id = self.sequence
        self._write({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            value = self._read(deadline)
            if "method" in value:
                if "id" in value:
                    # Discovery never services auth refresh, approvals or tool calls.
                    self._write({"id": value["id"], "error": {"code": -32601,
                                 "message": "Request unsupported by ops discovery"}})
                    raise Blocked("Codex discovery unexpectedly requested a client action")
                if len(self.notifications) >= MAX_NOTIFICATIONS:
                    raise Blocked("Codex notification observation bound exceeded")
                self.notifications.append(value)
                continue
            if value.get("id") != request_id:
                raise Blocked("Codex reply identity mismatch")
            if "error" in value:
                raise Blocked("Codex discovery RPC rejected " + method)
            if "result" not in value:
                raise Blocked("Codex discovery reply lacks a result")
            return value["result"]

    def close(self):
        # Only this probe's process. No process discovery, takeover or global kill.
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()


def compact_notifications(notifications):
    """Never retain messages, tool arguments, auth values or provider transcripts."""
    result = []
    for value in notifications:
        method = value.get("method")
        if not isinstance(method, str):
            raise Blocked("Codex notification method is malformed")
        item = {"method": method}
        params = value.get("params") or {}
        if not isinstance(params, dict):
            raise Blocked("Codex notification parameters are malformed")
        if method == "model/rerouted":
            for key in ("threadId", "turnId", "fromModel", "toModel", "reason"):
                item[key] = params.get(key)
        elif method == "account/updated":
            item.update({key: params.get(key) for key in ("authMode", "planType")})
        result.append(item)
    return result
