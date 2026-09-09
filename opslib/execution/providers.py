"""Read-only eligibility discovery. No credential stores or provider inference.

Catalog/config eligibility is deliberately separate from admission. In particular,
neither ChatGPT Pro nor Google OAuth proves that every auxiliary call is free of
paid fallback. Unsupported guarantees remain blocked, never operator booleans.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time

from .common import Blocked, command, digest, encoded, json_command


class CodexRPC:
    def __init__(self, executable, cwd):
        self.process = subprocess.Popen([executable, "app-server", "--stdio"], cwd=cwd,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0)
        self.buffer = b""
        self.sequence = 0

    def request(self, method, params):
        self.sequence += 1
        self.process.stdin.write(encoded({"id": self.sequence, "method": method, "params": params}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if b"\n" not in self.buffer:
                with selectors.DefaultSelector() as select:
                    select.register(self.process.stdout, selectors.EVENT_READ)
                    if not select.select(min(2, max(0, deadline - time.monotonic()))):
                        continue
                chunk = self.process.stdout.read(65536)
                if not chunk:
                    raise Blocked("Codex App Server observation unavailable; host state access may be required")
                self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                value = json.loads(line)
                if value.get("id") == self.sequence:
                    if "error" in value:
                        raise Blocked("Codex discovery RPC rejected " + method)
                    return value["result"]
        raise Blocked("Codex discovery timeout: " + method)

    def close(self):
        # This process is created by this probe, never an existing agent session.
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()


def identity(name):
    executable = shutil.which(name)
    if not executable:
        raise Blocked(name + " executable unavailable")
    target = Path(executable).resolve()
    return {"path": executable, "resolved_path": str(target),
            "sha256": digest(target.read_bytes()), "version": command([executable, "--version"]).strip()}


def environment_risks():
    # Names only. Never persist values of inherited auth/routing variables.
    prefixes = ("OPENAI_", "CODEX_", "ANTHROPIC_", "CLAUDE_", "GEMINI_", "GOOGLE_", "AWS_", "AZURE_", "VERTEX_", "HERMES_")
    return sorted(k for k in os.environ if k.startswith(prefixes) or k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))


def inspect_codex(executable, cwd):
    rpc = CodexRPC(executable, cwd)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "ops-execution", "version": "1"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.process.stdin.write(encoded({"method": "initialized"}))
        account = rpc.request("account/read", {"refreshToken": False}).get("account") or {}
        models, cursor = [], None
        for _ in range(20):
            page = rpc.request("model/list", {"includeHidden": True, "limit": 100, **({"cursor": cursor} if cursor else {})})
            models.extend(page["data"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        if cursor:
            raise Blocked("Codex catalog pagination incomplete")
        config = rpc.request("config/read", {"cwd": str(cwd), "includeLayers": True})
        body = config["config"]
        return {"auth_source": account.get("type"), "plan": account.get("planType"),
                "models": [{"model": m["model"], "efforts": [e["reasoningEffort"] for e in m["supportedReasoningEfforts"]]} for m in models],
                "config_sha256": digest(encoded(body)),
                "effective": {k: body.get(k) for k in ("model", "model_provider", "model_reasoning_effort", "forced_login_method", "sandbox_mode", "approval_policy", "service_tier")},
                "auxiliary_surfaces": {k: bool(body.get(k)) for k in ("apps", "plugins", "mcp_servers", "hooks", "agents", "memories", "auto_review", "tool_suggest")},
                "layers": [{"name": x.get("name"), "version": x.get("version")} for x in config.get("layers", [])]}
    finally:
        rpc.close()


def verify(route, cwd, orca=None):
    result = {"schema_version": 1, "recorded_at": time.time(), "route": route,
              "eligibility": "unknown", "admission": "blocked", "observed_route": None,
              "economic_route": None, "fallback": "unknown", "blockers": [],
              "environment_names": environment_risks()}
    try:
        exe = identity(route["provider"])
        result["executable"] = exe
        if route["provider"] == "codex":
            data = inspect_codex(exe["path"], cwd)
            result["discovery"] = data
            models = {x["model"]: x["efforts"] for x in data["models"]}
            if route["model"] not in models:
                result["blockers"].append("requested model is absent from this executable's catalog")
            elif route["effort"] not in models[route["model"]]:
                result["blockers"].append("requested effort is unsupported by this executable/model")
            elif data["auth_source"] == "chatgpt":
                result["eligibility"] = "catalog-and-subscription-auth-eligible"
            else:
                result["blockers"].append("ChatGPT subscription authentication is not observed")
            result["blockers"] += [
                "no supported pre-dispatch guarantee disabling server model rerouting has been established",
                "subscription auth does not establish no-credit use for all main and auxiliary calls",
                "stock worker-start launch lacks executable/config pinning and enforced runtime/permission bounds; a verified custom launcher is required"]
        elif route["provider"] == "claude":
            auth = json_command([exe["path"], "auth", "status", "--json"])
            result["discovery"] = {k: auth.get(k) for k in ("loggedIn", "authMethod", "apiProvider", "subscriptionType")}
            result["blockers"] += ["exact model catalog/effort and auxiliary-call routing not established through a supported discovery interface",
                                   "Orca launch does not attest absence of API-key fallback, utility calls, plugins and inherited routing",
                                   "the installed ops adapter has no verified custom launcher enforcing read-only review and runtime bounds"]
        elif route["provider"] == "gemini":
            settings = Path.home() / ".gemini/settings.json"
            config = json.loads(settings.read_text()) if settings.exists() else {}
            result["discovery"] = {"auth_source": config.get("security", {}).get("auth", {}).get("selectedType"),
                                   "settings_sha256": digest(encoded(config))}
            result["blockers"] += ["Gemini 3.8 Flash exact identifier, availability and economic route unverified",
                                   "documented automatic and utility-call fallback has no verified disable guarantee",
                                   "Orca per-launch model/effort preference interface does not advertise Gemini support"]
        else:
            result["blockers"].append("provider adapter is not implemented")
        if orca:
            state = orca.call("status")
            result["orca"] = {"runtime_id": state.get("runtime", {}).get("runtimeId"),
                               "version": state.get("runtime", {}).get("appVersion"),
                               "capabilities": [x for x in state.get("runtime", {}).get("capabilities", []) if x.startswith(("orchestration.", "agent-session."))]}
    except (Blocked, OSError, ValueError, KeyError, TypeError) as exc:
        result["blockers"].append(str(exc) if isinstance(exc, Blocked) else "provider discovery returned incomplete or unusable data")
    return result


def require_admission(observation):
    if observation.get("admission") != "admitted" or observation.get("blockers"):
        raise Blocked("route admission withheld: " + "; ".join(observation.get("blockers", ["required evidence missing"])))
    if time.time() - observation["recorded_at"] > 60:
        raise Blocked("route admission observation expired")
