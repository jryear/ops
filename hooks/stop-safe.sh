#!/bin/sh
# Stop hook: warn when the repo just worked in holds the only copy of real work.
#
# Deliberately narrow. It scopes to the session's cwd rather than scanning all
# ~165 checkouts (0.5s instead of 3s), warns only on `critical` -- real history
# with no remote anywhere -- and always exits 0. A hook that blocks Stop, or
# that fires on ordinary unpushed work, gets disabled within a week and then
# protects nothing.
#
# It calls the same `ops safe` that Codex, Orca and the shell call. The hook is
# an adapter; the logic lives in opslib/safe.py.

set -u
HERE=$(dirname "$0")
OPS="$HOME/dev/agents/ops/ops"
PY=/usr/bin/python3

[ -x "$OPS" ] || exit 0

CWD=$(cat 2>/dev/null | "$PY" "$HERE/_stop_cwd.py" 2>/dev/null)
[ -n "$CWD" ] || CWD=$PWD
[ -d "$CWD" ] || exit 0

"$OPS" safe --root "$CWD" --json 2>/dev/null | "$PY" "$HERE/_stop_filter.py"
exit 0
