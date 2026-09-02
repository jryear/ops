"""Ownership classification tests.

The cases here are the ones that would have caused real damage. Each is drawn
from something actually observed on this machine, not invented.

Run: python3 tests/test_ownership.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opslib import ownership as ow  # noqa: E402

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got={got!r} want={want!r}")
        FAILS.append(name)


def p(pid=9999, ppid=1, etime_s=7200, command="x", cwd=""):
    q = ow.Process(pid=pid, ppid=ppid, etime_s=etime_s, command=command)
    q.cwd = cwd
    return q


print("=== the near-miss: ppid==1 but inside an Orca worktree ===")
# An earlier audit would have killed these. Force the ppid==1 path so the
# live-parent shortcut cannot mask whether the Orca lookup works.
orca_paths = ow.orca_worktree_paths() or ow.orca_terminal_paths()
if orca_paths:
    inside = orca_paths[0] + "/viewer"
    got = ow.classify(p(command="node vite", cwd=inside), resolve_cwd=False)
    check("orphan-looking process in Orca worktree -> active-owned",
          got.classification, ow.OWNED)
    check("evidence names the Orca owner", "Orca" in got.evidence, True)
else:
    print("  SKIP  no live Orca worktrees to test against")

print()
print("=== true positives: real leaks the audit found ===")
# 12 of these were reparented to launchd with dead parents, up for days.
got = ow.classify(
    p(command="/opt/homebrew/.../Python -m browser_harness.daemon",
      etime_s=5 * 86400),
    resolve_cwd=False)
check("browser_harness daemon, parent gone -> definitely-orphaned",
      got.classification, ow.ORPHAN)

# Sparkle updaters wedged for 4 days.
got = ow.classify(
    p(command="/Applications/ChatGPT.app/.../Sparkle.framework/Versions/B/Autoupdate",
      etime_s=4 * 86400),
    resolve_cwd=False)
check("stuck Sparkle updater -> definitely-orphaned",
      got.classification, ow.ORPHAN)

print()
print("=== things that must never be called orphans ===")
got = ow.classify(p(ppid=42, command="node server.js"), resolve_cwd=False)
check("live parent -> active-owned", got.classification, ow.OWNED)

live_launchd = next(iter(ow.launchd_pids()), None)
if live_launchd:
    got = ow.classify(p(pid=live_launchd, ppid=1, command="some launchd job"),
                      resolve_cwd=False)
    check("launchd-managed pid -> active-owned", got.classification, ow.OWNED)

got = ow.classify(p(command="something new", etime_s=60), resolve_cwd=False)
check("recent, unowned, no leak signature -> unknown (not orphan)",
      got.classification, ow.UNKNOWN)

got = ow.classify(p(command="mystery", etime_s=9 * 3600), resolve_cwd=False)
check("old + unowned + no signature -> likely-orphaned (not definite)",
      got.classification, ow.LIKELY_ORPHAN)

print()
print("=== etime parsing ===")
check("mm:ss", ow._etime_seconds("07:35"), 455)
check("hh:mm:ss", ow._etime_seconds("20:37:07"), 74227)
check("dd-hh:mm:ss", ow._etime_seconds("05-22:27:46"), 512866)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
    sys.exit(1)
print("all ownership tests passed")
