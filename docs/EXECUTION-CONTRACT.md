# Shared execution contract

Policy version 2026-09-09.2. Applies to new runs only. Routing in the September 8
“Astra authors” contract is superseded; its task and acceptance requirements are
preserved below. Existing sessions, live profiles, historical runs and frozen
Junior remain unchanged. The September 9 implementation is staged in an isolated
infrastructure worktree. **Live provider admission and independent acceptance are
blocked; this is not a completed rollout.**

## Ownership

| Layer | Authority |
| --- | --- |
| Principal | Intent, canonical decisions, consequential escalation |
| Kiki / Hermes | Task intent, dependencies, coordination and acceptance workflow |
| Orca | Worktree placement, native attempts, worker identity and lifecycle |
| Reviewer | Independent acceptance/rejection of exact artifacts |
| Integrator | Serialized canonical-branch mutation |
| ops | Routing policy, validation, evidence and health reconciliation |
| Git / GitHub | Code, revisions, reviewable changes and backup evidence |

No new scheduler or task database is introduced. Hermes tasks are explicitly
unassigned and held (`blocked`/`review`) to prevent its dispatcher from competing
with Orca. Hermes events and attachments retain the workflow record. A blocked
Hermes card can represent an admission hold; it does not imply a live process
failure. Native IDs are always qualified by board, Run and Dispatch.

## Routing and admission

`execution-policy.toml` is the canonical routing policy in this revision. Run
`ops execution resolve <route> --json` for the exact model, effort, role, class,
provider, review, escalation and billing constraints. Principal is Sol high;
Kiki is Sol medium (high for difficult coordination). Consequential decisions use
Astra medium, then high for high risk, xhigh/max only for exceptional difficulty.
Complex implementation uses Sol high; bounded work uses Terra medium/high or
Sonnet; difficult independent review uses Opus or Sol high for Claude-authored
work; mechanical work uses Luna low/medium. Gemini 3.8 Flash is experimental and
blocked pending substantive verified execution. No automatic model substitution,
paid fallback, credential change or credit purchase is permitted.

Zack's September 9 clarification establishes **YOLO permissions for every new
Orca-launched model**, including principals, coordinators, implementers and
reviewers. Policy v2 requires `permissions = "yolo"`, `approval_policy = "never"`
and `sandbox = "none"`. Workers perform authorized work without native approval
prompts. This supersedes the earlier requirement for restrictive worker sandboxes.
Task write sets and prohibited effects are instructions and acceptance conditions;
they are not claimed as operating-system isolation for a YOLO worker. Reviewers
use separate checkouts and an empty write set, with acceptance bound to the exact
unchanged candidate. Runtime/depth bounds and model/billing admission still apply.

Provider help advertises Codex `--dangerously-bypass-approvals-and-sandbox`, Claude
`--dangerously-skip-permissions`, and Gemini `--approval-mode yolo --sandbox=false`.
The adapter records these interfaces separately from observed launch permissions.
In particular, a bypass flag alone does not prove inherited sandbox configuration
is disabled. New runs require the requested YOLO mode to be verified by the launcher.

Catalog and effective config establish eligibility. Native runtime events must
establish observed execution. A model's answer about its identity is never proof.
Account discovery, config hashes and auxiliary-surface inventory are compacted
without credentials. Subscription authentication is a fact; no-extra-charge
execution remains a separate fact. Allowance differences are confounded by other
sessions and never treated as exact task costs.

Current adapters deliberately cannot admit a provider: no verified pre-dispatch
no-rerouting/no-credit guarantee is established. A reviewed custom launcher is
also needed to pin the executable/config, establish YOLO permissions and enforce runtime and
auxiliary-call constraints while using Orca's supported lifecycle. The CLI has no
`--force`, imported admission receipt, boolean override, or paid fallback path.
Explicit model/effort preferences alone cannot enable admission. See
[rollout evidence and remaining gates](execution/ROLLOUT.md).

## Durable packets and evidence

`schemas/task-packet-v2.json` defines identity, outcome, user journey, source
scope/base, write sets, ownership, acceptance commands/timeouts, dependencies,
decisions and source hashes, permission/network/expiry authorization, runtime,
depth, correction and attempt bounds, stop conditions, and integration authority.
Schema validation is separate from admission. Scope paths are explicit relative
paths; review/diagnosis write sets must be empty. Unknown fields fail closed.
Network authorization describes the task's allowed effects; it does not enable a
worker sandbox. Schema v1 and its example remain unchanged for historical
validation and recovery. New dispatch requires v2; old packets are never silently
upgraded to YOLO.

Every attempted dispatch stores a content-addressed immutable packet and exact
policy bytes, including the policy hash. `schemas/run-event-v1.json` defines
append-only, hash-linked events. They carry native task/run/dispatch/session IDs,
requested/observed routes, verification, review, timings, rework and failure
categories as available. Missing observations remain null or explicitly unknown.
Policy snapshots are used during recovery, even if today's defaults change.

Compact evidence lives under `~/.local/state/ops/execution/` (directories 0700,
files 0600). Objects are SHA-256-addressed `.blob` files; most hold JSON and policy
objects hold exact TOML. Hermes attachments/comments reference their hashes.
Common credential shapes are rejected, not silently rewritten. Raw transcripts
stay in provider-owned restricted local stores and are never committed or
attached. Preservation copies are private and distinct from redacted evidence.

## Integration

The project registry records principal-owned canonical-checkout selection,
canonical branch, execution/integration authority references and Hermes queue
board. Git and runtime facts are read live. Projects without explicit authority
remain blocked. Exchange continues to require its principal and designated
integrator; ops does not assign itself that authority.

Verification binds a clean source commit/tree/base and exact checks. Independent
review uses a separate checkout and must prove native reviewer/session identity,
unchanged candidate artifacts and appropriate provider independence. Native
reviewer permissions are YOLO, while its task scope permits inspection only. Current adapters
record rejection but withhold acceptance until that execution proof exists.
An accepted review queues one integration item in Hermes. The integrator takes
a project lock, checks the first item, current expected head, clean canonical
checkout and absence of competing terminal ownership, then uses Git's prepared
ref transaction and a fast-forward tree installation. The expected-head check
is held under Git's ref lock. Changed heads or artifacts invalidate acceptance.
No implicit stash, reset, conflict resolution, branch rewrite, push or deployment.

A crash after tree installation may leave the candidate index with the old ref.
The durable integration intent prevents another mutation. Preserve that state;
reconcile Git/head/index and Hermes before a principal-authorized repair. Never
reset it automatically. Successful integration records source and integrated
commits, worktree, verification/review references and resulting Git health.

## Recovery and bounded work

Every packet names an objective, authority, observable progress, runtime/depth
bounds and stop criteria. The sequence is initial attempt, at most one bounded
correction, independent diagnosis, approved escalation, then reframe or principal
escalation. `recovery.next_stage` validates the order and cannot treat an absent
process observation as exit. Current `recover` reconstructs source and native
state and proposes the next stage; it never launches a retry. Automated bounded
retry/resume remains gated on an admitted launcher and independent diagnosis.

An interrupted dispatch stores intent before native mutation. An existing packet
attempt refuses another dispatch, even when the response or worker observation
is missing. Use native request/Dispatch reconciliation, never resend on silence.
Worktree locks supplement native ownership; they do not authorize takeover.
Retained resources are reported distinctly from live blockage and released
historical failures. Frozen Junior is available only as historical triage data.

## Compatibility and rollback

Existing health verbs retain the `{verb, exit_code, summary, checked, notes,
findings}` envelope and 0/1/2 meanings. `triage --scope execution` reconciles
Hermes and structured Orca projections; `--scope junior` is historical only.
Execution mutations require the named subcommand plus `--apply`; schema validation,
resolution, status and default preparation/recovery are read-only. Route discovery
may initialize its own App Server local runtime, but sends no inference task.

Policy rollback selects the previous Git revision for future runs. Existing
attempts always keep their original packet/policy/evidence. The installed ops
symlink, global provider defaults and profiles are not activated from this staged
worktree. Configuration reconciliation is supplied as reviewable templates and
hash-bound diffs, pending admission and independent review.

## Minimum executable task

Every dispatch names or links one self-contained contract with:

1. **Outcome and user journey:** the actual result someone can open/use, concrete triggers, expected states and visible completion. Not merely a file list.
2. **Starting truth:** exact workspace, code/baseline identity, inherited dirty work, protected sources/checkpoints, preview ownership and relevant authority docs. Current user corrections explicitly supersede stale restrictions.
3. **Writer and change boundary:** exact allowed modules/directories, prohibited effects, dependencies, required components and the chosen model/effort. Capture initial preservation evidence; do not overwrite another writer.
4. **Behavior:** inputs/outputs, permissions, persistence, money/source semantics, interaction details, loading/empty/error/unknown/stale/conflict/retry behavior and recovery.
5. **Design:** named reference patterns, screen hierarchy, interaction contract and actual rendered review widths. A library is a component source, not a substitute for composition. No dummy buttons, fabricated data or success theater.
6. **Verification:** executable checks drawn from the current repo plus end-to-end acceptance cases and exact evidence destinations. Tests must prove boundary behavior rather than mirror implementation. Real restricted evidence stays outside Git.
7. **Decision rights:** reversible implementation choices the worker owns; consequential questions for Zack; precise blockers. Missing convenient tooling is a problem to solve within scope, not a reason to return a plan.
8. **Completion:** working URL/startup/readback, final code identity/change manifest, passed/failed checks, before/after rendered evidence, limitations, preservation receipt and retained-service owner.

## Dispatch and completion gates

- "Brief written", "input sent", "process alive", "task dispatched", "implementation observed", "verification passed", "ready for user review" and "visually accepted" are distinct statuses.
- Do not call a task implementing until a live Dispatch is bound to the intended worker and the correct brief/model. Verify launch results, not just a successful terminal-send receipt.
- The card title/body must reflect the current scope. Link the immutable prior delivery as history rather than leaving a stale task description under a new result.
- Worker success is reported evidence. Kiki checks the artifact, relevant tests/readback and actual browser journey before accepting it. Review findings become bounded repair work for the same owned implementation, not another plan or silent waiver.
- If a launched coordinator has not reached a dispatch or a precise blocker, record its actual phase and investigate the handoff; do not leave "running" as indefinite proof of useful progress. A timeout alone never authorizes killing a live process.
- Completion must survive reopening the management interface and product. Missing evidence stays visibly missing. Publish no timing, quality, efficiency or reliability claim without measurement.
- Release/reuse settled workers through Orca, preserve their reports, and retain only the specifically owned preview needed for review. No extra fallback shells, duplicate coordinators or unmanaged services.

The goal is to make correct execution concrete and checkable. No prompt guarantees correctness; independent evidence and bounded repair close that gap. Astra is not a dependency of the normal execution loop.
