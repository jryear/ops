"""Fresh Hermes intent + native Orca projections; history never grants takeover."""
from __future__ import annotations

from collections import Counter

from ..model import WARN
from .common import Blocked, Store, STATE
from .native import Hermes, Orca


def classify(worker):
    p = worker.get("projection")
    if not isinstance(p, dict):
        return "observation-unavailable"
    if worker.get("terminalState") == "retained":
        return "retained-resource"
    if worker.get("terminalState") == "released":
        return "historical-failure" if p.get("outcome") == "failed" else "historical-completion"
    verdict = p.get("liveness", {}).get("verdict")
    if verdict not in ("live", "exited"):
        return "observation-unavailable"
    if verdict == "live":
        return "live-blockage" if p.get("attention", {}).get("requiresAction") else "live-execution"
    if p.get("nextAction", {}).get("argv") and p.get("attention", {}).get("requiresAction"):
        return "actionable-native-settlement"
    return "exited-awaiting-reconciliation"


def reconcile(rep, hermes=None, orca=None):
    hermes, orca = hermes or Hermes(), orca or Orca()
    workers, task_index = None, {}
    try:
        workers = orca.workers()
        classes = Counter(classify(w) for w in workers)
        rep.checked["orca_dispatches"] = len(workers)
        rep.checked["orca_projection_categories"] = dict(classes)
        for w in workers:
            kind = classify(w)
            if kind in ("observation-unavailable", "live-blockage", "actionable-native-settlement", "exited-awaiting-reconciliation"):
                p = w.get("projection", {})
                rep.add(WARN, "execution." + kind, w.get("dispatchId", "unknown dispatch"),
                        "Native lifecycle category: " + kind,
                        "Inspect the authoritative Dispatch; no takeover is authorized by absence.",
                        {"run_id": w.get("runId"), "task_id": w.get("taskId"), "next_action": p.get("nextAction"), "takeover_authorized": False})
        rep.checked["orca_observation"] = "available"
    except Blocked as exc:
        rep.checked["orca_observation"] = "unavailable"
        rep.add(WARN, "execution.observation_unavailable", "Orca", str(exc), "Restore observation; do not retry or take over.")
    try:
        counts, blocked = Counter(), 0
        for board in hermes.boards():
            tasks = hermes.tasks(board)
            task_index.update({(board, t["id"]): t for t in tasks})
            counts.update(t.get("status", "unknown") for t in tasks)
            blocked += sum(t.get("status") == "blocked" for t in tasks)
        rep.checked["hermes_task_states"] = dict(counts)
        rep.checked["hermes_observation"] = "available"
        if blocked:
            rep.notes.append(f"Hermes retains {blocked} blocked tasks; blocked history alone is not a live execution failure.")
    except Blocked as exc:
        rep.checked["hermes_observation"] = "unavailable"
        rep.add(WARN, "execution.observation_unavailable", "Hermes", str(exc), "Restore intent observation before new dispatch.")
    if workers is not None and rep.checked.get("hermes_observation") == "available" and (STATE / "attempts").exists():
        reconcile_links(rep, Store(), task_index, workers)


def reconcile_links(rep, store, tasks, workers):
    links = []
    for folder in sorted((store.root / "attempts").iterdir()):
        if not folder.is_dir():
            continue
        try:
            events = store.events(folder.name)
            if not events or events[0]["kind"] != "prepared":
                raise Blocked("attempt lacks source packet")
            first = events[0]["data"]
            key = (first["hermes_board"], first["hermes_task_id"])
            task = tasks.get(key)
            task_ids = {e["data"]["orca_task_id"] for e in events if e["kind"] == "task_created"}
            native = [w for w in workers if w.get("taskId") in task_ids and w.get("runId") == first["orca_run_id"]]
            kind = "linked" if task and len(native) == 1 else "unresolved-link"
            accepted = any(e["kind"] == "review" and e["data"].get("verdict") == "accepted" for e in events)
            if task and task.get("status") == "done" and not accepted:
                kind = "acceptance-evidence-missing"
            links.append({"attempt_id": folder.name, "hermes_board": key[0], "hermes_task_id": key[1],
                          "orca_task_ids": sorted(task_ids), "classification": kind})
            if kind != "linked":
                rep.add(WARN, "execution." + kind, folder.name,
                        "Hermes intent and Orca attempt evidence do not establish accepted completion.",
                        "Recover from the immutable packet and native lifecycle; never dispatch from absence.")
        except (Blocked, KeyError, ValueError, OSError):
            rep.add(WARN, "execution.evidence_unavailable", folder.name,
                    "Execution evidence could not be reconciled.", "Preserve source evidence and reconstruct before resuming.")
    rep.checked["execution_links"] = links
