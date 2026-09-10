"""Deterministic boundary tests using real disposable Git worktrees.

Provider admission and independent execution proof are replaced ONLY in tests
that exercise later boundaries. These fixtures never claim live provider proof.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opslib import registry
from opslib.execution import integration, observation, policy, providers, workflow
from opslib.execution.recovery import next_stage
from opslib.execution.common import Blocked, Store, digest, encoded, git
from opslib.model import Report


class FakeHermes:
    def __init__(self):
        self.cards = {"t_test": {"id": "t_test", "status": "blocked", "assignee": None}}
        self.attachments = []

    def task(self, board, task):
        return {"task": self.cards[task]}

    def attach(self, board, task, reference):
        self.attachments.append((board, task, reference))

    def tasks(self, board):
        return list(self.cards.values())

    def boards(self):
        return ["fixture"]

    def create_integration(self, board, task, commit, body):
        self.cards["t_queue"] = {"id": "t_queue", "status": "blocked", "assignee": None,
            "created_by": "ops-execution", "title": "Integrate " + task, "body": body, "created_at": 1}
        return self.cards["t_queue"]


class FakeOrca:
    def __init__(self, worktree):
        self.path = str(worktree)
        self.fleet = []
        self.terminals = []
        self.calls = []
        self.fail_start = False
        self.reviewer = None

    def workers(self, run=None):
        return self.fleet

    def rows(self, *args, key):
        if key == "worktrees":
            return [{"worktreeId": "fixture::" + self.path, "path": self.path}]
        if key == "terminals":
            return self.terminals
        if key == "repos":
            return [{"id": "fixture", "hookSettings": {"scripts": {"setup": ""}}}]
        raise AssertionError(args)

    def call(self, *args, **kwargs):
        self.calls.append(args)
        if args[1] == "run-show":
            return {"run": {"id": "run_test", "coordinator_handle": "term_coordinator", "legacy": 0}}
        if args[1] == "task-create":
            return {"task": {"id": "task_native"}}
        if args[1] == "worker-start":
            if self.fail_start:
                raise Blocked("interrupted native response")
            self.fleet = [{"dispatchId": "ctx_test", "taskId": "task_native", "runId": "run_test",
                "terminalState": "owned", "projection": {"workspace": {"id": "fixture::" + self.path},
                "liveness": {"verdict": "live"}, "attention": {"requiresAction": False}}}]
            return {"taskId": "task_native", "dispatchId": "ctx_test", "stage": "ready"}
        if args[1] == "worker-show":
            return {"dispatch": {"id": "ctx_review", "status": "completed"},
                    "worker": {"state": "succeeded", "worktreeId": "fixture::" + str(self.reviewer)}}
        raise AssertionError(args)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ops-execution-test-")
        self.root = Path(self.temp.name).resolve()
        self.canonical = self.root / "canonical"
        self.canonical.mkdir()
        git(self.canonical, "init", "-b", "main")
        git(self.canonical, "config", "user.email", "fixture@invalid.local")
        git(self.canonical, "config", "user.name", "Fixture")
        (self.canonical / "source.txt").write_text("before\n")
        git(self.canonical, "add", "source.txt")
        git(self.canonical, "commit", "-m", "fixture base")
        self.base = git(self.canonical, "rev-parse", "HEAD")
        self.worktree = self.root / "worker"
        git(self.canonical, "worktree", "add", "-b", "worker", str(self.worktree))
        self.decision = self.root / "decision.txt"
        self.decision.write_text("Fixture authorization for bounded local tests only.\n")
        reference = {"path": str(self.decision), "sha256": digest(self.decision.read_bytes())}
        self.packet = {"schema_version": 2, "task_id": "t_test", "board": "fixture", "project": "fixture",
            "objective": "Change the fixture source", "user_journey": "Read the verified source",
            "role": "implementer", "task_class": "bounded-implementation", "route": "bounded",
            "scope": {"worktree": str(self.worktree), "base_revision": self.base, "read_paths": ["source.txt"],
                      "write_paths": ["source.txt"], "prohibited_effects": ["network", "canonical writes"]},
            "ownership": {"orca_run_id": "run_test", "coordinator_handle": "term_coordinator",
                          "execution_authority_ref": "fixture-authority", "allowed_terminal_handles": []},
            "acceptance": [{"description": "source is changed", "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('source.txt').read_text() == 'after\\n'"], "timeout_seconds": 10}],
            "dependencies": [], "source_evidence": [reference], "decisions": [reference],
            "authorization": {"references": [reference], "permissions": "yolo", "network": False, "expires_at": int(time.time()) + 3600},
            "limits": {"runtime_seconds": 60, "depth": 0, "corrections": 1, "max_attempts": 5, "progress_seconds": 20},
            "stop_conditions": ["source evidence unavailable", "bounded checks fail"],
            "integration": {"canonical_checkout": str(self.canonical), "canonical_branch": "main", "expected_head": self.base,
                            "authority_ref": "fixture-integrator", "queue_board": "fixture"}}
        self.entry = registry.Entry(id="fixture", path=str(self.canonical), canonical_checkout=str(self.canonical),
            canonical_branch="main", execution_authority_ref="fixture-authority", integration_authority_ref="fixture-integrator",
            integration_queue_board="fixture")
        self.registry_patch = patch.object(registry, "load", return_value={"fixture": self.entry})
        self.registry_patch.start()
        self.hermes, self.orca = FakeHermes(), FakeOrca(self.worktree)
        self.store = Store(self.root / "evidence")
        self.execution = workflow.Execution(self.store, self.orca, self.hermes)

    def tearDown(self):
        self.registry_patch.stop()
        self.temp.cleanup()

    def admitted(self, *args):
        return {"admission": "admitted", "blockers": [], "recorded_at": time.time(), "proof": "fixture-only",
                "requested_launch": dict(policy.YOLO_LAUNCH), "observed_launch": dict(policy.YOLO_LAUNCH)}

    def dispatch(self):
        with patch.object(providers, "verify", side_effect=self.admitted):
            return self.execution.dispatch(self.packet, apply=True)["attempt_id"]

    def changed(self):
        (self.worktree / "source.txt").write_text("after\n")
        git(self.worktree, "add", "source.txt")
        git(self.worktree, "commit", "-m", "fixture candidate")
        return git(self.worktree, "rev-parse", "HEAD")

    def review_receipt(self, attempt, revision):
        proof = integration.verify_candidate(self.execution, attempt, revision, True)
        self.reviewer = self.root / "review"
        git(self.canonical, "worktree", "add", "--detach", str(self.reviewer), revision)
        self.orca.reviewer = self.reviewer
        report = self.root / "review.txt"
        report.write_text("Independent review fixture; no live provider execution.\n")
        return {"schema_version": 1, "verdict": "accepted", "reviewer_dispatch_id": "ctx_review", "reviewer_session_id": "fixture-review",
            "reviewer_provider": "claude", "reviewer_worktree": str(self.reviewer), "candidate_commit": revision,
            "candidate_tree": git(self.worktree, "rev-parse", revision + "^{tree}"), "base_revision": self.base,
            "verification_sha256": proof["evidence"]["sha256"], "report": {"path": str(report), "sha256": digest(report.read_bytes())}}

    def test_policy_defaults_and_unknown_route(self):
        self.assertEqual((policy.resolve("principal")["model"], policy.resolve("principal")["effort"]), ("gpt-5.6-sol", "high"))
        self.assertEqual(policy.resolve("decision")["effort"], "medium")
        with self.assertRaises(Blocked): policy.resolve("silent-fallback")

    def test_schema_missing_unknown_unsafe_and_unbounded_fields(self):
        for field in list(self.packet):
            packet = copy.deepcopy(self.packet); del packet[field]
            with self.subTest(field=field), self.assertRaises(Blocked): policy.validate_packet(packet)
        cases = [("schema_version", True), ("scope", {**self.packet["scope"], "write_paths": ["../outside"]}),
                 ("limits", {**self.packet["limits"], "corrections": 2}), ("role", "reviewer")]
        for field, value in cases:
            packet = copy.deepcopy(self.packet); packet[field] = value
            with self.subTest(field=field), self.assertRaises(Blocked): policy.validate_packet(packet)
        packet = copy.deepcopy(self.packet); packet["unbounded"] = True
        with self.assertRaises(Blocked): policy.validate_packet(packet)

    def test_yolo_review_keeps_empty_write_scope_without_native_permission_downgrade(self):
        self.packet.update(role="reviewer", route="review", task_class="difficult-review")
        self.packet["scope"]["write_paths"] = []
        with patch.object(providers, "verify", side_effect=self.admitted):
            prepared = self.execution.prepare(self.packet)
        self.assertEqual(prepared["route"]["launch"], policy.YOLO_LAUNCH)
        self.packet["scope"]["write_paths"] = ["source.txt"]
        with self.assertRaisesRegex(Blocked, "read-only scope"):
            self.execution.prepare(self.packet)

    def test_historical_v1_packet_reconstructs_unchanged_but_cannot_start_new_run(self):
        old_packet = copy.deepcopy(self.packet)
        old_packet["schema_version"] = 1
        old_packet["authorization"]["permissions"] = "workspace-write"
        policy.validate_packet(old_packet)
        old_policy = (policy.POLICY.read_text()
                      .replace("schema_version = 2", "schema_version = 1")
                      .replace('policy_version = "2026-09-09.2"', 'policy_version = "2026-09-09.1"')
                      .replace('[launch]\npermissions = "yolo"\napproval_policy = "never"\nsandbox = "none"\n\n', ''))
        old_policy_ref = self.store.put_bytes(old_policy.encode())
        self.store.event("legacy", "prepared", {"packet": self.store.put(old_packet), "policy": old_policy_ref})
        recovered, events = self.execution.reconstruct("legacy")
        self.assertEqual(recovered, old_packet)
        self.assertIsNone(policy.resolve("principal", old_policy_ref["path"])["launch"])
        with self.assertRaisesRegex(Blocked, "v2 YOLO packet"):
            self.execution.prepare(old_packet)
        self.assertFalse(self.orca.calls)

    def test_blocked_route_never_reaches_native_mutation(self):
        with patch.object(providers, "verify", return_value={"admission": "blocked", "blockers": ["billing unknown"]}):
            with self.assertRaisesRegex(Blocked, "billing unknown"): self.execution.dispatch(self.packet, True)
        self.assertFalse(any(c[1] in ("task-create", "worker-start") for c in self.orca.calls))
        self.assertFalse(self.hermes.attachments)

    def test_dirty_stale_symlink_and_unregistered_checkout(self):
        (self.worktree / "untracked").write_text("preserve me")
        with self.assertRaisesRegex(Blocked, "dirty"): self.execution.prepare(self.packet)
        (self.worktree / "untracked").unlink()
        self.packet["scope"]["base_revision"] = "0" * 40
        with self.assertRaisesRegex(Blocked, "base revision"): self.execution.prepare(self.packet)
        self.packet["scope"]["base_revision"] = self.base
        self.entry.canonical_checkout = ""
        with self.assertRaisesRegex(Blocked, "canonical selection"): self.execution.prepare(self.packet)

    def test_competing_native_worker_and_unavailable_observation(self):
        self.orca.terminals = [{"handle": "term_someone_else"}]
        with self.assertRaisesRegex(Blocked, "competing"): self.execution.prepare(self.packet)
        self.orca.terminals = []
        self.orca.fleet = [{"terminalState": "retained", "projection": {"workspace": {"id": "fixture::" + str(self.worktree)}}}]
        with self.assertRaisesRegex(Blocked, "retains"): self.execution.prepare(self.packet)
        with patch.object(self.orca, "workers", side_effect=Blocked("unavailable")):
            with self.assertRaises(Blocked): self.execution.prepare(self.packet)

    def test_dependencies_auth_expiry_and_hermes_owner(self):
        self.packet["dependencies"] = ["t_dependency"]
        self.hermes.cards["t_dependency"] = {"id": "t_dependency", "status": "running"}
        with self.assertRaisesRegex(Blocked, "dependency"): self.execution.prepare(self.packet)
        self.packet["dependencies"] = []
        self.packet["authorization"]["expires_at"] = 1
        with self.assertRaisesRegex(Blocked, "expired"): self.execution.prepare(self.packet)
        self.packet["authorization"]["expires_at"] = int(time.time()) + 3600
        self.hermes.cards["t_test"]["assignee"] = "competing-runtime"
        with self.assertRaisesRegex(Blocked, "unassigned"): self.execution.prepare(self.packet)

    def test_dispatch_reconstruction_policy_snapshot_and_duplicate_delivery(self):
        attempt = self.dispatch()
        self.assertTrue(self.hermes.attachments)
        reconstructed, events = workflow.Execution(self.store, self.orca, self.hermes).reconstruct(attempt)
        self.assertEqual(reconstructed, self.packet)
        self.assertEqual(events[0]["data"]["observed_route"], None)
        with patch.object(policy, "load", side_effect=AssertionError("historical run must not use current policy")):
            self.execution.reconstruct(attempt)
        self.orca.fleet = []
        with patch.object(providers, "verify", side_effect=self.admitted), self.assertRaisesRegex(Blocked, "already has an attempt"):
            self.execution.dispatch(self.packet, True)
        self.assertEqual(sum(c[1] == "worker-start" for c in self.orca.calls), 1)

    def test_interrupted_dispatch_cannot_be_replayed_or_taken_over(self):
        self.orca.fail_start = True
        with patch.object(providers, "verify", side_effect=self.admitted), self.assertRaisesRegex(Blocked, "outcome unknown"):
            self.execution.dispatch(self.packet, True)
        attempt = self.execution.find_attempts(self.packet)[0]
        recovered = self.execution.recover(attempt)
        self.assertFalse(recovered["takeover_authorized"])
        self.assertIn("no takeover", recovered["next_action"])
        with patch.object(providers, "verify", side_effect=self.admitted), self.assertRaises(Blocked):
            self.execution.dispatch(self.packet, True)
        self.assertEqual(sum(c[1] == "worker-start" for c in self.orca.calls), 1)

    def test_evidence_tamper_and_source_loss_block_recovery(self):
        attempt = self.dispatch()
        self.decision.write_text("changed authority")
        with self.assertRaisesRegex(Blocked, "hash changed"): self.execution.recover(attempt)
        event_path = self.store.root / "attempts" / attempt / "000000.json"
        data = json.loads(event_path.read_text()); data["sequence"] = 9
        event_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(Blocked, "chain is corrupt"): self.store.events(attempt)

    def test_private_immutable_objects_and_project_locks(self):
        ref = self.store.put({"a": 1})
        self.assertEqual(Path(ref["path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.put({"a": 1}), ref)
        with self.store.lock("project:a"):
            with self.assertRaisesRegex(Blocked, "competing"):
                with self.store.lock("project:a"): pass
            with self.store.lock("project:b"): pass
        with self.assertRaisesRegex(Blocked, "credential-bearing"):
            self.store.put({"access_token": "fixture-not-a-real-token"})
        for value in (".", "..", "../escape", "/tmp/escape"):
            with self.assertRaises(Blocked): self.store.events(value)

    def test_verification_sandbox_denies_source_writes_and_network(self):
        with self.assertRaises(Blocked):
            integration.bounded_check([sys.executable, "-c", "from pathlib import Path; Path('source.txt').write_text('overwritten')"], self.worktree, 5)
        self.assertEqual((self.worktree / "source.txt").read_text(), "before\n")
        with self.assertRaises(Blocked):
            integration.bounded_check([sys.executable, "-c", "import socket; s=socket.socket(); s.bind(('127.0.0.1',0))"], self.worktree, 5)
        result = integration.bounded_check([sys.executable, "-c", "import os; from pathlib import Path; p=Path(os.environ['TMPDIR'])/'ok'; p.write_text('allowed'); print(p.read_text())"], self.worktree, 5)
        self.assertEqual(result.strip(), "allowed")

    def test_parallel_workers_have_disjoint_native_worktrees(self):
        from concurrent.futures import ThreadPoolExecutor
        second = self.root / "worker-two"
        git(self.canonical, "worktree", "add", "-b", "worker-two", str(second))
        packet = copy.deepcopy(self.packet)
        packet["task_id"] = "t_second"; packet["scope"]["worktree"] = str(second)
        self.hermes.cards["t_second"] = {"id": "t_second", "status": "blocked", "assignee": None}
        second_execution = workflow.Execution(self.store, FakeOrca(second), self.hermes)
        with patch.object(providers, "verify", side_effect=self.admitted), ThreadPoolExecutor(2) as pool:
            a = pool.submit(self.execution.dispatch, self.packet, True)
            b = pool.submit(second_execution.dispatch, packet, True)
            self.assertNotEqual(a.result()["attempt_id"], b.result()["attempt_id"])

    def test_expected_head_is_checked_under_git_ref_lock(self):
        revision = self.changed()
        (self.canonical / "other.txt").write_text("other writer\n")
        git(self.canonical, "add", "other.txt"); git(self.canonical, "commit", "-m", "race winner")
        current = git(self.canonical, "rev-parse", "HEAD")
        with self.assertRaisesRegex(Blocked, "ref transaction rejected"):
            integration.advance_canonical(self.canonical, "main", self.base, revision)
        self.assertEqual(git(self.canonical, "rev-parse", "HEAD"), current)
        self.assertEqual((self.canonical / "source.txt").read_text(), "before\n")

    def test_recovery_order_bounds_and_escalation_authority(self):
        workers = [{"terminalState": "released", "projection": {"liveness": {"verdict": "exited"}}}]
        limits = self.packet["limits"]
        self.assertEqual(next_stage(["initial"], limits, workers), "correction")
        self.assertEqual(next_stage(["initial", "correction"], limits, workers), "diagnosis")
        with self.assertRaises(Blocked): next_stage(["initial", "diagnosis"], limits, workers)
        with self.assertRaisesRegex(Blocked, "explicit escalation"):
            next_stage(["initial", "correction", "diagnosis"], limits, workers)
        self.assertEqual(next_stage(["initial", "correction", "diagnosis"], limits, workers,
                                   diagnosis_accepted=True, escalation_authorized=True), "escalation")
        self.assertEqual(next_stage(["initial", "correction", "diagnosis", "escalation"], limits, workers), "reframe")
        self.assertEqual(next_stage(["initial", "correction", "diagnosis", "escalation", "reframe"], limits, workers), "principal")
        with self.assertRaisesRegex(Blocked, "no takeover"): next_stage(["initial"], limits, [])

    def test_verification_then_stale_review_and_missing_provider_proof(self):
        attempt = self.dispatch(); revision = self.changed(); receipt = self.review_receipt(attempt, revision)
        with self.assertRaisesRegex(Blocked, "execution evidence"): integration.review(self.execution, attempt, receipt, True)
        stale = {**receipt, "candidate_tree": "0" * 40}
        with self.assertRaisesRegex(Blocked, "exact candidate"): integration.review(self.execution, attempt, stale)
        receipt["verdict"] = "rejected"
        integration.review(self.execution, attempt, receipt, True)
        with self.assertRaisesRegex(Blocked, "independent acceptance"): integration.queue(self.execution, attempt)

    def test_fixture_end_to_end_review_queue_and_integration(self):
        attempt = self.dispatch(); revision = self.changed(); receipt = self.review_receipt(attempt, revision)
        with patch.object(integration, "require_review_execution_proof"):
            integration.review(self.execution, attempt, receipt, True)
            queue = integration.queue(self.execution, attempt, True)
            result = integration.integrate(self.execution, attempt, queue["hermes_task_id"], self.base, True)
        self.assertEqual(result["integrated_commit"], revision)
        self.assertEqual((self.canonical / "source.txt").read_text(), "after\n")
        self.assertTrue(result["health"]["git_clean"])
        with self.assertRaisesRegex(Blocked, "prior integration intent"):
            integration.integrate(self.execution, attempt, queue["hermes_task_id"], self.base, True)

    def test_changed_canonical_head_invalidates_acceptance(self):
        attempt = self.dispatch(); revision = self.changed(); receipt = self.review_receipt(attempt, revision)
        with patch.object(integration, "require_review_execution_proof"):
            integration.review(self.execution, attempt, receipt, True)
            item = integration.queue(self.execution, attempt, True)
            (self.canonical / "other.txt").write_text("other writer\n")
            git(self.canonical, "add", "other.txt"); git(self.canonical, "commit", "-m", "new canonical base")
            with self.assertRaisesRegex(Blocked, "canonical head changed"):
                integration.integrate(self.execution, attempt, item["hermes_task_id"], self.base, True)
        self.assertEqual((self.canonical / "source.txt").read_text(), "before\n")

    def test_failure_in_verification_cannot_enter_queue(self):
        attempt = self.dispatch()
        result = integration.verify_candidate(self.execution, attempt, self.base, True)
        self.assertFalse(result["passed"])
        with self.assertRaises(Blocked): integration.queue(self.execution, attempt)

    def test_unavailable_observation_history_retention_and_live_blockage(self):
        for state, verdict, outcome, expected in [("retained", "unverifiable", "failed", "retained-resource"),
                ("released", "exited", "failed", "historical-failure"), ("owned", "unverifiable", None, "observation-unavailable"),
                ("owned", "live", None, "live-blockage")]:
            self.assertEqual(observation.classify({"terminalState": state, "projection": {"liveness": {"verdict": verdict},
                "outcome": outcome, "attention": {"requiresAction": True}}}), expected)
        rep = Report("triage")
        with patch.object(self.orca, "workers", side_effect=Blocked("lost observation")):
            observation.reconcile(rep, self.hermes, self.orca)
        self.assertEqual(rep.exit_code, 1)
        self.assertEqual(rep.checked["orca_observation"], "unavailable")
        self.assertEqual(rep.checked["hermes_observation"], "available")


if __name__ == "__main__":
    unittest.main()
