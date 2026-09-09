"""reap -- what processes are leaked?

Reports by default. Killing requires both `--kill` and an explicit `--pid` list,
so nothing is ever terminated as a side effect of looking.

The bias is deliberate and asymmetric: a missed orphan costs some memory, a
false positive kills someone's running work. So ownership evidence always beats
orphan heuristics, and anything uncertain is reported as `unknown` rather than
swept up. `ownership.classify` carries the actual rules; this module presents
them and, only when explicitly told to, acts on them.
"""

from __future__ import annotations

import os
import signal
import time
from collections import Counter
from pathlib import Path

from . import ownership as ow
from .model import ACTION, OK, WARN, Report

HOME = Path.home()


def _name(cmd: str) -> str:
    parts = cmd.split()
    if not parts:
        return "?"
    exe = Path(parts[0]).name
    # `python -m browser_harness.daemon` is far more useful than `Python`.
    for i, tok in enumerate(parts[1:], 1):
        if tok == "-m" and i + 1 < len(parts):
            return f"{exe} -m {parts[i + 1]}"
    return exe


def _hours(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def assess() -> list[ow.Process]:
    """Classify every candidate process. Read-only."""
    return [ow.classify(p) for p in ow.reap_candidates()]


def run(kill: bool = False, pids: list[int] | None = None) -> Report:
    rep = Report(verb="reap")
    ow.reset_cache()
    try:
        if kill:
            ow.require_ownership_observation()
        classified = assess()
    except ow.ObservationUnavailable as exc:
        ow.report_unavailable(rep, exc)
        if kill:
            rep.add(ACTION, "process.kill_refused", "Process termination refused",
                    "Required process or ownership observation is unavailable", "Restore observation and retry.")
        return rep

    tally = Counter(p.classification for p in classified)
    rep.checked = {
        "process_observation": "available",
        "candidates": len(classified),
        "definitely_orphaned": tally[ow.ORPHAN],
        "likely_orphaned": tally[ow.LIKELY_ORPHAN],
        "active_owned": tally[ow.OWNED],
        "unknown": tally[ow.UNKNOWN],
    }

    orphans = [p for p in classified if p.classification == ow.ORPHAN]
    likely = [p for p in classified if p.classification == ow.LIKELY_ORPHAN]
    unknown = [p for p in classified if p.classification == ow.UNKNOWN]

    if not kill:
        for p in orphans:
            rep.add(
                WARN, "process.orphaned", f"pid {p.pid}  {_name(p.command)}",
                f"parent gone, no owner, up {_hours(p.etime_s)} - {p.evidence}",
                f"ops reap --kill --pid {p.pid}",
                {"pid": p.pid, "command": p.command, "cwd": p.cwd,
                 "age_s": p.etime_s, "classification": p.classification},
            )
        for p in likely:
            rep.add(
                WARN, "process.likely_orphaned", f"pid {p.pid}  {_name(p.command)}",
                f"{p.evidence}"
                + (f" (cwd {str(p.cwd).replace(str(HOME), '~')})" if p.cwd else ""),
                "Inspect before killing; no owner found but no leak signature either.",
                {"pid": p.pid, "command": p.command, "cwd": p.cwd,
                 "age_s": p.etime_s, "classification": p.classification},
            )
        if unknown:
            rep.notes.append(
                f"{len(unknown)} process(es) classified 'unknown' (too new to judge) "
                f"- not reported as leaks"
            )
        if tally[ow.OWNED]:
            rep.notes.append(
                f"{tally[ow.OWNED]} reparented process(es) resolved to an active "
                f"owner (Orca terminal/worktree or launchd) and were left alone"
            )
        return rep

    # --- destructive path: only reachable with --kill AND explicit pids ---
    requested = set(pids or [])
    by_pid = {p.pid: p for p in classified}
    killed, refused, missing = [], [], []

    for pid in sorted(requested):
        p = by_pid.get(pid)
        if p is None:
            missing.append(pid)
            continue
        # Refuse anything that resolved to an owner, even if asked directly.
        if p.classification in (ow.OWNED,):
            refused.append(p)
            continue
        try:
            ow.reset_cache()
            current = next((q for q in ow.list_processes() if q.pid == pid), None)
            if current is None:
                missing.append(pid)
                continue
            if (current.uid, current.ppid, current.command) != (p.uid, p.ppid, p.command):
                rep.add(ACTION, "process.kill_refused", f"pid {pid}",
                        "Process identity changed since inspection", "Inspect again.")
                continue
            ow.require_ownership_observation()
            current = ow.classify(current)
            if current.classification == ow.OWNED:
                refused.append(current)
                continue
            os.kill(pid, signal.SIGTERM)
            killed.append(p)
        except ow.ObservationUnavailable as exc:
            ow.report_unavailable(rep, exc)
            rep.add(ACTION, "process.kill_refused", f"pid {pid}",
                    "Fresh observation unavailable; no signal sent", "Restore observation and retry.")
        except (ProcessLookupError, PermissionError) as e:
            rep.add(WARN, "process.kill_failed", f"pid {pid}", str(e), "")

    if killed:
        time.sleep(2)
        for p in killed:
            try:
                ow.reset_cache()
                current = next((q for q in ow.list_processes() if q.pid == p.pid), None)
                if current is None:
                    continue
                if (current.uid, current.ppid, current.command) != (p.uid, p.ppid, p.command):
                    raise ow.ObservationUnavailable("Process identity changed before escalation")
                ow.require_ownership_observation()
                if ow.classify(current).classification == ow.OWNED:
                    raise ow.ObservationUnavailable("Process now has an active owner")
                os.kill(p.pid, signal.SIGKILL)
                p.evidence += " (SIGKILL required)"
            except ow.ObservationUnavailable as exc:
                ow.report_unavailable(rep, exc)
                rep.add(ACTION, "process.kill_refused", f"pid {p.pid}",
                        "Escalation refused; SIGTERM was sent but termination is unverified",
                        "Inspect through the process owner.")
            except ProcessLookupError:
                pass
            except PermissionError:
                pass

    for p in killed:
        rep.add(OK, "process.killed", f"pid {p.pid}  {_name(p.command)}",
                f"termination signal sent ({p.classification}); see any verification warnings", "",
                {"pid": p.pid, "command": p.command})
    for p in refused:
        rep.add(ACTION, "process.kill_refused", f"pid {p.pid}  {_name(p.command)}",
                f"refused: resolved to an active owner - {p.evidence}",
                "If you are certain, stop it through its owner (e.g. orca terminal close).",
                {"pid": p.pid})
    for pid in missing:
        rep.add(WARN, "process.not_a_candidate", f"pid {pid}",
                "not in the candidate set (already gone, system-owned, or has a live parent)",
                "")

    rep.checked["killed"] = len(killed)
    rep.checked["refused"] = len(refused)
    return rep
