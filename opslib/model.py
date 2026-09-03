"""Shared finding model.

Every verb emits Findings. Machines read them from `--json`; humans read the
rendered form. Keeping one shape means Kiki, Junior or a shell script can
consume any verb's output without knowing which verb produced it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict

# Severities double as exit codes, so `ops safe && deploy` behaves sensibly.
OK = 0        # healthy: no action
WARN = 1      # worth attention, not urgent
ACTION = 2    # requires a human decision now

NAMES = {OK: "healthy", WARN: "warning", ACTION: "action-required"}
ICONS = {OK: "ok", WARN: "warn", ACTION: "ACTION"}


@dataclass
class Finding:
    severity: int
    kind: str                  # stable machine key, e.g. "repo.no_remote"
    subject: str               # what it is about (a path, pid, host resource)
    evidence: str              # why we believe it, in concrete terms
    suggested_action: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity_name"] = NAMES[self.severity]
        return d


@dataclass
class Report:
    verb: str
    findings: list[Finding] = field(default_factory=list)
    checked: dict = field(default_factory=dict)   # what was examined, for context
    notes: list[str] = field(default_factory=list)

    def add(self, *args, **kw) -> None:
        self.findings.append(Finding(*args, **kw))

    @property
    def exit_code(self) -> int:
        return max((f.severity for f in self.findings), default=OK)

    def to_dict(self) -> dict:
        return {
            "verb": self.verb,
            "exit_code": self.exit_code,
            "summary": {
                "action_required": sum(1 for f in self.findings if f.severity == ACTION),
                "warnings": sum(1 for f in self.findings if f.severity == WARN),
            },
            "checked": self.checked,
            "notes": self.notes,
            "findings": [f.to_dict() for f in
                         sorted(self.findings, key=lambda x: -x.severity)],
        }


def emit(report: Report, as_json: bool, quiet: bool = False) -> int:
    """Print a report in the requested form and return its exit code."""
    if as_json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return report.exit_code

    findings = sorted(report.findings, key=lambda x: -x.severity)
    if quiet:
        findings = [f for f in findings if f.severity == ACTION]

    if not findings:
        if not quiet:
            scope = ", ".join(f"{v} {k}" for k, v in report.checked.items())
            print(f"{report.verb}: nothing needs attention" + (f" ({scope})" if scope else ""))
            # Notes carry context that is most useful precisely when there are
            # no findings -- e.g. reconcile's "25 local repos are unregistered".
            for n in report.notes:
                print(f"note: {n}")
        return report.exit_code

    width = 78
    for sev in (ACTION, WARN):
        group = [f for f in findings if f.severity == sev]
        if not group:
            continue
        label = "ACTION REQUIRED" if sev == ACTION else "WARNINGS"
        print(f"\n{label}  ({len(group)})")
        print("-" * width)
        for f in group:
            print(f"  {f.subject}")
            print(f"    {f.evidence}")
            if f.suggested_action:
                print(f"    -> {f.suggested_action}")
            print()

    if not quiet:
        for n in report.notes:
            print(f"note: {n}")
        scope = ", ".join(f"{v} {k}" for k, v in report.checked.items())
        if scope:
            print(f"checked: {scope}")
    return report.exit_code
