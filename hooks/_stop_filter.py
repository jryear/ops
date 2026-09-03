"""Stop-hook formatter: stdin is `ops safe --json`, stderr gets the warning.

Separate from stop-safe.sh only because inlining it there means quoting Python
through a shell heredoc, which is how the first version broke. Always exits 0 --
this hook informs, it never blocks Stop.
"""
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)

crit = [f for f in d.get("findings", [])
        if f.get("severity_name") == "action-required"]
if not crit:
    sys.exit(0)

w = sys.stderr.write
w("ops safe: work here exists in only one place\n")
for f in crit[:3]:
    w("  {}: {}\n".format(f.get("subject", "?"), f.get("evidence", "")))
    if f.get("suggested_action"):
        w("    -> {}\n".format(f["suggested_action"]))
sys.exit(0)
