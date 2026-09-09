# Execution substrate rollout evidence

Status: **staged implementation; rollout not complete**. No provider inference was
performed, no independent reviewer was launched, and no canonical branch or live
profile was changed. The implemented admission gate blocks every current provider
lane. The later workflow is exercised with explicitly simulated provider proofs
and real disposable Git worktrees; it must not be described as live acceptance.

## Preservation and native authority

- Base: `83d28e3e8202576e92296f4c876d33e130e52b6d`.
- Isolated branch: `jryear/execution-substrate-20260909`.
- Worktree: `/Users/jryear/orca/workspaces/ops/execution-substrate-20260909`.
- Orca Run: `run_b498f2ac5ed5`; runtime version observed `1.4.199`.
- Run coordinator: `term_733304b8-712c-4f76-a016-80114e956213`, a retained
  infrastructure coordination shell. This existing Codex turn implemented the
  changes; the shell is not claimed as a separately launched Sol principal.
- Hermes board/task: `ops-execution` / `t_46355305`, unassigned and held from the
  Hermes dispatcher. Provider and verification evidence is attached there.
- The original checkout, terminal, frozen Junior, old orchestration repositories,
  profiles, global config and existing sessions remain untouched.
- Preserved 50 inherited dirty files; their content hashes were unchanged on
  readback. Manifest: `~/.local/state/ops/execution/preservation/20260909-substrate/manifest.json`.
  Manifest SHA-256: `139614f27d32b8e85ac7c362577725d34911286b86047d0816f0209fff14855b`.
- Incorporated only preserved process-observation changes (`machine`, `ownership`,
  `reap`, `triage`, their focused tests) and the execution contract. Product
  delivery documents and the unrelated original registry change were excluded.

## Implemented and inspectable

- One `ops execution` namespace: `resolve`, `verify-route`, `validate`, `dispatch`,
  `status`, `verify`, `review`, `queue`, `integrate`, `recover`.
- Sixteen role/class routing entries, versioned TOML and strict task/event schemas.
- Fresh supported Codex account/model/config discovery; Claude auth discovery;
  Gemini executable/settings observation. Main and auxiliary facts remain separate.
- Immutable policy/packet objects; hash-linked attempt events; private storage;
  Hermes attachments/comments; no additional task database or scheduler.
- Fresh native ownership, clean/base/source/dependency/expiry checks; lock-backed
  dispatch deduplication; interrupted mutation preservation and reconstruction.
- Exact candidate commit/tree/base checks, bounded read-only local verification,
  independent-review refusal/recording, Hermes integration queue and atomic Git
  expected-head integration machinery. These later gates have fixture proof only.
- Triage now reads Hermes boards and structured Orca projections, separates
  retained resources, released history, live blockage and unavailable observation,
  and reconciles durable execution links. Junior is explicitly historical only.
- Bounded recovery order validation and source reconstruction. `recover` never
  automatically retries or takes over; it returns the native state and next stage.
- Additive future-session profile templates and a narrow global-instruction diff
  under `config/`, with hashes of their current source files. These are not activated.

## Verification

`python3 -m unittest discover -s tests -v` passed with host observation access:
**41 unittest cases and 70 legacy assertions**. The sandbox denies launchctl access,
so host access is needed for the legacy ownership tests. Verification commands
inside the tests use their own macOS sandbox, denying network and writes outside
their own temporary scratch directory. Candidate source writes were refused.

`git diff --check` passed. Gitleaks scanned the worktree with full redaction and
reported no leaks. The private GitHub repository identity was freshly verified.
See `live-evidence.json` for compact route observations and the earlier 40-case
verification hash, and `final-verification.json` for the final 41-case result;
raw local test output is `~/.local/state/ops/execution/validation/tests-20260909.log`.

| Acceptance case | Evidence boundary |
| --- | --- |
| Simple and parallel workers with separate worktrees | Deterministic Orca fixtures, real Git; no live workers |
| Failed execution / review rejection / failed verification | Deterministic tests; fail closed |
| Bounded correction / diagnosis / escalation / reframe | Recovery decision sequence tested; actual retry/resume not enabled |
| Proactive Astra decisions / cross-family review | Requested routes encoded; live execution blocked |
| Substantive Gemini execution / matched multi-packet comparison | Blocked; no result or default change inferred |
| Unsupported model / effort / auth failure | Adapter failure-injection tests |
| Unexpected downgrade / attempted paid fallback | No live injection; automatic-substitution and paid-fallback policy weakening rejected before admission |
| Interrupted dispatch / duplicate delivery / missing observation | Durable-intent and reconstruction tests |
| Dirty/stale worktrees / competing ownership / integration conflict | Real disposable Git checks, native fixtures, expected-head transaction test |
| Context reduction / source recovery / fresh principal reconstruction | Fresh `Execution` object reconstructs immutable policy/packet; tampering/source loss blocks recovery |
| Independent review of substrate changes | **Outstanding; no accepted reviewer evidence** |

## Exact remaining provider and runtime gates

| Lane | Observed | Missing before admission |
| --- | --- | --- |
| OpenAI | Codex `0.153.4`, ChatGPT Pro auth, requested models/efforts in actual executable catalog | Supported no-server-rerouting and no-credit guarantees for main/auxiliary calls; verified per-attempt executable/config pinning and runtime/permission enforcement |
| Claude | CLI `2.1.266`, first-party `claude.ai` Pro auth | Supported exact model/effort discovery, auxiliary/economic/fallback proof, and verified read-only bounded review launcher |
| Gemini | CLI `0.59.0`, `oauth-personal` setting | Exact Gemini 3.8 Flash identifier/availability/economics; disabling automatic and utility fallback; supported model/effort launch path; substantive independent acceptance |
| Kiki/Hermes | Existing task/events/attachments APIs inspected; routing defaults encoded | New-session profile plus compression/utility/plugin/economic admission remains to be implemented and verified; existing Kiki config unchanged |

There is no hidden bypass flag. `verify-route` and `dispatch --apply` return code 2
when admission is missing. The current adapter also deliberately withholds accepted
review because `worker-show` launch preferences do not prove read-only execution
or provider session identity. It does not invent a native attestation field.

Remaining implementation: a supported custom per-attempt launcher that proves
executable/config/environment identity, enforces permissions/runtime/depth and
observes provider events; real bounded retry/resume after native reconciliation;
full auxiliary admission including Kiki/Hermes. These are not represented as
completed by the fixtures. Provider economic/no-substitution guarantees must be
established before that launcher performs any inference.

## Activation, backup and rollback

This branch can be reviewed privately without changing canonical `main` or the
installed `~/.local/bin/ops` symlink. Complete independent review and the first
admitted OpenAI end-to-end acceptance before activation. Then enable review and
integration, admit other providers only on evidence, and run recovery drills.
Existing sessions retain their current configuration. Any new infrastructure
principal must launch explicitly on `gpt-5.6-sol` with `high` effort.

The generated config changes must be checked against `config/reconciliation-base.json`
before application. Do not overwrite concurrent edits or copy credential stores.
Rollback selects the previous policy/config revision for future runs, preserving
all execution history. Retain private source snapshots and the Git branch/bundle;
only compact redacted evidence and policy/code artifacts belong in GitHub.

## Interface sources

- [Codex App Server](https://learn.chatgpt.com/docs/app-server): account/model/config discovery and distinct runtime notifications.
- [Gemini model routing](https://geminicli.com/docs/cli/model-routing/): automatic and utility-call fallback considerations.
- [Git update-ref](https://git-scm.com/docs/git-update-ref): expected-old-value and prepared ref transactions.
- [Git read-tree](https://git-scm.com/docs/git-read-tree): fast-forward index/worktree installation.
- Installed `orca skills get orchestration`, coordinator-loop/placement/low-level
  references and command help: native lifecycle, launch preferences and ownership.
