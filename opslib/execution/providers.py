"""Read-only eligibility discovery. No credential stores or provider inference.

Catalog/config eligibility is deliberately separate from admission. In particular,
neither ChatGPT Pro nor Google OAuth proves that every auxiliary call is free of
paid fallback. Unsupported guarantees remain blocked, never operator booleans.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

from .common import Blocked, command, digest, encoded, json_command
from .policy import YOLO_LAUNCH
from .codex_rpc import CodexRPC, compact_notifications

PERMISSION_ARGS = {
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "claude": ["--dangerously-skip-permissions"],
    "gemini": ["--approval-mode", "yolo", "--sandbox=false"],
}


def inspect_permission_interface(provider, executable):
    """Discover native YOLO flags without launching a model or claiming execution."""
    args = PERMISSION_ARGS.get(provider)
    if args is None:
        raise Blocked("provider YOLO permission interface is unsupported")
    help_text = command([executable, "--help"])
    for arg in args:
        if arg.startswith("--") and arg.split("=", 1)[0] not in help_text:
            raise Blocked("installed provider does not advertise the requested YOLO permission flag")
    return {"argv": args, "help_sha256": digest(help_text.encode()),
            "evidence": "advertised-interface-only"}


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


def compact_allowance(value):
    """An account observation, never an exact task charge or reservation."""
    if not isinstance(value, dict) or not isinstance(value.get("rateLimits"), dict):
        raise Blocked("Codex allowance observation unavailable")
    buckets = value.get("rateLimitsByLimitId")
    if buckets is not None and (not isinstance(buckets, dict)
                                or not all(isinstance(v, dict) for v in buckets.values())):
        raise Blocked("Codex allowance buckets are malformed")

    def compact(bucket):
        item = {k: bucket.get(k) for k in ("limitId", "planType", "rateLimitReachedType", "spendControlReached")}
        for name in ("primary", "secondary"):
            window = bucket.get(name)
            if window is not None and not isinstance(window, dict):
                raise Blocked("Codex allowance window is malformed")
            item[name] = None if window is None else {k: window.get(k) for k in
                         ("usedPercent", "windowDurationMins", "resetsAt")}
        credits = bucket.get("credits")
        if credits is not None and not isinstance(credits, dict):
            raise Blocked("Codex credit observation is malformed")
        item["credits"] = None if credits is None else {k: credits.get(k) for k in
                          ("hasCredits", "unlimited", "balance")}
        return item

    return {"default": compact(value["rateLimits"]),
            "buckets": {k: compact(v) for k, v in (buckets or {}).items()},
            "exact_task_charge": None, "reservation": False}


def allowance_check(value):
    bucket = (value or {}).get("default") or {}
    credits = bucket.get("credits")
    if not isinstance(credits, dict):
        return {"status": "unknown", "reason": "paid credit availability was not returned"}
    if credits.get("hasCredits") is not False or credits.get("unlimited") is not False or credits.get("balance") != "0":
        return {"status": "blocked", "reason": "paid or unlimited credit availability cannot be excluded"}
    windows = [bucket.get(k) for k in ("primary", "secondary") if bucket.get(k) is not None]
    if not windows or any(type(w.get("usedPercent")) not in (int, float) or not 0 <= w["usedPercent"] < 100
                          or type(w.get("resetsAt")) not in (int, float) or w["resetsAt"] <= time.time()
                          for w in windows):
        return {"status": "blocked", "reason": "remaining subscription allowance is unavailable, exhausted or stale"}
    if bucket.get("rateLimitReachedType") or bucket.get("spendControlReached") is not False:
        return {"status": "blocked", "reason": "an account usage limit is reached or unknown"}
    return {"status": "observed", "reason": "remaining allowance and zero paid credits at observation time; no reservation or future no-credit guarantee"}


def inspect_codex(executable, cwd):
    rpc = CodexRPC(executable, cwd)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "ops-execution", "version": "1"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify_initialized()
        account = rpc.request("account/read", {"refreshToken": False}).get("account") or {}
        limits = rpc.request("account/rateLimits/read", {})
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
                "allowance": compact_allowance(limits),
                "notifications": compact_notifications(rpc.notifications),
                "effective": {k: body.get(k) for k in ("model", "model_provider", "model_reasoning_effort", "forced_login_method", "sandbox_mode", "approval_policy", "service_tier")},
                "auxiliary_surfaces": {k: bool(body.get(k)) for k in ("apps", "plugins", "mcp_servers", "hooks", "agents", "memories", "auto_review", "tool_suggest")},
                "layers": [{"name": x.get("name"), "version": x.get("version")} for x in config.get("layers", [])]}
    finally:
        rpc.close()


def verify(route, cwd, orca=None, *, launch_config=False):
    result = {"schema_version": 1, "recorded_at": time.time(), "route": route,
              "eligibility": "unknown", "admission": "blocked", "observed_route": None,
              "requested_launch": route.get("launch"), "observed_launch": None,
              "economic_route": None, "fallback": "unknown", "blockers": [],
              "inference_performed": False, "checks": {},
              "environment_names": environment_risks()}
    try:
        exe = identity(route["provider"])
        result["executable"] = exe
        result["permission_interface"] = inspect_permission_interface(route["provider"], exe["path"])
        if route["provider"] == "codex":
            data = inspect_codex(exe["path"], cwd)
            result["discovery"] = data
            result["checks"]["account_allowance"] = allowance_check(data.get("allowance"))
            notices = data.get("notifications", [])
            if any(n.get("method") == "model/rerouted" for n in notices):
                result["blockers"].append("provider reported model rerouting during observation")
            if result["checks"]["account_allowance"]["status"] != "observed":
                result["blockers"].append(result["checks"]["account_allowance"]["reason"])
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
                "stock worker-start launch lacks executable/config pinning, verified YOLO permissions and runtime bounds; a verified custom launcher is required"]
            if launch_config:
                from .codex_launch import inspect
                result["launch_configuration"] = inspect(exe, route, cwd)
        elif route["provider"] == "claude":
            auth = json_command([exe["path"], "auth", "status", "--json"])
            result["discovery"] = {k: auth.get(k) for k in ("loggedIn", "authMethod", "apiProvider", "subscriptionType")}
            result["blockers"] += ["exact model catalog/effort and auxiliary-call routing not established through a supported discovery interface",
                                   "Orca launch does not attest absence of API-key fallback, utility calls, plugins and inherited routing",
                                   "the installed ops adapter has no verified custom launcher establishing YOLO permissions and runtime bounds"]
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
    if observation.get("requested_launch") != YOLO_LAUNCH or observation.get("observed_launch") != YOLO_LAUNCH:
        raise Blocked("YOLO launch permissions are unverified or differ from the requested policy")
