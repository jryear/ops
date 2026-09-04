"""Repository and worktree discovery.

The audit that motivated this package searched for `.git` *directories* only.
That missed 40 saturn-office worktrees and mislabelled 38 mercahq worktrees as
"no git at all" -- they use `.git` *pointer files* (`gitdir: /path/to/...`).
Everything here matches both forms. That is the whole reason this module exists.

Stdlib only. Read-only: nothing in this module mutates a repository.
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path

HOME = Path.home()

# Roots worth scanning. Ordered roughly by how likely durable work lives there.
DEFAULT_ROOTS = [
    HOME / "dev",
    HOME / "Desktop",
    HOME / "mercahq",
    HOME / "Junior",
    HOME / "orca" / "workspaces",
    HOME / "seed",
    HOME / "Documents",
    HOME / "operator-system",
]

# Directory names never worth descending into. Keeps a full scan to seconds.
PRUNE = {
    "node_modules", "target", ".venv", "venv", "__pycache__", ".Trash",
    ".cache", "dist", ".next", "build", "DerivedData", "Library",
    ".pytest_cache", "site-packages", ".mypy_cache", "blobs",
}

# Depth beyond which we stop looking. Worktrees nest ~2 deep under a repo.
MAX_DEPTH = 6

# A repo with at most this many commits and no remote is probably scratch,
# not durable work worth alarming about.
TRIVIAL_COMMITS = 1


def _git(repo: Path, *args: str, timeout: int = 15) -> str:
    """Run a read-only git command. Returns '' on any failure."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


@dataclass
class Repo:
    path: str
    kind: str                      # "repo" | "worktree"
    branch: str = ""
    remote: str = ""
    remote_url: str = ""
    commits: int = 0
    unpushed: int = 0
    dirty: int = 0                 # unbacked: untracked, modified or added
    deleted: int = 0               # deletions -- content is still in history
    last_commit: str = ""
    parent_repo: str = ""          # for worktrees: the repo owning the object store
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def find_git_paths(roots=None, max_depth: int = MAX_DEPTH) -> list[tuple[Path, bool]]:
    """Find every git checkout under `roots`.

    Returns (checkout_path, is_worktree). `is_worktree` is True when `.git` is a
    pointer file rather than a directory -- the case the original audit missed.
    """
    roots = [Path(r) for r in (roots or DEFAULT_ROOTS)]
    found: list[tuple[Path, bool]] = []
    seen: set[str] = set()

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(os.scandir(d))
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            return

        for e in entries:
            if e.name != ".git":
                continue
            # A directory is a normal repo; a file is a worktree/submodule pointer.
            real = os.path.realpath(d)
            if real not in seen:
                seen.add(real)
                found.append((d, e.is_file()))
            # Do not descend past a checkout boundary except into .worktrees,
            # which is where Orca and saturn-office keep nested worktrees.
            break

        for e in entries:
            if not e.is_dir(follow_symlinks=False):
                continue
            if e.name in PRUNE:
                continue
            if e.name.startswith(".") and e.name not in (".worktrees",):
                continue
            walk(Path(e.path), depth + 1)

    for r in roots:
        if r.exists():
            walk(r, 0)
    return found


def _worktree_parent(checkout: Path) -> str:
    """Read the parent repo out of a `.git` pointer file."""
    try:
        txt = (checkout / ".git").read_text(errors="replace").strip()
    except OSError:
        return ""
    if txt.startswith("gitdir:"):
        gitdir = txt.split(":", 1)[1].strip()
        # .../<parent>/.git/worktrees/<name>  ->  <parent>
        marker = "/.git/worktrees/"
        if marker in gitdir:
            return gitdir.split(marker)[0]
        return gitdir
    return ""


def inspect(checkout: Path, is_worktree: bool) -> Repo:
    """Collect read-only git facts for one checkout."""
    r = Repo(path=str(checkout), kind="worktree" if is_worktree else "repo")

    if is_worktree:
        r.parent_repo = _worktree_parent(checkout)

    # A checkout whose parent object store is gone is broken, not empty.
    if _git(checkout, "rev-parse", "--git-dir") == "":
        r.error = "unreadable git dir (parent repo missing?)"
        return r

    r.branch = _git(checkout, "rev-parse", "--abbrev-ref", "HEAD")
    # `origin` is what git and everyone else mean by "the remote". Taking the
    # first name listed picks alphabetically, which silently made
    # bridge-agent-workspace's `backup` remote look canonical the moment one was
    # added. Whether a repo is *durable* is a different question, answered by
    # registry._reachable() across every remote.
    remotes = (_git(checkout, "remote") or "").split()
    r.remote = "origin" if "origin" in remotes else (remotes[0] if remotes else "")
    if r.remote:
        r.remote_url = _git(checkout, "remote", "get-url", r.remote)

    commits = _git(checkout, "rev-list", "--all", "--count")
    r.commits = int(commits) if commits.isdigit() else 0

    if r.remote:
        unpushed = _git(checkout, "log", "--branches", "--not", "--remotes", "--oneline")
        r.unpushed = len([l for l in unpushed.split("\n") if l.strip()])
    else:
        # With no remote, every commit is unpushed by definition.
        r.unpushed = r.commits

    status = _git(checkout, "status", "--porcelain")
    # Split unbacked content from deletions. A deleted-but-uncommitted file
    # still has its content in git history and is recoverable with a checkout;
    # counting it as work-at-risk overstated this machine's exposure ~5x
    # (663 reported vs 135 real), and a durability check that cries wolf is a
    # durability check that gets ignored.
    lines = [l for l in status.split("\n") if l.strip()]
    r.deleted = len([l for l in lines if l[:2].strip().startswith("D")
                     or (len(l) > 1 and l[1] == "D")])
    r.dirty = len(lines) - r.deleted

    r.last_commit = _git(checkout, "log", "-1", "--format=%cI")
    return r


def scan(roots=None, workers: int = 16) -> list[Repo]:
    """Discover and inspect every checkout. The primary entry point.

    Inspection is ~6 git subprocess calls per checkout and there are typically
    150+ checkouts here, so it runs on a thread pool. The work is process-spawn
    bound rather than CPU bound, so threads are the right tool and every git
    call is read-only, making concurrency safe.
    """
    paths = find_git_paths(roots)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(lambda t: inspect(t[0], t[1]), paths))
    return sorted(out, key=lambda r: r.path)
