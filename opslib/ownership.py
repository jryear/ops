"""Resolve who owns a path or a process.

This module exists because of a near-miss. An earlier audit saw five
`npm run dev` servers and classified them as leaked, on the reasoning that
orphaned agent processes had been found elsewhere. They were not leaked: they
were live Vite servers inside Orca-managed worktrees
(`~/orca/workspaces/hillside/.../viewer`), doing real work.

So ownership is resolved against Orca *first*, and `PPID == 1` is never by
itself evidence of an orphan -- launchd legitimately reparents its own jobs.

Orca is the authority for execution ownership. We only read from it.
Stdlib only; every Orca call degrades to "unknown" rather than failing.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path

ORCA = "/usr/local/bin/orca"

# Classifications, most to least confident.
OWNED = "active-owned"
LIKELY_ORPHAN = "likely-orphaned"
ORPHAN = "definitely-orphaned"
UNKNOWN = "unknown"

# Executables that are *known* leak sources, confirmed by the audit: browser
# harness daemons whose parents died, and Sparkle updaters wedged for days.
KNOWN_LEAK_PATTERNS = (
    "browser_harness.daemon",
    "Sparkle.framework/Versions/B/Autoupdate",
    "org.sparkle-project.Sparkle/Launcher",
)

# Age past which an unowned process becomes suspicious (seconds).
STALE_AGE = 6 * 3600


def _run(cmd: list[str], timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def orca_available() -> bool:
    return os.path.exists(ORCA)


def orca_json(*args: str) -> dict:
    """Call an Orca command with --json and unwrap its {id, ok, result} envelope.

    Orca's own machine interface, so we parse structure rather than scrape text.
    Returns {} on any failure -- callers degrade to 'unknown', never to a guess.
    """
    if not orca_available():
        return {}
    out = _run([ORCA, *args, "--json"], timeout=30)
    if not out.strip():
        return {}
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        return {}
    if not env.get("ok"):
        return {}
    return env.get("result") or {}


@functools.lru_cache(maxsize=1)
def orca_terminals() -> tuple[dict, ...]:
    """Live Orca-managed terminals, with Orca's own `orphaned` flag."""
    return tuple(orca_json("terminal", "list").get("terminals") or ())


@functools.lru_cache(maxsize=1)
def orca_worktrees() -> tuple[dict, ...]:
    """Orca worktrees with activity fields (liveTerminalCount, unread, status)."""
    return tuple(orca_json("worktree", "ps").get("worktrees") or ())


@functools.lru_cache(maxsize=1)
def orca_terminal_paths() -> tuple[str, ...]:
    """Directories hosting a live, non-orphaned Orca terminal."""
    return tuple(dict.fromkeys(
        t["worktreePath"] for t in orca_terminals()
        if t.get("worktreePath") and not t.get("orphaned")
    ))


@functools.lru_cache(maxsize=1)
def orca_worktree_paths() -> tuple[str, ...]:
    """Worktrees Orca is actively running work in (a live terminal attached)."""
    return tuple(dict.fromkeys(
        w["path"] for w in orca_worktrees()
        if w.get("path") and (w.get("liveTerminalCount") or 0) > 0
        and not w.get("isArchived")
    ))


@functools.lru_cache(maxsize=1)
def orca_repo_paths() -> tuple[str, ...]:
    """Repos registered with Orca (`orca repo list`)."""
    if not orca_available():
        return ()
    out = _run([ORCA, "repo", "list"])
    return tuple(
        tok for line in out.splitlines() for tok in line.split() if tok.startswith("/")
    )


@functools.lru_cache(maxsize=1)
def orca_orphaned_terminals() -> tuple[dict, ...]:
    """Terminals Orca itself flags as orphaned -- a first-party leak signal."""
    return tuple(t for t in orca_terminals() if t.get("orphaned"))


@functools.lru_cache(maxsize=1)
def orca_unreleased_workers() -> tuple[str, ...]:
    """Dispatches whose terminal was never confirmed released.

    `terminal=release_unknown` means Orca could not confirm teardown -- the
    accounting signal for a possible leak.
    """
    if not orca_available():
        return ()
    out = _run([ORCA, "orchestration", "worker-list"])
    return tuple(
        line.split()[0]
        for line in out.splitlines()
        if "release_unknown" in line and line.strip()
    )


@functools.lru_cache(maxsize=1)
def launchd_labels() -> frozenset[str]:
    """Labels launchd currently manages, so we never call its jobs orphans."""
    out = _run(["launchctl", "list"])
    labels = set()
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            labels.add(parts[2].strip())
    return frozenset(labels)


@functools.lru_cache(maxsize=1)
def launchd_pids() -> frozenset[int]:
    """PIDs launchd reports as its own running jobs."""
    out = _run(["launchctl", "list"])
    pids = set()
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if parts and parts[0].strip().isdigit():
            pids.add(int(parts[0].strip()))
    return frozenset(pids)


@functools.lru_cache(maxsize=1)
def running_app_bundles() -> frozenset[str]:
    """App bundles with at least one live process (`/Applications/X.app`).

    Electron and Chromium apps spawn helpers (crashpad handlers, renderers)
    that legitimately reparent to launchd while the app runs. Without this,
    Orca's own helpers get reported as leaks -- observed, not hypothetical.
    """
    out = _run(["ps", "-Ao", "args="])
    bundles = set()
    for line in out.splitlines():
        exe = line.split()[0] if line.split() else ""
        if exe.startswith("/Applications/") and ".app/" in exe:
            bundles.add(exe.split(".app/")[0] + ".app")
    return frozenset(bundles)


def app_bundle_owner(command: str) -> tuple[str, str]:
    """Is this process a helper of a currently-running app bundle?"""
    exe = command.split()[0] if command.split() else ""
    if not exe.startswith("/Applications/") or ".app/" not in exe:
        return "", ""
    bundle = exe.split(".app/")[0] + ".app"
    if bundle in running_app_bundles():
        return "app-helper", f"helper process of running {Path(bundle).name}"
    return "", ""


def path_owner(path: str) -> tuple[str, str]:
    """Who owns this filesystem path? -> (owner, evidence)"""
    p = str(path)
    for t in orca_terminal_paths():
        if p == t or p.startswith(t.rstrip("/") + "/"):
            return "orca-terminal", f"live Orca terminal at {t}"
    for w in orca_worktree_paths():
        if p == w or p.startswith(w.rstrip("/") + "/"):
            return "orca-worktree", f"Orca worktree with live workers at {w}"
    for r in orca_repo_paths():
        if p == r or p.startswith(r.rstrip("/") + "/"):
            return "orca-repo", f"registered Orca repo at {r}"
    return "", ""


# macOS runs several hundred system daemons that are legitimately reparented to
# launchd. `launchctl list` only reports the *user* domain, so those daemons look
# unowned by every other test. Excluding system-owned executables takes the
# candidate set from ~745 to ~24 -- the difference between a false-positive flood
# and a usable signal.
SYSTEM_PREFIXES = (
    "/System/", "/usr/libexec/", "/usr/sbin/", "/usr/bin/", "/sbin/", "/bin/",
    "/Library/Apple/", "/private/var/db/", "/Library/PrivilegedHelperTools/",
)


def is_system_process(command: str) -> bool:
    exe = command.split()[0] if command.split() else command
    return exe.startswith(SYSTEM_PREFIXES)


@dataclass
class Process:
    pid: int
    ppid: int
    etime_s: int
    command: str
    uid: int = -1
    cwd: str = ""
    classification: str = UNKNOWN
    evidence: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _etime_seconds(etime: str) -> int:
    """Parse ps ELAPSED ([[dd-]hh:]mm:ss) into seconds."""
    etime = etime.strip()
    days = 0
    if "-" in etime:
        d, etime = etime.split("-", 1)
        days = int(d) if d.isdigit() else 0
    parts = [int(x) if x.isdigit() else 0 for x in etime.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return days * 86400 + h * 3600 + m * 60 + s


def _cwd_of(pid: int) -> str:
    out = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], timeout=8)
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def list_processes(patterns: tuple[str, ...] = ()) -> list[Process]:
    """Snapshot processes. `patterns` narrows by command substring."""
    out = _run(["ps", "-Ao", "uid=,pid=,ppid=,etime=,args="])
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        uid_s, pid_s, ppid_s, etime, args = parts
        if not pid_s.isdigit():
            continue
        if patterns and not any(pat in args for pat in patterns):
            continue
        procs.append(Process(
            pid=int(pid_s), ppid=int(ppid_s),
            etime_s=_etime_seconds(etime), command=args,
            uid=int(uid_s) if uid_s.isdigit() else -1,
        ))
    return procs


def reap_candidates(procs: list[Process] | None = None) -> list[Process]:
    """Processes worth classifying: ours, reparented, and not system-owned.

    Anything excluded here can never be reported as a leak, which is the
    intended bias -- a missed orphan costs disk, a false positive costs work.
    """
    me = os.getuid()
    lp = launchd_pids()
    return [
        p for p in (procs if procs is not None else list_processes())
        if p.uid == me
        and p.ppid <= 1
        and p.pid not in lp
        and not is_system_process(p.command)
    ]


def classify(p: Process, resolve_cwd: bool = True) -> Process:
    """Classify one process. Ownership evidence beats orphan heuristics."""
    # 1. A known leak signature is the only fast path to "definitely orphaned",
    #    and even then only once its parent is gone.
    known_leak = any(pat in p.command for pat in KNOWN_LEAK_PATTERNS)

    # 2. launchd manages this PID directly -- never an orphan.
    if p.pid in launchd_pids():
        p.classification = OWNED
        p.evidence = "managed by launchd"
        return p

    # 3. Live parent that isn't launchd: something is supervising it.
    if p.ppid > 1:
        p.classification = OWNED
        p.evidence = f"live parent pid {p.ppid}"
        return p

    # 4. Reparented to launchd (ppid==1). NOT sufficient for orphan --
    #    this is exactly where the near-miss would have happened.

    # 4a. A helper of a running app bundle. Electron/Chromium helpers reparent
    #     by design; Orca's own crashpad handler surfaced here first.
    owner, why = app_bundle_owner(p.command)
    if owner and not known_leak:
        p.classification = OWNED
        p.evidence = why
        return p

    # 4b. Working inside something Orca is actively running.
    if resolve_cwd:
        p.cwd = _cwd_of(p.pid)
    if p.cwd:
        owner, why = path_owner(p.cwd)
        if owner:
            p.classification = OWNED
            p.evidence = why
            return p

    if known_leak:
        p.classification = ORPHAN
        p.evidence = f"known leak signature, parent gone, up {p.etime_s // 3600}h"
        return p

    if p.etime_s > STALE_AGE:
        p.classification = LIKELY_ORPHAN
        p.evidence = f"parent gone, no Orca owner, up {p.etime_s // 3600}h"
        return p

    p.classification = UNKNOWN
    p.evidence = "parent gone but recently started; too new to judge"
    return p


def reset_cache() -> None:
    """Drop memoised Orca/launchd state (long-lived callers)."""
    for fn in (orca_terminals, orca_worktrees, orca_terminal_paths,
               orca_worktree_paths, orca_repo_paths, orca_orphaned_terminals,
               orca_unreleased_workers, launchd_labels, launchd_pids,
               running_app_bundles):
        fn.cache_clear()
