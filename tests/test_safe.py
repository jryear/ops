"""Repository risk classification tests.

Uses synthetic Repo records so the assertions hold regardless of what the
machine happens to look like today. The cases mirror real situations:
saturn-office (90 commits, no remote) and the 53 Orca-leased worktrees that
legitimately carry unpushed work.

Run: python3 tests/test_safe.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opslib import safe  # noqa: E402
from opslib.discover import Repo  # noqa: E402

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got={got!r} want={want!r}")
        FAILS.append(name)


def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def repo(**kw):
    base = dict(path="/tmp/x", kind="repo", branch="main", remote="origin",
                remote_url="https://example/x", commits=10, unpushed=0, dirty=0,
                deleted=0, last_commit=iso(1))
    base.update(kw)
    return Repo(**base)


ORCA_PATH = "/Users/jryear/orca/workspaces/demo/wt"
OWNED = {ORCA_PATH}

print("=== critical: work that exists nowhere else ===")
# The saturn-office case: 90 commits, 60 branches, no remote, no Time Machine.
state, ev, act = safe.classify(repo(commits=90, remote="", unpushed=90), OWNED)
check("90 commits + no remote -> critical", state, safe.CRITICAL)
check("evidence says only copy", "only copy" in ev, True)
check("suggests creating a remote", "gh repo create" in act, True)

# A worktree with no remote is equally stranded.
state, _, _ = safe.classify(repo(kind="worktree", commits=43, remote=""), OWNED)
check("worktree, no remote, real history -> critical", state, safe.CRITICAL)

print()
print("=== not critical: scratch repos ===")
state, _, _ = safe.classify(repo(commits=1, remote="", unpushed=1), OWNED)
check("single-commit scratch repo -> not critical", state != safe.CRITICAL, True)
state, _, _ = safe.classify(repo(commits=0, remote="", unpushed=0), OWNED)
check("empty repo -> not critical", state != safe.CRITICAL, True)

print()
print("=== expected: active Orca work must not be alarming ===")
state, ev, _ = safe.classify(
    repo(path=ORCA_PATH, unpushed=7, dirty=12, last_commit=iso(0)), OWNED)
check("Orca-leased worktree with unpushed+dirty -> expected", state, safe.EXPECTED)
check("evidence names Orca", "Orca" in ev, True)

# Nested under an Orca worktree counts as owned too.
state, _, _ = safe.classify(
    repo(path=ORCA_PATH + "/viewer", unpushed=3, dirty=5), OWNED)
check("path nested under Orca worktree -> expected", state, safe.EXPECTED)

state, _, _ = safe.classify(
    repo(path=ORCA_PATH, unpushed=0, dirty=0), OWNED)
check("clean Orca worktree -> healthy", state, safe.HEALTHY)

print()
print("=== at-risk: abandoned, unowned work ===")
state, _, _ = safe.classify(repo(unpushed=5, last_commit=iso(60)), OWNED)
check("5 unpushed, idle 60d, unowned -> at-risk", state, safe.AT_RISK)

state, _, _ = safe.classify(repo(unpushed=5, last_commit=iso(2)), OWNED)
check("5 unpushed but active 2d ago -> expected, not at-risk", state, safe.EXPECTED)

state, _, _ = safe.classify(repo(dirty=51, unpushed=0, last_commit=iso(300)), OWNED)
check("uncommitted files in a repo idle 300d -> at-risk", state, safe.AT_RISK)

state, ev, _ = safe.classify(repo(error="unreadable git dir"), OWNED)
check("broken worktree pointer -> at-risk", state, safe.AT_RISK)

print()
print("=== healthy ===")
state, _, _ = safe.classify(repo(), OWNED)
check("clean, synchronized -> healthy", state, safe.HEALTHY)

print()
print("=== severity mapping reaches the report ===")
from opslib.model import ACTION, WARN  # noqa: E402
check("CRITICAL is action-required, not a warning", ACTION > WARN, True)

print()
print("=== deletions are not unbacked work ===")
# imogenOS reported 535 "uncommitted files" of which 526 were deletions. Their
# content is still in history, so counting them as at-risk overstated this
# machine's exposure 5x. A durability check that cries wolf gets ignored.
state, ev, act = safe.classify(repo(dirty=0, deleted=526, last_commit=iso(329)), OWNED)
check("deletions alone -> not at-risk", state != safe.AT_RISK, True)
check("evidence says content is still in history", "still in history" in ev, True)

state, ev, _ = safe.classify(repo(dirty=9, deleted=526, last_commit=iso(329)), OWNED)
check("mixed -> at-risk on the unbacked count", state, safe.AT_RISK)
check("headline counts only unbacked", ev.startswith("9 unbacked"), True)
check("deletions still disclosed", "526 uncommitted deletion" in ev, True)

state, ev, _ = safe.classify(repo(dirty=16, deleted=0, last_commit=iso(371)), OWNED)
check("pure unbacked content -> at-risk", state, safe.AT_RISK)
check("no deletion clause when there are none", "deletion" in ev, False)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
    sys.exit(1)
print("all safe tests passed")
