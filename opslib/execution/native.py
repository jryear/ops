"""Supported command adapters. All observations are fresh and shape checked."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .common import Blocked, command, json_command


class Orca:
    def __init__(self, executable=None):
        self.executable = executable or os.environ.get("ORCA_CLI_COMMAND") or ("orca-dev" if os.environ.get("ORCA_DEV_REPO_ROOT") else "orca")
        self.executable = shutil.which(self.executable) or self.executable

    def call(self, *args, timeout=30):
        result = json_command([self.executable, *args, "--json"], timeout=timeout)
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(result.get("result"), dict):
            raise Blocked("Orca observation unavailable or mutation outcome unknown; inspect native state")
        return result["result"]

    def rows(self, *args, key):
        result = self.call(*args)
        rows = result.get(key)
        page, host_scope = result.get("page") or {}, result.get("hostScope") or {}
        if not isinstance(page, dict) or not isinstance(host_scope, dict):
            raise Blocked("Orca inventory metadata is malformed")
        if (not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows)
                or result.get("truncated") or page.get("hasMore")):
            raise Blocked("Orca observation incomplete")
        if host_scope.get("omittedHostIds"):
            raise Blocked("Orca execution hosts omitted from observation")
        return rows

    def workers(self, run=None):
        return self.rows("orchestration", "worker-list", "--include-remote", *(["--run", run] if run else []), key="workers")


class Hermes:
    def __init__(self, executable=None):
        self.executable = executable or shutil.which("hermes") or "hermes"

    def read(self, board, *args):
        return json_command([self.executable, "kanban", "--board", board, *args, "--json"])

    def task(self, board, task):
        result = self.read(board, "show", task)
        if not isinstance(result, dict):
            raise Blocked("Hermes task observation unavailable")
        body = result.get("task", result)
        if body.get("id") != task:
            raise Blocked("Hermes task identity mismatch")
        return result

    def tasks(self, board):
        result = self.read(board, "list")
        if not isinstance(result, list) or not all(isinstance(x, dict) and x.get("id") for x in result):
            raise Blocked("Hermes task inventory unavailable")
        return result

    def boards(self):
        result = json_command([self.executable, "kanban", "boards", "list", "--json"])
        if not isinstance(result, list) or not all(isinstance(x, dict) and x.get("slug") for x in result):
            raise Blocked("Hermes board inventory unavailable")
        return [x["slug"] for x in result if not x.get("archived")]

    def attach(self, board, task, reference):
        command([self.executable, "kanban", "--board", board, "attach", task,
                 reference["path"], "--name", reference["sha256"] + Path(reference["path"]).suffix, "--author", "ops-execution"])
        command([self.executable, "kanban", "--board", board, "comment", task,
                 "ops execution evidence sha256=" + reference["sha256"] + " local=" + reference["path"],
                 "--author", "ops-execution"])

    def create_integration(self, board, task, candidate, body):
        result = self.read(board, "create", "Integrate " + task + " at " + candidate[:12],
                           "--body", body, "--initial-status", "blocked", "--created-by", "ops-execution",
                           "--idempotency-key", "integration:" + task + ":" + candidate,
                           "--max-retries", "1")
        if not isinstance(result, dict) or not result.get("id"):
            raise Blocked("integration task creation outcome unknown")
        return result
