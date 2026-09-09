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


class ProviderTests(unittest.TestCase):
    def test_auth_catalog_and_effort_fail_closed(self):
        for auth, models, expected in [
            ("apiKey", [{"model": "gpt-5.6-sol", "efforts": ["high"]}], "subscription authentication"),
            ("chatgpt", [], "absent"),
            ("chatgpt", [{"model": "gpt-5.6-sol", "efforts": ["low"]}], "unsupported"),
            ("chatgpt", [{"model": "gpt-5.6-sol", "efforts": ["high"]}], "rerouting")]:
            with self.subTest(auth=auth, models=models), patch.object(providers, "identity", return_value={"path": "/fixture/codex"}), patch.object(providers, "inspect_codex", return_value={"auth_source": auth, "models": models}):
                result = providers.verify(policy.resolve("principal"), Path("/fixture"))
                self.assertEqual(result["admission"], "blocked")
                self.assertIsNone(result["observed_route"])
                self.assertIsNone(result["economic_route"])
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

    def test_rpc_exit_always_reaps_only_its_own_probe(self):
        with patch.object(providers, "CodexRPC") as rpc:
            rpc.return_value.request.side_effect = Blocked("no account observation")
            with self.assertRaises(Blocked): providers.inspect_codex("/fixture/codex", "/fixture")
            rpc.return_value.close.assert_called_once()


if __name__ == "__main__": unittest.main()
