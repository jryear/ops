"""Per-process Codex configuration, verified without submitting a model turn.

This is a launch specification, not provider execution evidence or permission
to dispatch. Existing configuration and credential stores are never edited.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

from .common import Blocked, digest, encoded
from .codex_rpc import CodexRPC

DISABLED_FEATURES = ("hooks", "plugins", "remote_plugin", "apps", "memories",
                     "multi_agent", "multi_agent_v2", "tool_suggest", "chronicle",
                     "guardian_approval", "fast_mode", "goals", "image_generation",
                     "computer_use", "browser_use", "in_app_browser")


def environment():
    # Retain ordinary OS identity and terminal presentation, not provider keys,
    # routing URLs, proxies, Python injection or agent-session identity.
    keep = {"HOME", "USER", "LOGNAME", "PATH", "SHELL", "TMPDIR", "LANG", "LC_ALL",
            "TERM", "COLORTERM", "TERM_PROGRAM"}
    return {key: value for key, value in os.environ.items() if key in keep}


def overrides(route, config):
    fixed = {"model": route["model"], "model_reasoning_effort": route["effort"],
             "model_provider": "openai", "forced_login_method": "chatgpt",
             "approval_policy": "never", "sandbox_mode": "danger-full-access",
             "service_tier": "default", "web_search": "disabled"}
    # JSON strings are valid TOML string values. These are argv entries, not shell text.
    import json
    result = [key + "=" + json.dumps(value) for key, value in fixed.items()]
    result += ["features." + key + "=false" for key in DISABLED_FEATURES]
    for name in (config.get("mcp_servers") or {}):
        # Codex's -c path parser splits literal dots; quoted TOML key segments
        # are not unquoted by that parser. Never create a second quoted server.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise Blocked("MCP server name cannot be safely addressed by the installed override interface")
        result.append("mcp_servers." + name + ".enabled=false")
    return result, fixed


def inspect(executable, route, cwd):
    """Verify effective overrides on a new, owned discovery process only."""
    target = Path(executable["resolved_path"])
    if digest(target.read_bytes()) != executable["sha256"]:
        raise Blocked("Codex executable changed before configuration verification")
    child_env = environment()
    # No forced auth until the account has been observed in this same environment.
    rpc = CodexRPC(str(target), cwd, env=child_env)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "ops-launch-discovery", "version": "1"}})
        rpc.notify_initialized()
        account = rpc.request("account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise Blocked("isolated launch environment does not have ChatGPT authentication")
        config = rpc.request("config/read", {"cwd": str(cwd)})["config"]
    finally:
        rpc.close()
    if (config.get("openai_base_url") not in (None, "https://api.openai.com/v1", "https://api.openai.com/v1/")
            or config.get("chatgpt_base_url") not in (None, "https://chatgpt.com/backend-api", "https://chatgpt.com/backend-api/")
            or (config.get("model_providers") or {}).get("openai")):
        raise Blocked("custom OpenAI routing is present; its economic and auth route is unverified")
    args, fixed = overrides(route, config)
    rpc = CodexRPC(str(target), cwd, overrides=args, env=child_env)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "ops-launch-discovery", "version": "1"}})
        rpc.notify_initialized()
        actual = rpc.request("config/read", {"cwd": str(cwd)})["config"]
        account = rpc.request("account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise Blocked("isolated configuration authentication changed")
        mismatches = [key for key, value in fixed.items() if actual.get(key) != value]
        features = actual.get("features") or {}
        mismatches += ["features." + key for key in DISABLED_FEATURES if features.get(key) is not False]
        servers = actual.get("mcp_servers") or {}
        mismatches += ["mcp_servers." + name for name, value in servers.items() if value.get("enabled") is not False]
        if mismatches:
            raise Blocked("effective launch configuration mismatch: " + ", ".join(mismatches))
        if digest(target.read_bytes()) != executable["sha256"]:
            raise Blocked("Codex executable changed during configuration verification")
        return {"configuration_verified": True, "executable_sha256": executable["sha256"],
                "config_sha256": digest(encoded(actual)), "overrides": args,
                "effective": fixed, "disabled_features": list(DISABLED_FEATURES),
                "disabled_mcp_servers": sorted(servers), "environment_keys": sorted(child_env),
                "auth_source": account["type"], "inference_performed": False,
                "observed_launch": None, "observed_route": None,
                "auxiliary_economic_route": "unknown", "runtime_bounds": "not-yet-enforced"}
    finally:
        rpc.close()
