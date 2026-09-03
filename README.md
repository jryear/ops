# ops

Shared operator primitives for this machine. One implementation, called the same
way by Claude, Codex, Orca, Kiki, a shell alias or a cron job.

```
ops <verb> [--json] [--quiet]     # ~/.local/bin/ops -> this repo's ./ops
ops                               # no verb: prints help and the verb list
```

Stdlib-only Python, no install step. Read-only unless a destructive flag is
passed explicitly.

Installed as a **symlink** on `PATH`, not a shell alias: an alias exists only
inside interactive zsh, so scripts, cron, Codex and Orca would not see it. The
point of this package is that every caller resolves the same implementation.

```sh
ln -sf ~/dev/agents/ops/ops ~/.local/bin/ops
```

## Verbs

| verb | question it answers | cost |
|---|---|---|
| `triage` | What needs Zack right now? | ~4s |
| `safe` | Is valuable work stranded in one fragile place? | ~3s, 0.5s with `--root` |
| `reap` | What processes are leaked? | ~1s |
| `machine` | Is this box healthy enough to keep working? | ~1s |
| `newproj` | Create a project correctly, first time | network |
| `registry` | `show` / `render` / `reconcile` the project index | ~4s / network |

## Feature freeze

**As of 3 September 2026 this is done.** The six verbs answer the six questions
Phase 6 set out to answer, and that is the whole scope:

> What needs my attention? · Is my work durable? · What's leaking? ·
> Is my machine healthy? · How do I start something correctly? ·
> What projects actually exist?

From here, **Orca, Hermes and Saturn development create the requirement for the
next `ops` feature — not imagination.** If you are about to add a verb because it
sounds useful, that is the signal to stop. Fixing a verb that returns a wrong
answer is always in scope; adding capability on speculation is not.

The point of this layer is that it stays small enough to be trusted by five
runtimes at once. A substrate that keeps growing stops being a substrate.

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
- **`origin` identifies a repo; *any* reachable remote makes it durable.** Those
  are different questions and conflating them cost real accuracy.
  `discover` used to take the first remote git listed, which is alphabetical, so
  adding a `backup` remote silently made it look canonical. Meanwhile the
  durability check only looked at `origin` — which would call a repo unbacked
  forever if it deliberately keeps a dead origin for provenance, exactly what
  `bridge-agent-workspace` does.
- **Never compare against a Hugging Face cache without `find -L`.** The cache
  stores real files in `blobs/` and exposes them as symlinks under `snapshots/`.
  `find -type f` does not follow symlinks, so it finds no weights at all and
  every comparison returns the hash of empty input. That read as "duplicate" and
  nearly deleted 17 GB of models that existed nowhere else. Verify by content
  signature, per file, and only trust an exact match.
- **The identity-collision finding must not conclude.** Two checkouts sharing a
  remote can mean one product evolved into the other, one is an old checkout, a
  remote was repointed by accident, one directory is a historical alias, or two
  divergent histories are aimed at one repository. Only the last is dangerous
  and only a human can choose. Tip containment does not settle it: it says
  nothing about uncommitted work or which checkout a running service points at,
  and in the live case those pointed the other way. `tests/test_registry.py`
  pins this with a list of verdict phrases that must not appear.

## Tests

```
for t in tests/test_*.py; do python3 "$t"; done
```

The cases are drawn from things that actually happened on this machine, not
invented — `tests/test_ownership.py` leads with the near-miss that would have
killed live work.
