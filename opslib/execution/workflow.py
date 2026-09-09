"""One-shot lifecycle operations. No background loop, scheduler, or task cache."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from .. import registry
from . import policy, providers
from .common import Blocked, Store, command, digest, encoded, git
from .native import Hermes, Orca


def references(packet):
    refs = packet["source_evidence"] + packet["decisions"] + packet["authorization"]["references"]
    for ref in refs:
        path = Path(ref["path"])
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise Blocked("source reference is unavailable or indirect")
        if digest(path.read_bytes()) != ref["sha256"]:
            raise Blocked("source evidence hash changed: " + str(path))


def project_contract(packet):
    entry = registry.load().get(packet["project"])
    if not entry or entry.lifecycle in ("dormant", "frozen"):
        raise Blocked("project is unregistered or not admitted for new execution")
    integration = packet["integration"]
    if not entry.canonical_checkout or not entry.execution_authority_ref or not entry.integration_authority_ref:
        raise Blocked("explicit canonical selection and execution/integration authority are required")
    if (Path(integration["canonical_checkout"]).resolve() != Path(entry.canonical_checkout).resolve()
            or integration["canonical_branch"] != entry.canonical_branch
            or packet["ownership"]["execution_authority_ref"] != entry.execution_authority_ref
            or integration["authority_ref"] != entry.integration_authority_ref
            or integration["queue_board"] != entry.integration_queue_board):
        raise Blocked("packet disagrees with registered canonical selection or authority")
    return entry


def repository(packet, *, require_clean=True):
    path = Path(packet["scope"]["worktree"])
    if not path.is_absolute() or path.is_symlink() or str(path.resolve()) != str(path):
        raise Blocked("worktree must be an absolute canonical path")
    if git(path, "rev-parse", "--show-toplevel") != str(path):
        raise Blocked("scope must name the repository root")
    if git(path, "rev-parse", "HEAD") != packet["scope"]["base_revision"]:
        raise Blocked("worktree base revision changed")
    if require_clean and git(path, "status", "--porcelain=v1", "--untracked-files=all"):
        raise Blocked("dirty worktree: preserve and reconcile before dispatch")
    canonical = Path(packet["integration"]["canonical_checkout"])
    if git(path, "rev-parse", "--path-format=absolute", "--git-common-dir") != git(canonical, "rev-parse", "--path-format=absolute", "--git-common-dir"):
        raise Blocked("source and canonical checkout are not worktrees of the same repository")
    if packet["scope"]["write_paths"] and path.resolve() == canonical.resolve():
        raise Blocked("implementation cannot own the canonical checkout")
    for name in packet["scope"]["write_paths"] + packet["scope"]["read_paths"]:
        if not (path / name).resolve().is_relative_to(path):
            raise Blocked("scope escapes worktree through a symlink")
    return path


def ownership(packet, orca):
    context = packet["ownership"]
    run = orca.call("orchestration", "run-show", "--id", context["orca_run_id"])["run"]
    if run.get("coordinator_handle") != context["coordinator_handle"] or run.get("legacy"):
        raise Blocked("coordinator identity is not the native Run owner")
    path = packet["scope"]["worktree"]
    trees = orca.rows("worktree", "ps", key="worktrees")
    matches = [w for w in trees if w.get("path") == path and not w.get("isArchived")]
    if len(matches) != 1:
        raise Blocked("worktree must have one live Orca registration")
    worktree_id = matches[0].get("worktreeId") or matches[0].get("id")
    if not worktree_id:
        raise Blocked("Orca worktree identity unavailable")
    terminals = orca.rows("terminal", "list", "--worktree", "path:" + path, key="terminals")
    for terminal in terminals:
        if terminal.get("orphaned") or terminal.get("handle") not in context["allowed_terminal_handles"]:
            raise Blocked("competing or unverified terminal owns this mutable worktree")
    for worker in orca.workers():
        projection = worker.get("projection", {})
        workspace = projection.get("workspace", {}).get("id") or worker.get("resource", {}).get("worktreeId")
        if workspace == worktree_id and worker.get("terminalState") != "released":
            raise Blocked("native dispatch still owns or retains this worktree; missing observation never authorizes takeover")
    repos = orca.rows("repo", "list", key="repos")
    repo_id = worktree_id.split("::", 1)[0]
    repo = next((r for r in repos if r.get("id") == repo_id), None)
    if not repo or "hookSettings" not in repo:
        raise Blocked("setup policy observation unavailable")
    if repo["hookSettings"].get("scripts", {}).get("setup"):
        raise Blocked("setup completion attestation for configured hook is required before dispatch")
    return {"worktree_id": worktree_id, "setup": "verified-no-setup-command", "run_id": run["id"]}


def native_task(packet, hermes):
    result = hermes.task(packet["board"], packet["task_id"])
    task = result.get("task", result)
    # Ops cards are held from Hermes' autonomous dispatcher. Orca owns attempts.
    if task.get("status") not in ("blocked", "review") or task.get("assignee"):
        raise Blocked("Hermes packet must be unassigned and held from its dispatcher")
    for dependency in packet["dependencies"]:
        observed = hermes.task(packet["board"], dependency)
        if observed.get("task", observed).get("status") != "done":
            raise Blocked("dependency has not been accepted in Hermes: " + dependency)
    return task


class Execution:
    def __init__(self, store=None, orca=None, hermes=None):
        self.store = store or Store()
        self.orca = orca or Orca()
        self.hermes = hermes or Hermes()

    def record(self, packet, attempt, kind, data):
        reference = self.store.event(attempt, kind, data)
        self.hermes.attach(packet["board"], packet["task_id"], reference)
        return reference

    def prepare(self, packet):
        policy.validate_packet(packet)
        route = policy.resolve(packet["route"])
        if packet["role"] != route["role"] or packet["task_class"] != route["task_class"]:
            raise Blocked("packet role/class does not match the resolved route")
        if time.time() >= packet["authorization"]["expires_at"]:
            raise Blocked("task authorization expired")
        if not packet["scope"]["write_paths"] and packet["authorization"]["permissions"] != "read-only":
            raise Blocked("read-only tasks must use read-only permissions")
        project_contract(packet)
        references(packet)
        path = repository(packet)
        native_task(packet, self.hermes)
        owned = ownership(packet, self.orca)
        observation = providers.verify(route, path, self.orca)
        return {"packet": packet, "route": route, "ownership": owned, "admission": observation}

    def dispatch(self, packet, apply=False):
        prepared = self.prepare(packet)
        if not apply:
            return prepared
        providers.require_admission(prepared["admission"])
        key = packet["board"] + ":" + packet["task_id"]
        with self.store.lock("task:" + key), self.store.lock("worktree:" + packet["scope"]["worktree"]):
            # Durable intent makes crash/replay fail closed even before a native receipt.
            previous = self.find_attempts(packet)
            if previous:
                raise Blocked("packet already has an attempt; use recovery, never duplicate dispatch")
            prepared = self.prepare(packet)  # re-read all admission facts under the lock
            providers.require_admission(prepared["admission"])
            attempt = str(uuid.uuid4())
            _, raw, sha = policy.load()
            policy_ref = self.store.put_bytes(raw)
            packet_ref = self.store.put(packet)
            self.hermes.attach(packet["board"], packet["task_id"], packet_ref)
            self.hermes.attach(packet["board"], packet["task_id"], policy_ref)
            self.record(packet, attempt, "prepared", {"packet": packet_ref, "policy": policy_ref,
                "policy_sha256": sha, "route": prepared["route"], "hermes_task_id": packet["task_id"],
                "hermes_board": packet["board"], "orca_run_id": packet["ownership"]["orca_run_id"],
                "requested_route": prepared["route"], "observed_route": None, "provider_session_id": None})
            self.record(packet, attempt, "dispatch_intent", {"packet_sha256": packet_ref["sha256"]})
            try:
                task = self.orca.call("orchestration", "task-create", "--spec", self.spec(packet, attempt, packet_ref),
                                      "--run", packet["ownership"]["orca_run_id"], "--from", packet["ownership"]["coordinator_handle"])
                task_id = task["task"]["id"]
                self.record(packet, attempt, "task_created", {"orca_task_id": task_id})
                self.record(packet, attempt, "dispatch_requested", {"orca_task_id": task_id})
                receipt = self.orca.call("orchestration", "worker-start", "--task", task_id,
                    "--worktree", "path:" + packet["scope"]["worktree"], "--agent", prepared["route"]["provider"],
                    "--model", prepared["route"]["model"], "--effort", prepared["route"]["effort"],
                    "--run", packet["ownership"]["orca_run_id"], "--from", packet["ownership"]["coordinator_handle"],
                    "--timeout-ms", "30000", timeout=40)
                self.record(packet, attempt, "dispatch_receipt", compact_receipt(receipt))
            except (Blocked, KeyError) as exc:
                self.record(packet, attempt, "dispatch_unknown", {"failure_category": "dispatch-observation-unavailable"})
                raise Blocked("dispatch outcome unknown for " + attempt + "; reconcile native state before any retry") from exc
            return {"attempt_id": attempt, "receipt": compact_receipt(receipt)}

    @staticmethod
    def spec(packet, attempt, reference):
        return "\n".join(["Ops immutable packet " + reference["path"], "sha256=" + reference["sha256"],
            "Attempt " + attempt, "Target: " + packet["scope"]["worktree"], "Change: " + packet["objective"],
            "Constraints: " + json.dumps(packet["scope"]["prohibited_effects"]),
            "Ownership: " + json.dumps(packet["scope"]["write_paths"]),
            "Acceptance: " + json.dumps(packet["acceptance"]),
            "Read the entire packet, preserve its source references and stop conditions. Do not exceed runtime/depth bounds."])

    def find_attempts(self, packet):
        found = []
        for path in (self.store.root / "attempts").glob("*/000000.json"):
            events = self.store.events(path.parent.name)
            data = events[0]["data"]
            if data.get("hermes_task_id") == packet["task_id"] and data.get("hermes_board") == packet["board"]:
                found.append(path.parent.name)
        return found

    def reconstruct(self, attempt):
        events = self.store.events(attempt)
        if not events or events[0]["kind"] != "prepared":
            raise Blocked("attempt lacks a durable prepared packet")
        packet = self.store.read(events[0]["data"]["packet"])
        # Load the old immutable policy as bytes; never resolve a historical run from today's policy.
        policy_ref = events[0]["data"]["policy"]
        path = self.store.root / "objects" / (policy_ref["sha256"] + ".blob")
        if path.is_symlink() or digest(path.read_bytes()) != policy_ref["sha256"]:
            raise Blocked("run policy snapshot missing or modified")
        policy.validate_packet(packet)
        references(packet)
        return packet, events

    def status(self, attempt):
        packet, events = self.reconstruct(attempt)
        native = native_task(packet, self.hermes)
        task_ids = {e["data"].get("orca_task_id") for e in events if e["data"].get("orca_task_id")}
        workers = [w for w in self.orca.workers(packet["ownership"]["orca_run_id"]) if w.get("taskId") in task_ids]
        return {"attempt_id": attempt, "hermes_status": native.get("status"),
                "events": len(events), "workers": workers,
                "observation": "available" if workers else "unavailable", "takeover_authorized": False}

    def recover(self, attempt, apply=False):
        from .recovery import next_stage
        packet, events = self.reconstruct(attempt)
        status = self.status(attempt)
        history = ["initial"] + [e["data"]["stage"] for e in events if e["kind"] == "recovery" and e["data"].get("stage") in ("correction", "diagnosis", "escalation", "reframe")]
        try:
            stage = next_stage(history, packet["limits"], status["workers"])
            action = "propose " + stage + "; a fresh admitted packet and native retry reconciliation are required before execution"
        except Blocked as exc:
            stage, action = "reconcile-only", str(exc)
        result = {**status, "next_action": action, "packet": packet,
                  "next_stage": stage,
                  "source_policy_sha256": events[0]["data"]["policy_sha256"], "dispatch_performed": False}
        if apply:
            self.record(packet, attempt, "recovery", {"stage": "reconciliation", "next_action": action})
        return result


def compact_receipt(receipt):
    # Whitelist native identities and structured route facts; do not index prompts/transcripts.
    result = {key: receipt.get(key) for key in ("stage", "failedStage", "taskId", "dispatchId", "runId", "agentTerminalHandle", "launch", "setup", "mutation") if key in receipt}
    for key in ("task", "dispatch"):
        if isinstance(receipt.get(key), dict):
            result[key] = {k: receipt[key].get(k) for k in ("id", "task_id", "run_id", "status", "terminal_handle")}
    return result
