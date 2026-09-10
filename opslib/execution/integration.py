"""Exact-artifact verification/review and serialized, expected-head integration."""
from __future__ import annotations

from pathlib import Path
import json
import os
import selectors
import subprocess
import tempfile
import time

from .common import Blocked, command, digest, encoded, git
from .workflow import project_contract, references


def candidate(packet, revision):
    source = packet["scope"]["worktree"]
    if len(revision) != 40 or git(source, "rev-parse", revision + "^{commit}") != revision:
        raise Blocked("candidate must be an exact commit")
    if git(source, "status", "--porcelain=v1", "--untracked-files=all"):
        raise Blocked("candidate worktree is dirty")
    if git(source, "rev-parse", "HEAD") != revision:
        raise Blocked("candidate is not the worktree HEAD")
    base = packet["integration"]["expected_head"]
    git(source, "merge-base", "--is-ancestor", base, revision)
    files = git(source, "diff", "--name-only", "--no-renames", base, revision).splitlines()
    allowed = packet["scope"]["write_paths"]
    if any(not any(name == a or name.startswith(a.rstrip("/") + "/") for a in allowed) for name in files):
        raise Blocked("candidate changes paths outside the authorized write set")
    return {"commit": revision, "tree": git(source, "rev-parse", revision + "^{tree}"),
            "base": base, "files": files, "source_worktree": source}


def verify_candidate(execution, attempt, revision, apply=False):
    packet, events = execution.reconstruct(attempt)
    manifest = candidate(packet, revision)
    if not apply:
        return {"manifest": manifest, "checks": packet["acceptance"], "executed": False}
    references(packet)
    if time.time() >= packet["authorization"]["expires_at"]:
        raise Blocked("verification authorization expired")
    # The packet is itself the authorization to run these specific commands.
    results = []
    start = time.monotonic()
    for check in packet["acceptance"]:
        remaining = packet["limits"]["runtime_seconds"] - (time.monotonic() - start)
        if remaining <= 0:
            raise Blocked("verification runtime bound reached")
        try:
            output = bounded_check(check["argv"], manifest["source_worktree"],
                                   min(check["timeout_seconds"], remaining))
            results.append({"description": check["description"], "argv": check["argv"],
                            "passed": True, "stdout_sha256": digest(output.encode())})
        except Blocked:
            results.append({"description": check["description"], "argv": check["argv"], "passed": False})
            break
    # Tests that mutate source or Git invalidate the proof they were meant to provide.
    if candidate(packet, revision) != manifest:
        raise Blocked("verification changed the candidate")
    data = {"manifest": manifest, "checks": results, "passed": len(results) == len(packet["acceptance"]) and all(r["passed"] for r in results),
            "elapsed_seconds": round(time.monotonic() - start, 3)}
    ref = execution.record(packet, attempt, "verification", data)
    return {**data, "evidence": ref}


def bounded_check(argv, cwd, timeout):
    """Read-only candidate checks, no network, writes only to their own scratch.

    The machine's supported macOS sandbox is required. Unsupported platforms
    withhold verification rather than execute packet commands without limits.
    """
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file():
        raise Blocked("read-only verification sandbox unavailable")
    with tempfile.TemporaryDirectory(prefix="ops-check-") as scratch:
        scratch = str(Path(scratch).resolve())
        profile = ('(version 1)(deny default)(allow process-exec process-fork)'
                   '(allow file-read* sysctl-read mach-lookup)'
                   '(allow file-write* (subpath ' + json.dumps(scratch) + '))'
                   '(allow file-write-data (literal "/dev/null"))')
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "HOME", "USER", "SHELL", "SYSTEMROOT")}
        env.update(TMPDIR=scratch, TMP=scratch, TEMP=scratch, PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0")
        return command([str(sandbox), "-p", profile, *argv], cwd=cwd, timeout=timeout, env=env)


def review(execution, attempt, receipt, apply=False):
    packet, events = execution.reconstruct(attempt)
    required = {"schema_version", "verdict", "reviewer_dispatch_id", "reviewer_session_id", "reviewer_provider",
                "reviewer_worktree", "candidate_commit", "candidate_tree", "base_revision", "verification_sha256", "report"}
    if set(receipt) != required or receipt["schema_version"] != 1 or receipt["verdict"] not in ("accepted", "rejected"):
        raise Blocked("invalid independent review receipt")
    manifest = candidate(packet, receipt["candidate_commit"])
    if manifest["tree"] != receipt["candidate_tree"] or manifest["base"] != receipt["base_revision"]:
        raise Blocked("review does not bind the exact candidate/base")
    report = Path(receipt["report"]["path"])
    if report.is_symlink() or digest(report.read_bytes()) != receipt["report"]["sha256"]:
        raise Blocked("review report missing or changed")
    verification = execution.store.read({"sha256": receipt["verification_sha256"]})
    if verification.get("attempt_id") != attempt or verification.get("kind") != "verification" or verification["data"]["manifest"] != manifest or not verification["data"]["passed"]:
        raise Blocked("review verification evidence is missing, failed, or stale")
    reviewer_path = Path(receipt["reviewer_worktree"]).resolve()
    if reviewer_path in (Path(manifest["source_worktree"]).resolve(), Path(packet["integration"]["canonical_checkout"]).resolve()):
        raise Blocked("review must use a separate checkout")
    if git(reviewer_path, "rev-parse", "HEAD") != manifest["commit"] or git(reviewer_path, "status", "--porcelain=v1"):
        raise Blocked("review checkout differs from exact candidate")
    native = execution.orca.call("orchestration", "worker-show", "--dispatch", receipt["reviewer_dispatch_id"])
    if native.get("dispatch", {}).get("id") != receipt["reviewer_dispatch_id"]:
        raise Blocked("native reviewer dispatch identity mismatch")
    workspace = native.get("worker", {}).get("worktreeId", "")
    if not workspace.endswith("::" + str(reviewer_path)):
        raise Blocked("native reviewer checkout mismatch")
    if receipt["verdict"] == "accepted":
        require_review_execution_proof(native, receipt)
    author = events[0]["data"].get("route", {}).get("provider")
    requested_review = events[0]["data"].get("route", {}).get("review")
    if requested_review == "cross-family" and receipt["reviewer_provider"] == author:
        raise Blocked("independent cross-family review required")
    own_dispatches = {e["data"].get("dispatchId") for e in events if e["kind"] == "dispatch_receipt"}
    if receipt["reviewer_dispatch_id"] in own_dispatches:
        raise Blocked("author cannot accept its own work")
    if apply:
        execution.record(packet, attempt, "review", receipt)
    return {"review": receipt, "recorded": apply}


def require_review_execution_proof(native, receipt):
    """Current supported worker-show proves lifecycle and launch preferences only.

    Do not invent an attestation field or equate effective launch preferences with
    observed provider execution. A reviewed adapter must implement this proof
    before accepting a review in the live lane. Tests isolate this explicit gate.
    """
    if native.get("dispatch", {}).get("status") != "completed" or native.get("worker", {}).get("state") != "succeeded":
        raise Blocked("independent reviewer has not completed successfully")
    raise Blocked("reviewer provider-session execution evidence and unchanged-artifact proof are not established by the installed adapter")


def queue(execution, attempt, apply=False):
    packet, events = execution.reconstruct(attempt)
    reviews = [e["data"] for e in events if e["kind"] == "review"]
    if not reviews or reviews[-1]["verdict"] != "accepted":
        raise Blocked("candidate lacks independent acceptance")
    receipt = reviews[-1]
    # Re-check exact source, evidence and native reviewer identity before queue admission.
    review(execution, attempt, receipt)
    result = {"board": packet["integration"]["queue_board"], "candidate": receipt["candidate_commit"], "attempt_id": attempt}
    if apply:
        task = execution.hermes.create_integration(result["board"], packet["task_id"], result["candidate"],
            "Ops integration candidate. " + json.dumps(result, sort_keys=True) + " Authority: " + packet["integration"]["authority_ref"])
        result["hermes_task_id"] = task["id"]
    return result


def integrate(execution, attempt, queue_task, expected_head, apply=False):
    packet, events = execution.reconstruct(attempt)
    entry = project_contract(packet)
    if any(e["kind"] == "integration_intent" for e in events):
        raise Blocked("prior integration intent exists; reconcile expected/resulting heads before any repeat mutation")
    canonical = Path(entry.canonical_checkout)
    with execution.store.lock("project:" + git(canonical, "rev-parse", "--path-format=absolute", "--git-common-dir")):
        references(packet)
        if time.time() >= packet["authorization"]["expires_at"]:
            raise Blocked("integration authorization expired")
        item = queue(execution, attempt)
        tasks = execution.hermes.tasks(item["board"])
        relevant = [t for t in tasks if t.get("created_by") == "ops-execution" and t.get("title", "").startswith("Integrate ") and t.get("status") not in ("done", "archived")]
        relevant.sort(key=lambda t: (t.get("created_at", 0), t["id"]))
        if not relevant or relevant[0]["id"] != queue_task:
            raise Blocked("integration task is not first in the project's Hermes queue")
        native_task = relevant[0]
        if native_task.get("assignee") or native_task.get("status") != "blocked":
            raise Blocked("integration task is owned by a competing dispatcher")
        if attempt not in native_task.get("body", "") or item["candidate"] not in native_task.get("body", ""):
            raise Blocked("Hermes queue item does not identify this candidate and attempt")
        if expected_head != packet["integration"]["expected_head"] or git(canonical, "rev-parse", "HEAD") != expected_head:
            raise Blocked("canonical head changed: verification and independent review against latest base required")
        if git(canonical, "symbolic-ref", "--short", "HEAD") != entry.canonical_branch or git(canonical, "status", "--porcelain=v1", "--untracked-files=all"):
            raise Blocked("canonical checkout is dirty or on another branch")
        terminals = execution.orca.rows("terminal", "list", "--worktree", "path:" + str(canonical), key="terminals")
        if terminals:
            raise Blocked("canonical checkout has retained/live terminal ownership; integration withheld")
        git(canonical, "merge-base", "--is-ancestor", expected_head, item["candidate"])
        result = {"source_worktree": packet["scope"]["worktree"], "source_commit": item["candidate"],
                  "expected_head": expected_head, "canonical_checkout": str(canonical), "queue_task": queue_task}
        if not apply:
            return {**result, "mutated": False}
        execution.record(packet, attempt, "integration_intent", result)
        # Git's own ref locks supplement the project lock. No merge conflict resolution,
        # stash, reset, rebase, force update, push, or deployment is implicit here.
        if git(canonical, "rev-parse", "HEAD") != expected_head:
            raise Blocked("canonical head changed at integration boundary")
        advance_canonical(canonical, entry.canonical_branch, expected_head, item["candidate"])
        integrated = git(canonical, "rev-parse", "HEAD")
        if integrated != item["candidate"]:
            raise Blocked("integrated head mismatch; preserve and reconcile")
        result.update(integrated_commit=integrated, health={"git_clean": not bool(git(canonical, "status", "--porcelain=v1")),
                                                         "provider_runtime": "unknown"})
        ref = execution.record(packet, attempt, "integrated", result)
        execution.hermes.attach(item["board"], queue_task, ref)
        return result


def advance_canonical(canonical, branch, expected, revision):
    """Hold Git's ref transaction lock while installing a fast-forward tree.

    Expected-head checking is atomic with ref preparation, including against
    writers that do not use ops' project lock. A crash after read-tree leaves an
    explicit integration intent and an inspectable index; it is never retried or
    reset automatically. This function is reached only after all admission gates.
    """
    git(canonical, "check-ref-format", "refs/heads/" + branch)
    if git(canonical, "symbolic-ref", "HEAD") != "refs/heads/" + branch:
        raise Blocked("canonical branch changed before ref transaction")
    process = subprocess.Popen(["git", "-C", str(canonical), "update-ref", "--stdin", "-m", "ops verified integration"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    def step(payload, reply):
        process.stdin.write(payload.encode())
        line = b""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(1):
                    continue
            byte = process.stdout.read(1)
            if not byte:
                raise Blocked("Git ref transaction rejected expected head or competing lock; reconcile integration intent")
            line += byte
            if byte == b"\n":
                if line.decode().strip() != reply:
                    raise Blocked("Git ref transaction response unverified")
                return
        raise Blocked("Git ref transaction timed out; preserve and reconcile")

    try:
        step("start\n", "start: ok")
        step(f"update refs/heads/{branch} {revision} {expected}\nprepare\n", "prepare: ok")
        if git(canonical, "status", "--porcelain=v1", "--untracked-files=all"):
            raise Blocked("canonical contents changed before tree installation")
        git(canonical, "read-tree", "-m", "-u", expected, revision)
        if git(canonical, "write-tree") != git(canonical, "rev-parse", revision + "^{tree}"):
            raise Blocked("installed tree differs from accepted candidate; preserve index and reconcile")
        step("commit\n", "commit: ok")
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        process.stdout.close()
