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
    if scope in ("all", "orca", "hermes", "execution"):
        from .execution.observation import reconcile
        reconcile(rep)
    if scope == "junior":
        rep.checked["junior_authority"] = "frozen-historical-only"
        rep.checked["junior_counts"] = junior.counts()
        rep.notes.append("Junior is frozen historical evidence; its old leases never authorize current task or process actions.")
    if scope in ("all", "process"):
        try:
            _process_findings(rep)
            rep.checked["process_observation"] = "available"
        except ow.ObservationUnavailable as exc:
            ow.report_unavailable(rep, exc)

    rep.checked["elapsed_s"] = round(time.time() - started, 1)
    return rep
