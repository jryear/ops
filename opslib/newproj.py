"""newproj -- create a project correctly, the first time.

This exists because of what the audit found: 18 repos with real history and no
remote anywhere, one of them 90 commits deep. None of that was carelessness --
it is what happens when "git init, and also remember eleven other things"
depends on remembering. So the eleven things become one command, and the
default is the safe one.

The steps are ordered so that a failure never leaves a half-made project that
looks finished: the remote is created and pushed *before* the registry entry
exists, so an entry in the registry always means the work is backed up.

Defaults to `warm`: a canonical path, a private remote and an identity, with no
Hermes project or Orca execution attached. Most new things are warm. `--active`
is opt-in, because coordination overhead should be a decision, not a side
effect of typing `newproj`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import registry
from .model import ACTION, OK, WARN

HOME = Path.home()
DEV = HOME / "dev"

# Matches the machine layout documented in ~/.claude/CLAUDE.md.
AREAS = ("saturn", "merca", "agents", "labs")

NAME_OK = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

GITIGNORE = """# Project-specific ignores only.
# Build output, caches, .env* and agent session logs are already covered
# globally by ~/.gitignore_global -- do not duplicate them here.
"""

SEED_DOC = """# {name}

{description}

- Area: `~/dev/{area}/`
- Lifecycle: **{lifecycle}** ({lifecycle_desc})
- Created: {date}

## Conventions

Machine-wide conventions live in `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`.
Record here only what is specific to this project.
"""


class Failed(Exception):
    """A step failed; nothing further should run."""


def _run(cmd, cwd=None, timeout=120) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise Failed(f"{' '.join(cmd[:3])}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def _gh_user() -> str:
    try:
        return _run(["gh", "api", "user", "--jq", ".login"], timeout=30)
    except (Failed, subprocess.SubprocessError, OSError) as e:
        raise Failed(f"gh is not usable ({e}). Run `gh auth login` first.")


def plan(name: str, area: str, lifecycle: str, description: str) -> dict:
    """Everything that will happen, resolved but not yet done."""
    if not NAME_OK.match(name):
        raise Failed(f"'{name}' is not a usable project name "
                     "(lowercase letters, digits, . _ - ; must not start with -)")
    if lifecycle not in registry.LIFECYCLES:
        raise Failed(f"unknown lifecycle '{lifecycle}'")
    path = DEV / area / name
    if path.exists():
        raise Failed(f"{path} already exists")
    return {
        "name": name,
        "area": area,
        "path": str(path),
        "lifecycle": lifecycle,
        "description": description or f"{name} ({area})",
        "steps": [
            f"mkdir -p {path}",
            "git init -b main",
            "write .gitignore, CLAUDE.md, AGENTS.md",
            "git commit (first commit)",
            f"gh repo create --private --source . --push",
            f"register in registry.toml as lifecycle={lifecycle}",
        ],
    }


def create(p: dict) -> dict:
    path = Path(p["path"])
    path.mkdir(parents=True)
    ctx = dict(name=p["name"], description=p["description"], area=p["area"],
               lifecycle=p["lifecycle"],
               lifecycle_desc=registry.LIFECYCLES[p["lifecycle"]],
               date=datetime.now(timezone.utc).date().isoformat())

    (path / ".gitignore").write_text(GITIGNORE)
    doc = SEED_DOC.format(**ctx)
    (path / "CLAUDE.md").write_text(doc)
    # Codex reads AGENTS.md; one file, two names, so neither runtime is special.
    (path / "AGENTS.md").write_text(doc)
    (path / "README.md").write_text(f"# {p['name']}\n\n{p['description']}\n")

    _run(["git", "init", "-b", "main"], cwd=path)
    _run(["git", "add", "-A"], cwd=path)
    _run(["git", "commit", "-m",
          f"Initial commit: {p['name']}\n\nCreated by `ops newproj`."], cwd=path)

    owner = _gh_user()
    slug = f"{owner}/{p['name']}"
    # --push here is the whole point: the remote exists before a second commit
    # can ever be made locally.
    _run(["gh", "repo", "create", slug, "--private", "--source", ".",
          "--push", "--description", p["description"]], cwd=path, timeout=180)
    remote = _run(["git", "remote", "get-url", "origin"], cwd=path)

    registry.add(registry.Entry(
        id=p["name"], name=p["name"], path=str(path), remote=f"github.com/{slug}",
        lifecycle=p["lifecycle"],
        runtime="orca" if p["lifecycle"] == "active" else "none",
        description=p["description"],
        last_verified=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))
    return {"path": str(path), "remote": remote, "slug": slug,
            "lifecycle": p["lifecycle"]}


def run(name: str, area: str = "labs", lifecycle: str = "warm",
        description: str = "", dry_run: bool = False,
        as_json: bool = False) -> int:
    try:
        p = plan(name, area, lifecycle, description)
    except Failed as e:
        print(f"newproj: {e}", file=sys.stderr)
        return ACTION

    if area not in AREAS:
        print(f"note: '{area}' is not one of {', '.join(AREAS)}; "
              f"creating it under ~/dev anyway.", file=sys.stderr)

    if dry_run:
        if as_json:
            json.dump({"dry_run": True, **p}, sys.stdout, indent=2)
            print()
        else:
            print(f"would create {p['path']}  (lifecycle: {p['lifecycle']})")
            for s in p["steps"]:
                print(f"  - {s}")
        return OK

    try:
        got = create(p)
    except Failed as e:
        print(f"newproj: {e}", file=sys.stderr)
        print(f"newproj: stopped before registering; {p['path']} may exist "
              f"but is not recorded as a project.", file=sys.stderr)
        return ACTION

    if as_json:
        json.dump({"created": True, **got}, sys.stdout, indent=2)
        print()
    else:
        print(f"created {got['path'].replace(str(HOME), '~')}")
        print(f"  remote     {got['remote']}")
        print(f"  lifecycle  {got['lifecycle']} "
              f"({registry.LIFECYCLES[got['lifecycle']]})")
    return OK
