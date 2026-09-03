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
    classified = assess()

    tally = Counter(p.classification for p in classified)
    rep.checked = {
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
            os.kill(pid, signal.SIGTERM)
            killed.append(p)
        except (ProcessLookupError, PermissionError) as e:
            rep.add(WARN, "process.kill_failed", f"pid {pid}", str(e), "")

    if killed:
        time.sleep(2)
        for p in killed:
            try:
                os.kill(p.pid, 0)          # still alive -> escalate
                os.kill(p.pid, signal.SIGKILL)
                p.evidence += " (SIGKILL required)"
            except ProcessLookupError:
                pass
            except PermissionError:
                pass

    for p in killed:
        rep.add(OK, "process.killed", f"pid {p.pid}  {_name(p.command)}",
                f"terminated ({p.classification})", "",
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
