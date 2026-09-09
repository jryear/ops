"""Bounded recovery decisions. This module never starts or takes over a worker."""
from __future__ import annotations

from .common import Blocked

STAGES = ("initial", "correction", "diagnosis", "escalation", "reframe")


def next_stage(history, limits, workers, *, diagnosis_accepted=False, escalation_authorized=False):
    if not workers or any(w.get("projection", {}).get("liveness", {}).get("verdict") != "exited" for w in workers):
        raise Blocked("native exit is not proven; reconciliation only, no takeover")
    if any(w.get("terminalState") != "released" for w in workers):
        raise Blocked("native resource ownership has not been released")
    if any(stage not in STAGES for stage in history) or len(history) != len(set(history)):
        raise Blocked("invalid or repeated recovery stage")
    allowed = list(STAGES)
    if not limits["corrections"]:
        allowed.remove("correction")
    if history != allowed[:len(history)]:
        raise Blocked("recovery sequence skipped a required stage")
    if len(history) >= limits["max_attempts"] or len(history) >= len(allowed):
        return "principal"
    stage = allowed[len(history)]
    if stage == "escalation" and not (diagnosis_accepted and escalation_authorized):
        raise Blocked("accepted independent diagnosis and explicit escalation authority are required")
    return stage
