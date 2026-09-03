"""registry -- a reconciling index, deliberately not a third source of truth.

Step A established why this shape and no other. Orca has a repo list, but it
covers only the 12 repos it manages and has no concept of lifecycle. Junior Core
has a `projects` table, but its schema is frozen behind SHA-256 hashes in
FROZEN_CORE_V01.json and its status column is
`CHECK (status IN ('active','paused','archived'))` -- it cannot express warm or
dormant. GitHub knows about remotes but nothing about what is on this disk.

So this file stores exactly one fact that no existing system holds --
**lifecycle** -- plus the minimum identity needed to join against the systems
that do own the rest. Everything else (branch, commits, unpushed work, whether
the remote still exists, which worktrees are live) is derived at read time.

That is the entire discipline. The moment this file starts caching facts git
already knows, it becomes a fourth thing to keep in sync, and the drift it was
built to detect starts living inside it.

`reconcile` reports drift; it never silently repairs it. Read-only apart from
`add`, which `newproj` calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from . import discover
from .model import ACTION, OK, WARN, Report

HOME = Path.home()
REGISTRY = Path(__file__).resolve().parents[1] / "registry.toml"

# Lifecycle is the one stored fact, so its meaning has to be unambiguous.
LIFECYCLES = {
    "active":  "being worked on now; has Hermes/Orca coordination",
    "warm":    "canonical repo and remote, no coordination overhead",
    "dormant": "not in progress; kept because it may return",
    "frozen":  "finished or superseded; kept for the record only",
}
DEFAULT_LIFECYCLE = "warm"

RUNTIMES = ("orca", "hermes", "junior", "codex", "claude", "none", "unknown")


@dataclass
class Entry:
    id: str
    name: str = ""
    path: str = ""
    remote: str = ""
    lifecycle: str = DEFAULT_LIFECYCLE
    runtime: str = "unknown"
    description: str = ""
    last_verified: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def load() -> dict[str, Entry]:
    if not REGISTRY.exists():
        return {}
    with REGISTRY.open("rb") as fh:
        raw = tomllib.load(fh)
    out = {}
    for pid, body in (raw.get("project") or {}).items():
        out[pid] = Entry(id=pid, **{k: v for k, v in body.items()
                                    if k in Entry.__annotations__ and k != "id"})
    return out


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(entries: dict[str, Entry]) -> None:
    """Hand-rolled writer: tomllib reads but does not write, and pulling in a
    dependency for six flat string fields would break the no-install contract."""
    lines = [
        "# ops project registry -- lifecycle only.",
        "#",
        "# This file is the source of truth for ONE thing: lifecycle. Everything",
        "# else is derived at read time from git, Orca and GitHub. Do not add",
        "# fields that another system already owns -- see opslib/registry.py.",
        "#",
        "# lifecycle: " + " | ".join(f"{k} ({v})" for k, v in LIFECYCLES.items()),
        "",
    ]
    for pid in sorted(entries):
        e = entries[pid]
        lines.append(f"[project.{pid}]")
        for f in ("name", "path", "remote", "lifecycle", "runtime",
                  "description", "last_verified"):
            v = getattr(e, f)
            if v:
                lines.append(f"{f} = {_toml_str(str(v))}")
        lines.append("")
    REGISTRY.write_text("\n".join(lines))


def add(entry: Entry) -> None:
    entries = load()
    entries[entry.id] = entry
    save(entries)


# --------------------------------------------------------------------------
# derivation -- everything the registry deliberately does not store
# --------------------------------------------------------------------------

def _cmd(cmd, timeout=25) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _orca(*args) -> dict:
    """Orca wraps every payload in {id, ok, result}; unwrap or return {}."""
    out = _cmd(["orca", *args, "--json"])
    if not out:
        return {}
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        return {}
    return env.get("result") or {} if env.get("ok") else {}


def orca_repos() -> dict[str, str]:
    """canonical remote key -> local path, for repos Orca manages."""
    out = {}
    for r in _orca("repo", "list").get("repos", []):
        ident = r.get("gitRemoteIdentity") or {}
        key = ident.get("canonicalKey") or r.get("path", "")
        if key:
            out[key] = r.get("path", "")
    return out


def _gh_owner(owner: str = "") -> set[str]:
    out = _cmd(["gh", "repo", "list", *([owner] if owner else []),
                "--limit", "300", "--json", "nameWithOwner"], timeout=40)
    if not out:
        return set()
    try:
        return {f"github.com/{r['nameWithOwner']}" for r in json.loads(out)}
    except (json.JSONDecodeError, KeyError, TypeError):
        return set()


def github_repos(owners: set[str] | None = None) -> set[str]:
    """The user's own repos, plus any org that a registered remote points at.

    Querying only the personal account made every org-owned repo -- mercahq,
    outsourc-e, PatientPassLLC -- report as "remote missing". A check that
    fires on healthy repos gets ignored, so the orgs are queried too.
    """
    found = _gh_owner()
    for o in sorted(owners or set()):
        if not any(k.startswith(f"github.com/{o}/") for k in found):
            found |= _gh_owner(o)
    return found


def _canonical_key(remote_url: str) -> str:
    """git@github.com:o/r.git and https://github.com/o/r.git -> github.com/o/r"""
    u = remote_url.strip()
    if not u:
        return ""
    for pre in ("git@", "ssh://git@", "https://", "http://"):
        if u.startswith(pre):
            u = u[len(pre):]
            break
    u = u.replace(":", "/", 1) if u.startswith("github.com:") else u
    return u[:-4] if u.endswith(".git") else u


def _reachable(path: str) -> str:
    """Name of the first remote we can actually fetch from, or "".

    Checks *every* remote, not just origin. bridge-agent-workspace keeps a dead
    `origin` on purpose -- it records who owned the history before access was
    lost -- and pushes to a `backup` remote instead. A durability check that only
    looks at origin would call that repo unbacked forever, which trains you to
    ignore it.

    Network, so callers should reach for this only after the cheap membership
    check has already failed.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        names = subprocess.run(["git", "-C", path, "remote"],
                               capture_output=True, text=True, timeout=10)
        remotes = names.stdout.split() if names.returncode == 0 else []
    except (subprocess.SubprocessError, OSError):
        return ""
    # origin first: it is right in the overwhelming majority of cases.
    for name in sorted(remotes, key=lambda n: (n != "origin", n)):
        try:
            r = subprocess.run(["git", "-C", path, "ls-remote", "--exit-code",
                                name, "HEAD"],
                               capture_output=True, text=True, timeout=20, env=env)
            if r.returncode == 0:
                return name
        except (subprocess.SubprocessError, OSError):
            continue
    return ""


def derive(entries: dict[str, Entry] | None = None) -> dict:
    """Join stored lifecycle against live git, Orca and GitHub state."""
    entries = load() if entries is None else entries
    repos = {r.path: r for r in discover.scan()}
    orca = orca_repos()
    owners = set()
    for e in entries.values():
        key = e.remote or ""
        if key.startswith("github.com/") and key.count("/") >= 2:
            owners.add(key.split("/")[1])
    gh = github_repos(owners)

    rows = []
    for pid, e in sorted(entries.items()):
        r = repos.get(e.path)
        key = _canonical_key(r.remote_url if r else e.remote)
        worktrees = [x for x in repos.values()
                     if x.kind == "worktree" and x.parent_repo == e.path]
        rows.append({
            "id": pid,
            "name": e.name or pid,
            "path": e.path,
            # A project can legitimately exist as a remote with no checkout on
            # this machine -- Orca manages several. That is different from a
            # declared path having vanished, which is real drift.
            "checked_out": bool(e.path),
            "lifecycle": e.lifecycle,
            "runtime": e.runtime,
            "description": e.description,
            "exists": r is not None,
            "remote": key,
            "remote_on_github": key in gh if key else False,
            "orca_managed": key in orca if key else False,
            "branch": r.branch if r else "",
            "commits": r.commits if r else 0,
            "unpushed": r.unpushed if r else 0,
            "dirty": r.dirty if r else 0,
            "last_commit": r.last_commit if r else "",
            "worktrees": len(worktrees),
        })
    return {"rows": rows, "known_repos": repos, "orca": orca, "github": gh}


# --------------------------------------------------------------------------
# verbs
# --------------------------------------------------------------------------

def _lineage(paths: dict[str, str], names: list[str]) -> str:
    """What can be established locally about two checkouts sharing a remote.

    Deliberately descriptive. A shared remote has several possible causes --
    one repo grew out of the other, one is a stale checkout, one was repointed
    by accident, or two genuinely divergent histories are aimed at the same
    place. Only the last is dangerous, and git can tell them apart, so state
    what is true and leave the decision alone.
    """
    if len(names) != 2:
        return "Inspect lineage before changing anything."
    a, b = (paths.get(n, "") for n in names)
    if not a or not b:
        return "Inspect lineage before changing anything."

    def root(p):
        out = _cmd(["git", "-C", p, "rev-list", "--max-parents=0", "HEAD"], 20)
        return out.strip().split("\n")[-1] if out.strip() else ""

    def tip(p):
        return _cmd(["git", "-C", p, "rev-parse", "HEAD"], 10).strip()

    def has(p, commit):
        return bool(commit) and subprocess.run(
            ["git", "-C", p, "cat-file", "-e", commit],
            capture_output=True).returncode == 0

    if root(a) != root(b):
        return ("Their root commits differ, so these are genuinely separate "
                "histories aimed at one remote - the case worth care.")
    ta, tb = tip(a), tip(b)
    a_has_b, b_has_a = has(a, tb), has(b, ta)
    if a_has_b and not b_has_a:
        return (f"Same root commit, and {names[0]} already contains "
                f"{names[1]}'s tip - so {names[1]} is a stale checkout with no "
                f"unique history, not a fork.")
    if b_has_a and not a_has_b:
        return (f"Same root commit, and {names[1]} already contains "
                f"{names[0]}'s tip - so {names[0]} is a stale checkout with no "
                f"unique history, not a fork.")
    if a_has_b and b_has_a:
        return "Same root commit and identical tips - one is a plain duplicate."
    return ("Same root commit but neither contains the other's tip - they have "
            "diverged and both hold unique commits.")


def reconcile() -> Report:
    """Report drift between the index and reality. Never repairs silently."""
    rep = Report(verb="registry")
    entries = load()
    d = derive(entries)
    repos, orca, gh = d["known_repos"], d["orca"], d["github"]

    rep.checked = {
        "registered": len(entries),
        "local_checkouts": len(repos),
        "orca_repos": len(orca),
        "github_repos": len(gh),
    }

    not_local = [r["name"] for r in d["rows"] if not r["checked_out"]]
    if not_local:
        rep.notes.append(
            f"{len(not_local)} project(s) registered with no checkout on this "
            f"machine: {', '.join(sorted(not_local))}"
        )
        rep.checked["not_checked_out"] = len(not_local)

    for row in d["rows"]:
        if not row["checked_out"]:
            continue
        if not row["exists"]:
            rep.add(ACTION, "registry.path_missing", row["name"],
                    f"registered at {row['path']}, which is not a git checkout",
                    "Correct the path in registry.toml, or drop the entry.",
                    {"id": row["id"]})
            continue
        if row["remote"] and gh and not row["remote_on_github"]:
            # `safe` deliberately stays local and fast so the Stop hook can run
            # it. Reachability costs network, so it lives here instead -- but a
            # remote that does not resolve is the same as no remote, and
            # bridge-agent-workspace's 82 commits were exactly that.
            via = _reachable(row["path"])
            if via and via != "origin":
                rep.notes.append(
                    f"{row['name']}: origin ({row['remote']}) does not resolve, "
                    f"but history is reachable via the '{via}' remote"
                )
                continue
            if via:
                continue
            sev = ACTION if row["commits"] > 1 else WARN
            rep.add(sev, "registry.remote_gone", row["name"],
                    f"no remote resolves - {row['commits']} commits exist only "
                    f"on this disk",
                    "Add a remote you control: gh repo create --private "
                    "&& git remote add backup <url> && git push backup --all",
                    {"id": row["id"], "path": row["path"]})
        if not row["remote"]:
            rep.add(ACTION, "registry.no_remote", row["name"],
                    f"{row['commits']} commits and no remote - the only copy",
                    "gh repo create --private --source . --push",
                    {"id": row["id"], "path": row["path"]})

    # Two checkouts claiming one remote means one of them is a clone that looks
    # canonical. `tradewell` and `merca-platform` shared a remote for months.
    by_remote: dict[str, list[str]] = {}
    for row in d["rows"]:
        if row["remote"]:
            by_remote.setdefault(row["remote"], []).append(row["name"])
    paths = {row["name"]: row["path"] for row in d["rows"]}
    for remote, names in sorted(by_remote.items()):
        if len(names) > 1:
            names = sorted(names)
            rep.add(WARN, "registry.identity_collision", ", ".join(names),
                    f"{len(names)} checkouts push to {remote}. "
                    + _lineage(paths, names),
                    "Reconciliation task, not a rename. Establish which "
                    "history is canonical before changing any remote.",
                    {"remote": remote, "projects": names,
                     "lineage": _lineage(paths, names)})

    # Durable local work the index has never heard of. Worktrees are excluded:
    # they belong to a registered repo, they are not projects in their own right.
    registered = {e.path for e in entries.values()}
    unregistered = [r for r in repos.values()
                    if r.kind == "repo" and r.path not in registered
                    and r.commits > discover.TRIVIAL_COMMITS]
    if unregistered:
        rep.notes.append(
            f"{len(unregistered)} local repo(s) with real history are not in the "
            f"registry. `ops registry show --json` lists what is; add the ones "
            f"that are projects rather than clones or scratch."
        )
        rep.checked["unregistered_repos"] = len(unregistered)
    return rep


def show(as_json: bool = False) -> int:
    d = derive()
    if as_json:
        json.dump({"projects": d["rows"]}, sys.stdout, indent=2)
        print()
        return OK
    rows = d["rows"]
    if not rows:
        print("registry is empty. `ops newproj <name>` registers as it creates;\n"
              "`ops registry reconcile` lists local repos worth adding.")
        return OK
    w = max(len(r["name"]) for r in rows)
    print(f"{'PROJECT':<{w}}  {'LIFECYCLE':<9}  {'RUNTIME':<8}  STATE")
    print("-" * (w + 42))
    for r in rows:
        state = []
        if not r["checked_out"]:
            state.append("not checked out here")
        elif not r["exists"]:
            state.append("MISSING")
        if not r["remote"]:
            state.append("no remote")
        if r["unpushed"]:
            state.append(f"{r['unpushed']} unpushed")
        if r["dirty"]:
            state.append(f"{r['dirty']} dirty")
        if r["worktrees"]:
            state.append(f"{r['worktrees']} worktrees")
        print(f"{r['name']:<{w}}  {r['lifecycle']:<9}  {r['runtime']:<8}  "
              f"{', '.join(state) or 'clean'}")
    return OK


def render() -> str:
    """The generated ARCHITECTURE.md. Registry is truth; this is a view."""
    d = derive()
    rows = d["rows"]
    out = [
        "<!-- generated by `ops registry render` - do not edit -->",
        "# Architecture",
        "",
        "Projects on this machine, grouped by lifecycle. Lifecycle is the only",
        "fact stored in `~/dev/agents/ops/registry.toml`; everything else here is",
        "derived from git, Orca and GitHub at render time.",
        "",
    ]
    for lc in ("active", "warm", "dormant", "frozen"):
        group = [r for r in rows if r["lifecycle"] == lc]
        if not group:
            continue
        out += [f"## {lc} — {LIFECYCLES[lc]}", ""]
        out.append("| project | path | remote | runtime | state |")
        out.append("|---|---|---|---|---|")
        for r in sorted(group, key=lambda x: x["name"]):
            path = r["path"].replace(str(HOME), "~")
            remote = r["remote"] or "*none*"
            state = []
            if not r["checked_out"]:
                state.append("*not checked out here*")
            elif not r["exists"]:
                state.append("**missing**")
            if r["unpushed"]:
                state.append(f"{r['unpushed']} unpushed")
            # Uncommitted work is the point of this column: hermes-os rendered
            # as "clean" while holding 14,831 uncommitted files.
            if r["dirty"]:
                state.append(f"{r['dirty']} uncommitted")
            if r["worktrees"]:
                state.append(f"{r['worktrees']} worktrees")
            out.append(f"| {r['name']} | `{path}` | {remote} | {r['runtime']} | "
                       f"{', '.join(state) or 'clean'} |")
        out.append("")

    out += [
        "## Ownership",
        "",
        "| system | owns |",
        "|---|---|",
        "| git / GitHub | artifact truth: code, history, remotes |",
        "| Hermes | project control and messaging |",
        "| Orca | execution: worktrees, terminals, workers, leases |",
        "| Junior Core | coordination substrate (frozen schema) |",
        "| `ops` | machine capabilities shared by all of the above |",
        "",
        "`ops` deliberately owns none of the above. It reads them and reports.",
        "",
    ]
    return "\n".join(out)


def cli(action: str, as_json: bool = False) -> int:
    from .model import emit
    if action == "show":
        return show(as_json)
    if action == "render":
        print(render(), end="")
        return OK
    if action == "reconcile":
        return emit(reconcile(), as_json)
    return OK
