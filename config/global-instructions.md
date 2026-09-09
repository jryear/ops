# Global instructions

Mirrors `~/.claude/CLAUDE.md`. Keep the two in sync — Claude Code and Codex both
work on this machine and should share one picture of it.

## Who you're working with

Zack. Self-taught — effectively everything technical here was learned in the last
year, at high velocity. That means: strong architectural instincts and real
shipped systems, but the conventional scaffolding (backups, ignore files, build
caches) sometimes hasn't been laid down yet. Don't pitch explanations too low,
and don't assume a convention is known just because it's standard.

**Explain while you execute.** Say what you're doing and why the approach is the
right one — not as a lecture after the fact, but as part of the work. If you spot
something structurally wrong adjacent to the task, name it once, clearly, then
carry on with what was asked.

## Machine layout

- `~/dev/` — all code. Subdirs: `saturn/`, `merca/`, `agents/`, `labs/`, `_archive/`.
- `~/Desktop` — kept empty. It is a desk, not a hard drive.
- `~/.claude`, `~/.codex`, `~/.hermes` — agent config only, never project source.

`~/Desktop` and `~/Documents` are iCloud-synced. **Never create a project,
build output, or cache under either.** iCloud will try to upload it forever.

## Conventions already in place

- **Global gitignore** at `~/.gitignore_global` covers build output, caches,
  `.env*`, and agent session logs. Don't re-add those to a per-repo `.gitignore`
  unless the repo needs something extra.
- **Shared Cargo target** via `~/.cargo/config.toml` → `~/.cargo/shared-target`.
  Never set a per-project `target-dir`; worktrees are meant to share one cache.
- **Every repo has a remote before it has a second commit.** If you create a repo,
  create its private GitHub remote in the same breath and push. Repos here have
  been lost to this exact gap.
- Agent session logs (`rollout-*.jsonl`, `.junior/sterile/`, `codex-home/sessions/`)
  carry live auth tokens. They are gitignored globally — keep it that way. This
  applies directly to Codex: your own rollout logs under `~/.codex/sessions/`
  contain auth material and must never be committed.

## Working preferences

- Prefer worktrees over directory copies. A copy has no history and no backup.
- Before any `git push` to a new remote, scan for real secrets — not just `.env`
  files, but session logs and test fixtures.
- Long-running local services (Docker stacks, dev servers, agent daemons) tend to
  accumulate here. If you start one, say so; if you find a stale one, say so.
- Local inference is available (Ollama, MLX, ~163 GB of weights) but not wired
  into a routine workflow. Treat it as an option worth proposing, not a given.

## Stack

Rust (`saturn-office`), TypeScript/Next.js + Supabase (`merca`, `saturnX`),
Python (`hermes`, `eigen`), Swift (`imogen`, `NativeOCR`). Node via Homebrew,
`uv` for Python.

Multiple agent runtimes run side by side — Claude Code, Codex, Hermes, Junior,
Orca. When work touches one, check whether it has a boundary doc
(`~/dev/agents/` has several) before assuming ownership.

## Note on `config.toml`

The `[projects."..."]` entries still point at `~/Desktop/...` paths. Those are
being migrated to `~/dev/`; update an entry when you next work in that project
rather than leaving a stale path behind.
