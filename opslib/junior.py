"""Read-only view of Junior Core.

Junior Core (`~/Junior/state/core.sqlite3`) is the coordination substrate:
projects, tasks, workers with leases, and an append-only event log.

It is deliberately treated as read-only here. `core/FROZEN_CORE_V01.json` pins
SHA-256 hashes over `schema.sql`, `junior_core.py`, `adapter_ingress.py` and the
Codex/Hermes runtime adapters. Writing through anything but Junior's own
authenticated adapter ingress would violate that boundary, and extending the
schema would break the frozen hashes outright. So `ops` reads and never writes.

Opened via SQLite URI in `mode=ro` so a bug here cannot mutate Junior's truth.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CORE_DB = Path.home() / "Junior" / "state" / "core.sqlite3"
FROZEN = Path.home() / "Junior" / "core" / "FROZEN_CORE_V01.json"

# Junior's own status vocabulary. Note it cannot express warm/dormant --
# that is precisely the gap the ops registry fills without touching this schema.
ACTIVE_TASK_STATES = ("ready", "running", "verifying")
BLOCKED_TASK_STATES = ("blocked",)
LIVE_WORKER_STATES = ("spawned", "running", "blocked")


def available() -> bool:
    return CORE_DB.exists()


def _connect() -> sqlite3.Connection | None:
    """Open strictly read-only; return None if unavailable or locked."""
    if not CORE_DB.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{CORE_DB}?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _rows(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    con = _connect()
    if con is None:
        return []
    try:
        return list(con.execute(sql, params))
    except sqlite3.Error:
        return []
    finally:
        con.close()


def projects() -> list[dict]:
    return [dict(r) for r in _rows(
        "select id, slug, name, status, desired_outcome, updated_at from projects"
    )]


def blocked_tasks() -> list[dict]:
    marks = ",".join("?" * len(BLOCKED_TASK_STATES))
    return [dict(r) for r in _rows(
        f"select t.id, t.title, t.status, t.updated_at, p.slug as project "
        f"from tasks t left join projects p on p.id = t.project_id "
        f"where t.status in ({marks})",
        BLOCKED_TASK_STATES,
    )]


def expired_leases() -> list[dict]:
    """Workers whose lease has passed but that are still in a live state.

    A live worker with an expired lease is the classic stuck-work signal: the
    authority to act has lapsed while the record still claims it is running.
    """
    marks = ",".join("?" * len(LIVE_WORKER_STATES))
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for r in _rows(
        f"select id, task_id, runtime, status, lease_expires_at, started_at "
        f"from workers where status in ({marks}) and lease_expires_at is not null",
        LIVE_WORKER_STATES,
    ):
        d = dict(r)
        if (d.get("lease_expires_at") or "") < now:
            out.append(d)
    return out


def last_event() -> dict | None:
    rows = _rows("select seq, event_type, created_at from events "
                 "order by seq desc limit 1")
    return dict(rows[0]) if rows else None


def counts() -> dict:
    """Row counts used to prove `ops` did not mutate Junior Core."""
    out = {}
    for t in ("projects", "tasks", "workers", "events", "artifacts"):
        rows = _rows(f"select count(*) as n from {t}")
        out[t] = rows[0]["n"] if rows else None
    return out
