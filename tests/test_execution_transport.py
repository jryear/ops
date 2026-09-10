"""Real subprocess protocol failures; no provider requests or copied credentials."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opslib.execution.codex_rpc import CodexRPC, compact_notifications
from opslib.execution import codex_launch, policy, providers
from opslib.execution.common import Blocked
from opslib.execution.native import Hermes, require_hermes_hold


class TransportTests(unittest.TestCase):
    def probe(self, body, check):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fake-codex"
            path.write_text("#!" + sys.executable + "\nimport json,sys,time\n" + body)
            path.chmod(0o700)
            rpc = CodexRPC(str(path), root, timeout=0.15)
            try:
                check(rpc)
            finally:
                rpc.close()
            self.assertIsNotNone(rpc.process.poll())

    def test_fragmented_reply_keeps_reroute_and_unknown_notice_in_order(self):
        body = """r=json.loads(sys.stdin.readline())
print(json.dumps({'method':'item/completed','params':{'text':'private transcript'}}),flush=True)
print(json.dumps({'method':'model/rerouted','params':{'fromModel':'requested','toModel':'other','reason':'service','threadId':'thread','turnId':'turn'}}),flush=True)
v=json.dumps({'id':r['id'],'result':{'account':{'type':'chatgpt'}}})+'\\n'
sys.stdout.write(v[:10]);sys.stdout.flush();time.sleep(.01)
sys.stdout.write(v[10:]);sys.stdout.flush()
time.sleep(1)
"""
        def check(rpc):
            self.assertEqual(rpc.request("account/read", {"refreshToken": False})["account"]["type"], "chatgpt")
            notes = compact_notifications(rpc.notifications)
            self.assertEqual([n["method"] for n in notes], ["item/completed", "model/rerouted"])
            self.assertEqual(notes[1]["toModel"], "other")
            self.assertNotIn("private transcript", str(notes))
        self.probe(body, check)

    def test_eof_malformed_mismatched_and_timeout_are_not_empty_results(self):
        for output, error in [("", "unavailable"), ("print('[]',flush=True)", "non-object"),
                              ("print('broken',flush=True)", "malformed"),
                              ("print('{\"id\":999,\"result\":{}}',flush=True)", "identity mismatch"),
                              ("time.sleep(1)", "timeout")]:
            with self.subTest(error=error):
                def check(rpc):
                    with self.assertRaisesRegex(Blocked, error): rpc.request("model/list", {})
                self.probe("sys.stdin.readline()\n" + output + "\n", check)

    def test_server_action_is_denied_and_no_turn_or_credit_mutation_can_be_sent(self):
        def check(rpc):
            for method, params in [("turn/start", {}), ("account/rateLimitResetCredit/consume", {}),
                                   ("account/read", {"refreshToken": True})]:
                with self.assertRaises(Blocked): rpc.request(method, params)
            self.assertEqual(rpc.sequence, 0)
            with self.assertRaisesRegex(Blocked, "client action"):
                rpc.request("model/list", {})
        self.probe("sys.stdin.readline()\nprint(json.dumps({'id':99,'method':'account/chatgptAuthTokens/refresh'}),flush=True)\nsys.stdin.readline()\n", check)

    def test_observation_memory_is_bounded(self):
        def check(rpc):
            with patch("opslib.execution.codex_rpc.MAX_FRAME", 16), self.assertRaisesRegex(Blocked, "bound"):
                rpc.request("model/list", {})
        self.probe("sys.stdin.readline()\nprint('x'*100,flush=True)\n", check)
        def notifications(rpc):
            with patch("opslib.execution.codex_rpc.MAX_NOTIFICATIONS", 1), self.assertRaisesRegex(Blocked, "bound"):
                rpc.request("model/list", {})
        self.probe("sys.stdin.readline()\nprint('{\"method\":\"test\"}\\n'*2,flush=True)\n", notifications)


class DiscoveryTests(unittest.TestCase):
    def test_allowance_redaction_and_unknown_paid_stale_exhausted_cases(self):
        window = {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": int(time.time()) + 300}
        bucket = {"limitId": "codex", "primary": window, "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                  "planType": "pro", "spendControlReached": False}
        value = providers.compact_allowance({"rateLimits": bucket, "accountId": "private-identity", "rateLimitResetCredits": {"id": "private-credit"}})
        self.assertNotIn("private", str(value))
        self.assertIsNone(value["exact_task_charge"])
        self.assertEqual(providers.allowance_check(value)["status"], "observed")
        for replacement in ({"credits": None}, {"credits": {"hasCredits": True}},
                            {"primary": {**window, "resetsAt": 1}},
                            {"primary": {**window, "usedPercent": 100}}, {"spendControlReached": None}):
            value = providers.compact_allowance({"rateLimits": {**bucket, **replacement}})
            self.assertNotEqual(providers.allowance_check(value)["status"], "observed")

    def test_launch_configuration_preserves_yolo_and_omits_provider_environment(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "private", "HTTPS_PROXY": "private", "PYTHONPATH": "private", "ORCA_TERMINAL_ID": "old"}):
            child = codex_launch.environment()
        self.assertFalse(set(child) & {"OPENAI_API_KEY", "HTTPS_PROXY", "PYTHONPATH", "ORCA_TERMINAL_ID"})
        args, fixed = codex_launch.overrides(policy.resolve("principal"), {"mcp_servers": {"node_repl": {}}})
        self.assertIn("mcp_servers.node_repl.enabled=false", args)
        self.assertEqual((fixed["model"], fixed["model_reasoning_effort"]), ("gpt-5.6-sol", "high"))
        self.assertEqual((fixed["approval_policy"], fixed["sandbox_mode"]), ("never", "danger-full-access"))
        with self.assertRaises(Blocked): codex_launch.overrides(policy.resolve("principal"), {"mcp_servers": {"ambiguous.name": {}}})


class HoldTests(unittest.TestCase):
    def observation(self, events, status="blocked", assignee=None):
        return {"task": {"id": "t_test", "status": status, "assignee": assignee}, "events": events}

    def test_created_blocked_and_unblocked_events_do_not_hold_dispatch(self):
        for value in [self.observation([]), self.observation([{"kind": "created", "payload": {"status": "blocked"}}]),
                      self.observation([{"kind": "blocked"}, {"kind": "unblocked"}]),
                      self.observation([{"kind": "blocked"}], "review"),
                      self.observation([{"kind": "blocked"}], assignee="worker")]:
            with self.assertRaises(Blocked): require_hermes_hold(value)
        self.assertEqual(require_hermes_hold(self.observation([{"kind": "blocked"}]))["id"], "t_test")

    def test_existing_hold_is_idempotent_and_active_work_is_never_blocked(self):
        h = Hermes("/fixture/hermes")
        with patch.object(h, "task", return_value=self.observation([{"kind": "blocked"}])), patch("opslib.execution.native.command") as cmd:
            self.assertFalse(h.hold("fixture", "t_test", "reason", apply=True)["mutated"])
            cmd.assert_not_called()
        for state in ("running", "done", "review", "scheduled"):
            with patch.object(h, "task", return_value=self.observation([], state)), patch("opslib.execution.native.command") as cmd:
                with self.assertRaises(Blocked): h.hold("fixture", "t_test", "reason", apply=True)
                cmd.assert_not_called()

    def test_hold_requires_readback_and_detects_a_claim_race(self):
        h = Hermes("/fixture/hermes")
        for after in (self.observation([]), self.observation([{"kind": "claimed"}, {"kind": "blocked"}])):
            with patch.object(h, "task", side_effect=[self.observation([], "ready"), after]), patch("opslib.execution.native.command"):
                with self.assertRaises(Blocked): h.hold("fixture", "t_test", "reason", apply=True)

    def test_integration_queue_is_created_in_unassigned_review_without_ready_window(self):
        h = Hermes("/fixture/hermes")
        observation = self.observation([{"kind": "created", "payload": {"status": "review"}}], "review")
        observation["task"]["created_by"] = "ops-execution"
        with patch("opslib.execution.native.command", return_value="--initial-status {blocked,running,review}"), patch.object(h, "read", return_value={"id": "t_test"}) as create, patch.object(h, "task", return_value=observation):
            self.assertEqual(h.create_integration("fixture", "t_source", "a" * 40, "fixture")["status"], "review")
        args = create.call_args.args
        self.assertEqual(args[args.index("--initial-status") + 1], "review")
        with self.assertRaises(Blocked): require_hermes_hold(observation)
        observation["task"]["assignee"] = "someone"
        with self.assertRaises(Blocked): require_hermes_hold(observation, integration_queue=True)

    def test_unsupported_atomic_queue_creation_never_creates_a_ready_card(self):
        h = Hermes("/fixture/hermes")
        with patch("opslib.execution.native.command", return_value="--initial-status {blocked,running}"), patch.object(h, "read") as mutate:
            with self.assertRaisesRegex(Blocked, "atomically"):
                h.create_integration("fixture", "t_source", "a" * 40, "fixture")
            mutate.assert_not_called()


if __name__ == "__main__": unittest.main()
