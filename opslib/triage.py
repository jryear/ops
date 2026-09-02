"""triage -- what actually needs Zack's attention right now?

Preserves the intent of a request typed by hand six times:

    "Check for stuck work, stale generated files, failing validation, and
     anything that needs human attention. Report only actionable issues."

The operative words are *only actionable*. This is deliberately not a health
report: a finding earns its place by implying a decision. Everything quiet stays
silent. `ops machine` is where raw state belongs.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from . import junior, ownership as ow, safe
from .model import ACTION, OK, WARN, Report

HOME = Path.home()

def _age_days(iso: str) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except ValueError:
        return None


def _short(p: str) -> str:
    return str(p).replace(str(HOME), "~")


def _git_findings(rep: Report) -> None:
    """Work stranded in one fragile place.

    Delegates to `safe`, which owns repository risk classification. Triage adds
    no rules of its own here -- one implementation, two entry points.
    """
    sub = safe.run()
    rep.findings.extend(sub.findings)
    rep.checked["checkouts"] = sub.checked.get("checkouts", 0)
    rep.notes.extend(sub.notes)


def _orca_findings(rep: Report) -> None:
    """Execution state Orca itself reports as anomalous."""
    if not ow.orca_available():
        rep.notes.append("orca CLI not found; execution signals skipped")
        return

    worktrees = ow.orca_worktrees()
    rep.checked["orca_worktrees"] = len(worktrees)

    for t in ow.orca_orphaned_terminals():
        rep.add(
            WARN, "orca.orphaned_terminal",
            _short(t.get("worktreePath") or t.get("handle", "?")),
            "Orca reports this terminal as orphaned",
            "orca terminal close, or reattach if the work still matters.",
            {"handle": t.get("handle")},
        )

    unreleased = ow.orca_unreleased_workers()
    if unreleased:
        rep.add(
            WARN, "orca.unreleased_workers", f"{len(unreleased)} dispatch(es)",
            "terminal=release_unknown - Orca could not confirm teardown",
            "orca orchestration worker-release, once you have read any output worth keeping.",
            {"contexts": list(unreleased[:10])},
        )

    # Unread output on a worktree with no live terminal = a worker finished and
    # nobody looked. That is exactly "stuck work" in the original request.
    for w in worktrees:
        if w.get("isArchived"):
            continue
        if w.get("unread") and not (w.get("liveTerminalCount") or 0):
            rep.add(
                WARN, "orca.unread_finished", _short(w.get("path", "?")),
                f"unread output on '{w.get('branch','?')}' with no live terminal",
                "orca terminal read, then release the worker.",
                {"path": w.get("path"), "branch": w.get("branch")},
            )


def _junior_findings(rep: Report) -> None:
    """Coordination state: blocked tasks and lapsed authority."""
    if not junior.available():
        return
    rep.checked["junior_projects"] = len(junior.projects())

    for w in junior.expired_leases():
        rep.add(
            ACTION, "junior.expired_lease", f"worker {w['id'][:16]}",
            f"status={w['status']} but lease expired {w.get('lease_expires_at','?')}",
            "Resolve or cancel the task; its authority to act has lapsed.",
            {"worker": w["id"], "task": w.get("task_id")},
        )

    for t in junior.blocked_tasks():
        rep.add(
            WARN, "junior.blocked_task", f"{t.get('project') or '?'}: {t['title'][:52]}",
            f"task blocked since {t.get('updated_at','?')[:10]}",
            "Unblock, or cancel if it is no longer wanted.",
            {"task": t["id"]},
        )


def _process_findings(rep: Report) -> None:
    """Leaked processes -- ownership resolved before anything is called a leak."""
    procs = ow.list_processes()
    rep.checked["processes"] = len(procs)

    # Ours, reparented, non-system. Excludes the ~700 macOS daemons that are
    # legitimately reparented to launchd but absent from the user-domain list.
    candidates = ow.reap_candidates(procs)

    orphans, likely = [], []
    for p in candidates:
        c = ow.classify(p)
        if c.classification == ow.ORPHAN:
            orphans.append(c)
        elif c.classification == ow.LIKELY_ORPHAN:
            likely.append(c)

    if orphans:
        rep.add(
            WARN, "process.orphaned", f"{len(orphans)} leaked process(es)",
            "; ".join(f"pid {p.pid} {Path(p.command.split()[0]).name}" for p in orphans[:4]),
            "ops reap  (report only; add --kill to act)",
            {"pids": [p.pid for p in orphans]},
        )
    if likely:
        rep.add(
            WARN, "process.likely_orphaned", f"{len(likely)} unowned process(es)",
            "no live parent, no Orca owner, running > 6h",
            "ops reap  to inspect before deciding.",
            {"pids": [p.pid for p in likely]},
        )


def run(scope: str = "all") -> Report:
    rep = Report(verb="triage")
    started = time.time()

    if scope in ("all", "git"):
        _git_findings(rep)
    if scope in ("all", "orca"):
        _orca_findings(rep)
    if scope in ("all", "junior"):
        _junior_findings(rep)
    if scope in ("all", "process"):
        _process_findings(rep)

    rep.checked["elapsed_s"] = round(time.time() - started, 1)
    return rep
