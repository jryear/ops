"""machine -- is this box healthy enough to keep working?

Abnormality-led, not a telemetry dump. Every threshold below traces to something
actually observed during the audit that motivated this package:

  * `fileproviderd` held 119% CPU for seven days syncing build artifacts to
    iCloud, which is what a runaway FileProvider looks like.
  * A `next dev` server burned a full core for five days after its parent died.
  * Disk reached 672/926 GB with 84 GB of it duplicate Rust builds.

So the checks are: is a core being burned by something that shouldn't, is disk
about to become a problem, and is the machine carrying more processes than the
work justifies.

Deliberately *not* here: `brctl status`. It is the obvious way to read the
iCloud queue depth and it hangs indefinitely while the queue is draining --
exactly when you would want to run it. A health check that blocks is worse than
one that is absent, and `fileproviderd` CPU already carries the same signal.
"""

from __future__ import annotations

import os
import math
import shutil
import subprocess
from pathlib import Path

from . import ownership as ow
from .model import ACTION, OK, WARN, Report

# Free space below this and builds/model downloads start failing.
DISK_WARN_GB = 100
DISK_ACTION_GB = 40

# Sustained load above (cores x this) means the machine is saturated.
LOAD_WARN_RATIO = 1.5

# A single non-interactive process holding this much CPU is doing something
# unintended far more often than not.
CPU_WARN_PCT = 80.0

# macOS's own indexers and sync daemons. These legitimately saturate cores for
# hours after a large change on disk, and must never be suggested for reaping.
# Reported as one finding so a normal post-cleanup reindex reads as one event.
HOUSEKEEPING = {
    "fileproviderd": "iCloud sync",
    "bird": "iCloud sync",
    "mds_stores": "Spotlight index",
    "mds": "Spotlight index",
    "mdworker_shared": "Spotlight index",
    "corespotlightd": "Spotlight index",
    "backupd": "Time Machine",
    "photoanalysisd": "Photos analysis",
}
HOUSEKEEPING_PCT = 50.0

# Ours, and expected to be busy while we work. Not worth reporting on CPU alone.
EXPECTED_BUSY = ("WindowServer", "Virtualization", "kernel_task", "cargo",
                 "rustc", "node", "python", "claude", "codex", "Chrome",
                 "Terminal", "docker", "com.docker")


def _run(cmd, timeout=10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _disk(rep: Report) -> None:
    du = shutil.disk_usage("/System/Volumes/Data")
    free_gb = du.free / 2**30
    used_gb = du.used / 2**30
    rep.checked["disk_free_gb"] = round(free_gb)
    rep.checked["disk_used_gb"] = round(used_gb)

    if free_gb < DISK_ACTION_GB:
        rep.add(ACTION, "disk.critical", f"{free_gb:.0f} GB free",
                f"below {DISK_ACTION_GB} GB - builds and downloads will fail",
                "ops triage; then reclaim build artifacts or model weights.")
    elif free_gb < DISK_WARN_GB:
        rep.add(WARN, "disk.low", f"{free_gb:.0f} GB free",
                f"below {DISK_WARN_GB} GB",
                "Clear build caches before starting anything large.")


def _cpu(rep: Report) -> tuple[list[str], list[str]]:
    """Returns (housekeeping_labels, hog_labels) so _load can explain itself."""
    out = ow.observe_command(["ps", "-Ao", "pcpu=,pid=,comm=", "-r"])
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 2)
        try:
            valid = (len(parts) == 3 and parts[1].isdigit() and int(parts[1]) >= 0
                     and math.isfinite(float(parts[0])) and float(parts[0]) >= 0)
        except ValueError:
            valid = False
        if not valid:
            raise ow.ObservationUnavailable("ps inspection returned unusable CPU rows")
        rows.append(parts)
    rep.checked["cpu_observation"] = "available"
    house: dict[str, float] = {}
    house_names: list[str] = []
    hogs: list[str] = []

    for parts in rows[:15]:
        pct = float(parts[0])
        if pct < HOUSEKEEPING_PCT:
            break
        pid, comm = parts[1], parts[2]
        name = Path(comm).name

        if name in HOUSEKEEPING:
            label = HOUSEKEEPING[name]
            house[label] = house.get(label, 0.0) + pct
            house_names.append(f"{name} {pct:.0f}%")
            continue
        if pct < CPU_WARN_PCT:
            continue
        if any(x in comm for x in EXPECTED_BUSY):
            continue
        if ow.is_system_process(comm):
            # A system daemon we don't recognise. Worth naming, never reaping.
            rep.add(WARN, "cpu.system", f"{name} (pid {pid}) at {pct:.0f}%",
                    "macOS system process holding a core",
                    "Not ours to kill; usually resolves itself, otherwise reboot.",
                    {"pid": pid, "cpu": pct})
            hogs.append(f"{name} {pct:.0f}%")
            continue
        rep.add(WARN, "cpu.hog", f"{name} (pid {pid}) at {pct:.0f}%",
                "single process holding a core",
                "Confirm it is wanted; ops reap if it has no owner.",
                {"pid": pid, "cpu": pct})
        hogs.append(f"{name} {pct:.0f}%")

    if house:
        total = sum(house.values())
        what = ", ".join(f"{k} ({v:.0f}%)" for k, v in sorted(house.items()))
        rep.add(WARN, "os.housekeeping", f"macOS reindexing: {what}",
                "expected for hours after a large change on disk; it settles on "
                "its own and is not a leak",
                "Leave it. If it persists for days, check ~/Desktop and "
                "~/Documents contain no build output.",
                {"total_cpu": round(total, 1), "processes": house_names})
    return house_names, hogs


def _load(rep: Report, housekeeping: list[str], hogs: list[str]) -> None:
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        return
    cores = os.cpu_count() or 1
    rep.checked["load_1m"] = round(one, 1)
    rep.checked["load_15m"] = round(fifteen, 1)
    rep.checked["cores"] = cores

    if fifteen <= cores * LOAD_WARN_RATIO:
        return

    if housekeeping and not hogs:
        why = "attributable to macOS housekeeping above, not to your work"
        act = "No action; re-run once indexing settles."
    elif hogs:
        why = f"contributors: {', '.join(hogs)}"
        act = "Address the CPU findings above."
    elif rep.checked.get("cpu_observation") == "unavailable":
        why = "CPU process observation unavailable; contributors cannot be determined"
        act = "Restore inspection access and retry."
    else:
        why = ("no single process accounts for it - usually many small "
               "processes or heavy disk I/O wait")
        act = "ops reap to check for accumulated leaks; otherwise wait it out."

    rep.add(WARN, "load.sustained",
            f"load {fifteen:.1f} over 15m on {cores} cores", why, act)


def _docker(rep: Report) -> None:
    out = _run(["docker", "ps", "-q"], timeout=20)
    if not out:
        return
    n = len([x for x in out.splitlines() if x.strip()])
    rep.checked["docker_containers"] = n


def _processes(rep: Report) -> None:
    procs = ow.list_processes()
    rep.checked["processes"] = len(procs)
    leaks = [p for p in (ow.classify(c) for c in ow.reap_candidates(procs))
             if p.classification in (ow.ORPHAN, ow.LIKELY_ORPHAN)]
    rep.checked["leaked_processes"] = len(leaks)
    if leaks:
        rep.add(WARN, "process.leaks", f"{len(leaks)} unowned process(es)",
                "no live parent and no Orca or app owner",
                "ops reap")


def run() -> Report:
    rep = Report(verb="machine")
    _disk(rep)
    try:
        house, hogs = _cpu(rep)
    except ow.ObservationUnavailable as exc:
        house, hogs = [], []
        ow.report_unavailable(rep, exc, "cpu")
    _load(rep, house, hogs)
    _docker(rep)
    try:
        _processes(rep)
        rep.checked["process_observation"] = "available"
    except ow.ObservationUnavailable as exc:
        ow.report_unavailable(rep, exc)
    return rep
