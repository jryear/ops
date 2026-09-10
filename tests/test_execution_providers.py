"""Provider eligibility is never promoted to runtime or billing proof."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opslib.execution import policy, providers
from opslib.execution.common import Blocked
from opslib.execution.native import Orca


class ProviderTests(unittest.TestCase):
    def test_paginated_or_truncated_ownership_inventory_is_not_complete(self):
        for result in ({"workers": [], "page": {"hasMore": True, "nextCursor": "next"}},
                       {"workers": [], "truncated": True},
                       {"workers": [], "page": ["malformed"]},
                       {"workers": [], "hostScope": {"omittedHostIds": ["remote"]}}):
            with patch.object(Orca, "call", return_value=result), self.assertRaises(Blocked):
                Orca("/fixture/orca").workers()

    def test_auth_catalog_and_effort_fail_closed(self):
        for auth, models, expected in [
            ("apiKey", [{"model": "gpt-5.6-sol", "efforts": ["high"]}], "subscription authentication"),
            ("chatgpt", [], "absent"),
            ("chatgpt", [{"model": "gpt-5.6-sol", "efforts": ["low"]}], "unsupported"),
            ("chatgpt", [{"model": "gpt-5.6-sol", "efforts": ["high"]}], "rerouting")]:
            with self.subTest(auth=auth, models=models), patch.object(providers, "identity", return_value={"path": "/fixture/codex"}), patch.object(providers, "inspect_permission_interface", return_value={"evidence": "fixture-only"}), patch.object(providers, "inspect_codex", return_value={"auth_source": auth, "models": models}):
                result = providers.verify(policy.resolve("principal"), Path("/fixture"))
                self.assertEqual(result["admission"], "blocked")
                self.assertIsNone(result["observed_route"])
                self.assertIsNone(result["economic_route"])
                self.assertIsNone(result["observed_launch"])
                self.assertEqual(result["requested_launch"], policy.YOLO_LAUNCH)
                self.assertIn(expected, "; ".join(result["blockers"]))
                with self.assertRaises(Blocked): providers.require_admission(result)

    def test_failed_auth_observation_is_unknown_and_does_not_leak_values(self):
        with patch.object(providers, "identity", side_effect=Blocked("account unavailable")), patch.dict(providers.os.environ, {"OPENAI_API_KEY": "test-private-value"}):
            result = providers.verify(policy.resolve("principal"), Path("/fixture"))
        self.assertNotIn("test-private-value", str(result))
        self.assertIn("OPENAI_API_KEY", result["environment_names"])
        self.assertEqual(result["eligibility"], "unknown")

    def test_paid_fallback_and_automatic_substitution_policy_are_rejected(self):
        for key in ("paid_fallback", "automatic_substitution"):
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "policy.toml"
                target.write_text(policy.POLICY.read_text().replace(key + " = false", key + " = true"))
                with self.assertRaisesRegex(Blocked, "weakens"):
                    policy.load(target)

    def test_admission_expiry(self):
        with self.assertRaisesRegex(Blocked, "expired"):
            providers.require_admission({"admission": "admitted", "blockers": [], "recorded_at": time.time() - 61})

    def test_new_routes_require_yolo_including_reviewers(self):
        current, _, _ = policy.load()
        for route in current["routes"]:
            self.assertEqual(policy.resolve(route)["launch"], policy.YOLO_LAUNCH)
        for old, new in [('permissions = "yolo"', 'permissions = "workspace-write"'),
                         ('approval_policy = "never"', 'approval_policy = "on-request"'),
                         ('sandbox = "none"', 'sandbox = "read-only"')]:
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "policy.toml"
                target.write_text(policy.POLICY.read_text().replace(old, new))
                with self.assertRaisesRegex(Blocked, "YOLO"):
                    policy.load(target)

    def test_permission_catalog_is_not_observed_execution(self):
        for provider, args in providers.PERMISSION_ARGS.items():
            help_text = " ".join(a.split("=", 1)[0] for a in args)
            with patch.object(providers, "command", return_value=help_text):
                result = providers.inspect_permission_interface(provider, "/fixture/cli")
            self.assertEqual(result["argv"], args)
            self.assertEqual(result["evidence"], "advertised-interface-only")
            with patch.object(providers, "command", return_value="unsupported"), self.assertRaises(Blocked):
                providers.inspect_permission_interface(provider, "/fixture/cli")
        for observed in (None, {**policy.YOLO_LAUNCH, "approval_policy": "on-request"},
                         {**policy.YOLO_LAUNCH, "sandbox": "workspace-write"}):
            with self.assertRaisesRegex(Blocked, "YOLO launch permissions"):
                providers.require_admission({"admission": "admitted", "blockers": [], "recorded_at": time.time(),
                    "requested_launch": dict(policy.YOLO_LAUNCH), "observed_launch": observed})

    def test_rpc_exit_always_reaps_only_its_own_probe(self):
        with patch.object(providers, "CodexRPC") as rpc:
            rpc.return_value.request.side_effect = Blocked("no account observation")
            with self.assertRaises(Blocked): providers.inspect_codex("/fixture/codex", "/fixture")
            rpc.return_value.close.assert_called_once()


if __name__ == "__main__": unittest.main()
