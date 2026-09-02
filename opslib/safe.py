"""safe -- is any valuable work stranded in one fragile place?

The invariant is *not* "everything must be pushed". Plenty of unpushed work is
perfectly fine: a worker mid-task under an active Orca lease is supposed to have
uncommitted and unpushed changes. Alarming on that trains you to ignore the tool.

The invariant is: **work with real history must not exist in exactly one
fragile location.** `saturn-office` -- 90 commits across 60 branches, no remote,
on a disk with no Time Machine destination -- was the case that motivated this.

Classification is contextual. Never mutates a repository.

This module owns repository risk classification for the whole `ops` package;
`triage` calls in here rather than reimplementing it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import discover, ownership as ow
from .model import ACTION, OK, WARN, Report

HOME = Path.home()

# Fewer commits than this and a repo is scratch, not durable work.
MIN_DURABLE_COMMITS = 2

# Untouched this long with no owner => abandoned rather than in progress.
STALE_DAYS = 14

# Severity labels for the structured output.
CRITICAL = "critical"
AT_RISK = "at-risk"
EXPECTED = "expected"
HEALTHY = "healthy"


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


def classify(repo: discover.Repo, owned_paths: set[str]) -> tuple[str, str, str]:
    """Classify one checkout -> (state, evidence, suggested_action).

    Order matters: ownership is checked before staleness so that active work is
    never described as abandoned.
    """
    if repo.error:
        return (AT_RISK, repo.error,
                "Re-create the worktree or remove the stale .git pointer.")

    under_orca = any(
        repo.path == o or repo.path.startswith(o.rstrip("/") + "/")
        for o in owned_paths
    )
    age = _age_days(repo.last_commit)

    # 1. Durable history that exists nowhere else. The saturn-office case.
    if not repo.remote and repo.commits >= MIN_DURABLE_COMMITS:
        return (
            CRITICAL,
            f"{repo.commits} commits, no remote - this disk is the only copy",
            f"gh repo create --private --source={_short(repo.path)} && "
            f"git -C {_short(repo.path)} push --all",
        )

    # 2. Active work under an Orca lease. Unpushed here is expected, not a risk.
    if under_orca:
        if repo.unpushed or repo.dirty:
            return (EXPECTED,
                    f"{repo.unpushed} unpushed, {repo.dirty} dirty - "
                    f"active Orca worktree", "")
        return (HEALTHY, "clean, under active Orca execution", "")

    # 3. Unique unpushed work in a checkout nobody is touching.
    if repo.unpushed and repo.commits >= MIN_DURABLE_COMMITS:
        if age is not None and age > STALE_DAYS:
            return (
                AT_RISK,
                f"{repo.unpushed} unpushed commit(s), untouched {age:.0f}d, "
                f"no active owner",
                "Push it, or delete the branch if the work was abandoned.",
            )
        return (EXPECTED,
                f"{repo.unpushed} unpushed commit(s), recently active", "")

    # 4. Uncommitted-only changes: real, but git can still see them locally.
    if repo.dirty and age is not None and age > STALE_DAYS:
        return (AT_RISK,
                f"{repo.dirty} uncommitted file(s) in a checkout idle {age:.0f}d",
                "Commit or discard; uncommitted work is invisible to every backup.")

    return (HEALTHY, "synchronized", "")


def assess(roots=None) -> list[tuple[discover.Repo, str, str, str]]:
    """Classify every checkout. Shared by `ops safe` and `ops triage`."""
    repos = discover.scan(roots)
    owned = set(ow.orca_worktree_paths()) | set(ow.orca_terminal_paths())
    return [(r, *classify(r, owned)) for r in repos]


def run(roots=None) -> Report:
    rep = Report(verb="safe")
    results = assess(roots)

    tally = {CRITICAL: 0, AT_RISK: 0, EXPECTED: 0, HEALTHY: 0}
    for repo, state, evidence, action in results:
        tally[state] += 1
        if state == CRITICAL:
            rep.add(ACTION, "repo.unbacked", _short(repo.path), evidence, action,
                    {"path": repo.path, "commits": repo.commits, "kind": repo.kind})
        elif state == AT_RISK:
            rep.add(WARN, "repo.at_risk", _short(repo.path), evidence, action,
                    {"path": repo.path, "unpushed": repo.unpushed,
                     "dirty": repo.dirty, "kind": repo.kind})

    rep.checked = {
        "checkouts": len(results),
        "worktrees": sum(1 for r, *_ in results if r.kind == "worktree"),
        "critical": tally[CRITICAL],
        "at_risk": tally[AT_RISK],
        "expected": tally[EXPECTED],
        "healthy": tally[HEALTHY],
    }
    if tally[EXPECTED]:
        rep.notes.append(
            f"{tally[EXPECTED]} checkout(s) have unpushed work that is expected "
            f"(active Orca lease or recent local work) - not reported as risk"
        )
    return rep
