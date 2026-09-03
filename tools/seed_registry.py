"""One-time seed of registry.toml from evidence already on disk.

Run once. Kept in the repo because the *rules* it applied are part of the
record -- if a lifecycle looks wrong later, this file says how it was chosen.

Three rules, all conservative:

1. **runtime** is `orca` only where Orca's own repo list claims the remote.
   That is an established fact. Everything else is `unknown`, which is a valid
   value; guessing would make the registry lie.

2. **lifecycle** is never auto-set to `active` or `frozen`. Active means Hermes
   or Orca coordination is attached, and frozen means finished or superseded --
   both are statements only Zack can make. The seed chooses between `warm` and
   `dormant` and nothing else.

3. **Idle time ignores the Phase 1 backup sweep.** 18 repos were committed and
   pushed during the backup work with messages like "Checkpoint before first
   push". Those commits are mine, not activity, and taking them at face value
   would mark long-abandoned repos as freshly worked. So idle is measured from
   the most recent commit that is *not* one of them.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opslib import discover, registry as reg  # noqa: E402

HOME = Path.home()
DORMANT_AFTER_DAYS = 180

# Commits written by the Phase 1 backup sweep, not by real work. Matched on
# subject AND date: "Checkpoint before first working push" in hermes-os is
# Zack's own wording and predates the sweep, so the subject alone over-matches.
SWEEP_SUBJECT = re.compile(r"^(Checkpoint before first push|Initial commit: )")
SWEEP_AFTER = "2026-09-01"


def _is_sweep(date: str, subject: str) -> bool:
    return date >= SWEEP_AFTER and bool(SWEEP_SUBJECT.match(subject))


def real_last_commit(path: str) -> tuple[str, bool]:
    """(ISO date of the newest non-sweep commit, whether sweep commits were skipped)."""
    try:
        out = subprocess.run(
            ["git", "-C", path, "log", "-30", "--format=%cI%x1f%s"],
            capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        return "", False
    if out.returncode != 0:
        return "", False
    skipped = False
    for line in out.stdout.splitlines():
        date, _, subject = line.partition("\x1f")
        if _is_sweep(date, subject):
            skipped = True
            continue
        return date, skipped
    return "", skipped


def project_id(remote_key: str, path: str, taken: set[str]) -> str:
    base = remote_key.rsplit("/", 1)[-1] if remote_key else Path(path).name
    pid = base.lower().replace(" ", "-")
    if pid not in taken:
        return pid
    # Two checkouts claiming one remote, or two dirs with one name. Disambiguate
    # by parent directory rather than a counter, so the id stays meaningful.
    return f"{Path(path).parent.name.lower()}-{pid}"


def main(apply: bool) -> int:
    now = datetime.now(timezone.utc)
    repos = [r for r in discover.scan()
             if r.kind == "repo" and r.commits > discover.TRIVIAL_COMMITS]
    orca = reg.orca_repos()
    entries: dict[str, reg.Entry] = {}

    def claim_rank(r):
        """Lower sorts first and so wins the clean id. A checkout whose directory
        name matches its remote is the canonical one; `tradewell` pointing at
        `merca-platform` is a clone and must not take that name."""
        key = reg._canonical_key(r.remote_url)
        name_matches = bool(key) and Path(r.path).name.lower() == key.rsplit("/", 1)[-1].lower()
        return (0 if name_matches else 1, -r.commits, r.path)

    rows = []
    for r in sorted(repos, key=claim_rank):
        key = reg._canonical_key(r.remote_url)
        last, skipped = real_last_commit(r.path)
        if last:
            idle = (now - datetime.fromisoformat(last)).days
        else:
            idle = 9999
        lifecycle = "dormant" if idle > DORMANT_AFTER_DAYS else "warm"
        runtime = "orca" if key and key in orca else "unknown"
        pid = project_id(key, r.path, set(entries))
        entries[pid] = reg.Entry(
            id=pid, name=pid, path=r.path, remote=key,
            lifecycle=lifecycle, runtime=runtime,
            description="",
            last_verified=now.isoformat(timespec="seconds"),
        )
        rows.append((pid, lifecycle, runtime, idle, skipped,
                     r.path.replace(str(HOME), "~")))

    rows.sort(key=lambda x: x[5])
    w = max(len(x[0]) for x in rows)
    print(f"{'ID':<{w}}  {'LIFECYCLE':<8}  {'RUNTIME':<7}  {'IDLE':>6}  PATH")
    print("-" * (w + 74))
    for pid, lc, rt, idle, skipped, path in rows:
        age = "never" if idle == 9999 else f"{idle}d"
        mark = " *" if skipped else ""
        print(f"{pid:<{w}}  {lc:<8}  {rt:<7}  {age:>6}{mark}  {path}")
    print()
    print("* idle measured from before the Phase 1 backup sweep")
    print(f"{len(entries)} projects  "
          f"({sum(1 for x in rows if x[1] == 'warm')} warm, "
          f"{sum(1 for x in rows if x[1] == 'dormant')} dormant)")

    if apply:
        reg.save(entries)
        print(f"\nwrote {reg.REGISTRY}")
    else:
        print("\ndry run -- pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
