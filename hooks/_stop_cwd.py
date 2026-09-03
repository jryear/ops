"""Extract `cwd` from the hook payload on stdin. Prints nothing on any failure."""
import json
import sys

try:
    print(json.load(sys.stdin).get("cwd") or "")
except Exception:
    print("")
