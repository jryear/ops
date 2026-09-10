"""One ops execution namespace, preserving the existing health envelope/codes."""
from __future__ import annotations

import json
from pathlib import Path

from ..model import ACTION, Report, emit
from . import integration, policy, providers
from .common import Blocked, Store
from .native import Orca, Hermes
from .workflow import Execution


def parser(sub):
    p = sub.add_parser("execution", help="Policy, admission, evidence, review, integration and recovery")
    commands = p.add_subparsers(dest="execution_action", required=True)
    hold = commands.add_parser("hold", help="Record and verify an explicit Hermes dispatcher hold")
    hold.add_argument("board")
    hold.add_argument("task")
    hold.add_argument("--reason", required=True)
    hold.add_argument("--apply", action="store_true")
    hold.add_argument("--json", action="store_true")
    hold.add_argument("--quiet", action="store_true")
    for action in ("resolve", "verify-route", "validate", "dispatch", "status", "verify", "review", "queue", "integrate", "recover"):
        cmd = commands.add_parser(action)
        cmd.add_argument("--json", action="store_true")
        cmd.add_argument("--quiet", action="store_true")
        if action in ("resolve", "verify-route"):
            cmd.add_argument("route")
        if action == "verify-route":
            cmd.add_argument("--cwd", default=str(Path.cwd()))
            cmd.add_argument("--launch-config", action="store_true", help="verify isolated process overrides without inference")
            cmd.add_argument("--record", action="store_true", help="save compact redacted evidence locally")
        if action in ("validate", "dispatch"):
            cmd.add_argument("packet", type=Path)
        if action in ("status", "verify", "review", "queue", "integrate", "recover"):
            cmd.add_argument("attempt")
        if action in ("dispatch", "verify", "review", "queue", "integrate", "recover"):
            cmd.add_argument("--apply", action="store_true", help="perform the specifically named operation after its gates pass")
        if action == "verify":
            cmd.add_argument("--commit", required=True)
        if action == "review":
            cmd.add_argument("--receipt", required=True, type=Path)
        if action == "integrate":
            cmd.add_argument("--queue-task", required=True)
            cmd.add_argument("--expected-head", required=True)


def run(args):
    rep = Report("execution")
    rep.checked["action"] = args.execution_action
    try:
        action = args.execution_action
        if action == "hold":
            result = Hermes().hold(args.board, args.task, args.reason, apply=args.apply)
        elif action == "resolve":
            result = policy.resolve(args.route)
        elif action == "verify-route":
            result = providers.verify(policy.resolve(args.route), Path(args.cwd), Orca(), launch_config=args.launch_config)
            if args.record:
                result["evidence"] = Store().put(result)
            if result["admission"] != "admitted":
                rep.add(ACTION, "execution.route_blocked", args.route,
                        "; ".join(result["blockers"]), "Keep this lane blocked until missing evidence is established.")
        elif action == "validate":
            packet = policy.validate_packet(json.loads(args.packet.read_text()))
            result = {"schema_valid": True, "task_id": packet["task_id"], "admission": "not-checked"}
        else:
            execution = Execution()
            if action == "dispatch":
                result = execution.dispatch(json.loads(args.packet.read_text()), args.apply)
                if not args.apply and result["admission"]["admission"] != "admitted":
                    rep.add(ACTION, "execution.route_blocked", args.packet.name,
                            "; ".join(result["admission"]["blockers"]), "Dispatch remains withheld.")
            elif action == "status":
                result = execution.status(args.attempt)
            elif action == "recover":
                result = execution.recover(args.attempt, args.apply)
            elif action == "verify":
                result = integration.verify_candidate(execution, args.attempt, args.commit, args.apply)
                if result.get("passed") is False:
                    rep.add(ACTION, "execution.verification_failed", args.attempt, "Candidate checks failed.")
            elif action == "review":
                result = integration.review(execution, args.attempt, json.loads(args.receipt.read_text()), args.apply)
            elif action == "queue":
                result = integration.queue(execution, args.attempt, args.apply)
            else:
                result = integration.integrate(execution, args.attempt, args.queue_task, args.expected_head, args.apply)
        rep.checked["result"] = result
    except (Blocked, OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, Blocked) else "Required input or observation is unavailable or malformed (" + type(exc).__name__ + ")."
        rep.add(ACTION, "execution.blocked", args.execution_action, message,
                "Preserve evidence and reconcile the exact boundary; no automatic substitution or takeover.")
    return emit(rep, args.json, args.quiet)
