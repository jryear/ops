"""Supported command adapters. All observations are fresh and shape checked."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .common import Blocked, command, json_command


def require_hermes_hold(result, *, integration_queue=False):
    """Hermes recompute_ready honors an explicit blocked event, not its label.

    Native event order is authoritative. A created(status=blocked) card with no
    blocked event is eligible for automatic promotion and default assignment.
    """
    task = result.get("task", result)
    events = result.get("events")
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        raise Blocked("Hermes hold event observation unavailable")
    if any(e.get("kind") == "claimed" for e in events):
        raise Blocked("Hermes has native claim history for this Orca-owned task; reconcile competing execution")
    if task.get("assignee"):
        raise Blocked("Hermes task needs an unassigned explicit dispatcher hold")
    if integration_queue and task.get("status") == "review" and task.get("created_by") == "ops-execution":
        # The native review dispatcher skips unassigned cards and does not
        # apply default-assignee routing to this lane. Create here atomically,
        # never expose a new integration item to the ready/default-assignee lane.
        created = next((e for e in events if e.get("kind") == "created"), {})
        if (created.get("payload") or {}).get("status") == "review":
            return task
    if task.get("status") != "blocked":
        raise Blocked("Hermes task needs an unassigned explicit dispatcher hold")
    transitions = [e for e in events if e.get("kind") in ("blocked", "unblocked")]
    if not transitions or transitions[-1].get("kind") != "blocked":
        raise Blocked("Hermes blocked label lacks a durable blocked event; automatic promotion remains possible")
    return task


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

    def hold(self, board, task, reason, *, apply=False):
        observed = self.task(board, task)
        body = observed.get("task", observed)
        if (body.get("assignee") or body.get("status") not in ("ready", "blocked")
                or any(e.get("kind") == "claimed" for e in observed.get("events", []))):
            raise Blocked("only an unassigned ready task may receive an ops hold; no active claim is reclaimed")
        if body.get("status") == "blocked":
            require_hermes_hold(observed)
            return {"task_id": task, "held": True, "mutated": False}
        if not apply:
            return {"task_id": task, "held": False, "mutated": False, "next_action": "record explicit capability block"}
        # The supported block command has no expected-state CAS. Refuse active
        # claims on readback. A raced claim permanently withholds ops admission
        # until native reconciliation; never launch an Orca writer on that card.
        command([self.executable, "kanban", "--board", board, "block", task,
                 reason, "--kind", "capability"])
        after = self.task(board, task)
        require_hermes_hold(after)
        before_claims = [e for e in observed.get("events", []) if e.get("kind") == "claimed"]
        after_claims = [e for e in after.get("events", []) if e.get("kind") == "claimed"]
        if before_claims != after_claims:
            raise Blocked("Hermes claim raced with hold; preserve native evidence and reconcile before dispatch")
        return {"task_id": task, "held": True, "mutated": True}

    def create_integration(self, board, task, candidate, body):
        # Do not simulate atomic queue creation with create-ready then block:
        # default-assignee dispatch could claim the task between those commands.
        help_text = command([self.executable, "kanban", "create", "--help"])
        modes = re.search(r"--initial-status\s+\{([^}]+)\}", help_text)
        if not modes or "review" not in modes.group(1).split(","):
            raise Blocked("Hermes CLI cannot atomically create an unassigned review queue item; integration queue creation withheld")
        result = self.read(board, "create", "Integrate " + task + " at " + candidate[:12],
                           "--body", body, "--initial-status", "review", "--created-by", "ops-execution",
                           "--idempotency-key", "integration:" + task + ":" + candidate,
                           "--max-retries", "1")
        if not isinstance(result, dict) or not result.get("id"):
            raise Blocked("integration task creation outcome unknown")
        return require_hermes_hold(self.task(board, result["id"]), integration_queue=True)
