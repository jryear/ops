# ops

Shared operator primitives for this machine. One implementation, called the same
way by Claude, Codex, Orca, Kiki, a shell alias or a cron job.

```
~/dev/agents/ops/ops <verb> [--json] [--quiet]
```

Stdlib-only Python, no install step. Read-only unless a destructive flag is
passed explicitly.

## Verbs

| verb | question it answers | cost |
|---|---|---|
| `triage` | What needs Zack right now? | ~4s |
| `safe` | Is valuable work stranded in one fragile place? | ~3s, 0.5s with `--root` |
| `reap` | What processes are leaked? | ~1s |
| `machine` | Is this box healthy enough to keep working? | ~1s |
| `newproj` | Create a project correctly, first time | network |
| `registry` | `show` / `render` / `reconcile` the project index | ~4s / network |

## Contract

Every verb honours the same three rules, so a caller never needs to know which
verb it invoked:

- **Text by default, `--json` for machines.** JSON is
  `{verb, exit_code, summary, checked, notes, findings[]}`, each finding carrying
  `severity_name`, `kind`, `subject`, `evidence`, `suggested_action`, `data`.
- **Exit code is the summary**: `0` healthy, `1` warnings, `2` action required.
  So `ops safe && deploy` behaves sensibly.
- **Read-only unless told otherwise.** `reap` reports; killing needs `--kill`
  *and* an explicit `--pid`, and refuses any process that resolved to an owner
  even when named directly.

## What owns what

`ops` owns none of the things it reads. It is a layer of machine capabilities
*beneath* the existing systems, never a competitor to them.

| system | owns |
|---|---|
| git / GitHub | artifact truth: code, history, remotes |
| Hermes | project control and messaging |
| Orca | execution: worktrees, terminals, workers, leases |
| Junior Core | coordination substrate (schema frozen behind SHA-256 hashes) |
| `ops` | machine capabilities shared by all of the above |

`registry.toml` stores exactly one fact no other system holds — **lifecycle**
(`active` / `warm` / `dormant` / `frozen`). Everything else in a registry view is
derived at read time from git, Orca and GitHub. The moment it caches something
git already knows, it becomes a fourth thing to keep in sync.

## Adapters

Thin, and containing no logic:

- **Claude** — `~/.claude/skills/{triage,safe,reap,machine,newproj}/SKILL.md`,
  plus a `Stop` hook (`hooks/stop-safe.sh`) that warns only when the repo just
  worked in holds the only copy of real work.
- **Anything else** — call `ops <verb> --json` and read the envelope.

If a verb is wrong, fix it in `opslib/`. Never work around it in an adapter, or
the runtimes stop agreeing about the machine.

## Design notes worth knowing before changing anything

- **Discovery matches `.git` as a directory *or* a pointer file.** The audit that
  motivated this package searched for directories only, missed 40 saturn-office
  worktrees and reported 38 mercahq worktrees as "no git at all".
- **`PPID == 1` is never treated as orphaned.** Five live `npm run dev` servers
  under `~/orca/workspaces/` looked exactly like orphans and were Orca-owned
  terminals. Ownership evidence always beats orphan heuristics.
- **`machine` does not call `brctl status`.** It is the obvious way to read the
  iCloud queue depth and it hangs indefinitely while the queue is draining —
  precisely when you would run it.
- **macOS indexers are one finding, not several leaks.** After a large change on
  disk, `fileproviderd` and `mds_stores` saturate cores for hours. That is the OS
  digesting work, and suggesting `ops reap` for it is actively wrong.

## Tests

```
for t in tests/test_*.py; do python3 "$t"; done
```

The cases are drawn from things that actually happened on this machine, not
invented — `tests/test_ownership.py` leads with the near-miss that would have
killed live work.
