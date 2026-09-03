"""Registry tests.

Two things here are worth pinning down. The first is remote-URL canonicalisation:
the same GitHub repo is written four different ways across git config, Orca's
`gitRemoteIdentity` and `gh repo list`, and the registry joins on that key. If it
normalises inconsistently, every project reads as "remote missing" and the whole
index becomes noise.

The second is that a round-trip through the hand-rolled TOML writer preserves
what went in -- there is no stdlib TOML writer, so save/load is our own code.

Run: python3 tests/test_registry.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opslib import registry as reg  # noqa: E402

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got={got!r} want={want!r}")
        FAILS.append(name)


print("=== canonical keys: four spellings, one repo ===")
K = "github.com/jryear/ops"
for form in ("https://github.com/jryear/ops.git",
             "https://github.com/jryear/ops",
             "git@github.com:jryear/ops.git",
             "ssh://git@github.com/jryear/ops.git"):
    check(f"{form} -> {K}", reg._canonical_key(form), K)

check("empty remote -> empty key", reg._canonical_key(""), "")
check("whitespace only -> empty key", reg._canonical_key("   "), "")

print()
print("=== TOML round-trip (no stdlib writer, so this is our code) ===")
orig = reg.REGISTRY
with tempfile.TemporaryDirectory() as d:
    reg.REGISTRY = Path(d) / "registry.toml"
    e = reg.Entry(id="demo", name="demo", path="/tmp/demo",
                  remote="github.com/jryear/demo", lifecycle="dormant",
                  runtime="orca", description='has "quotes" and a \\ backslash',
                  last_verified="2026-09-03T00:00:00+00:00")
    reg.add(e)
    back = reg.load()
    check("entry survives round-trip", back["demo"].to_dict(), e.to_dict())

    # A second add must update in place, not duplicate the table.
    e2 = reg.Entry(id="demo", name="demo", path="/tmp/demo", lifecycle="frozen")
    reg.add(e2)
    check("re-adding the same id updates in place", len(reg.load()), 1)
    check("lifecycle updated", reg.load()["demo"].lifecycle, "frozen")

    reg.REGISTRY = Path(d) / "missing.toml"
    check("absent registry loads as empty, not an error", reg.load(), {})
reg.REGISTRY = orig

print()
print("=== lifecycle vocabulary is closed ===")
check("four lifecycles, no more", sorted(reg.LIFECYCLES),
      ["active", "dormant", "frozen", "warm"])
check("default is warm, not active", reg.DEFAULT_LIFECYCLE, "warm")

print()
print("=== newproj refuses bad input before touching the disk ===")
from opslib import newproj  # noqa: E402

for bad in ("../escape", "Has Spaces", "-leading-dash", ""):
    try:
        newproj.plan(bad, "labs", "warm", "")
        check(f"rejects {bad!r}", "accepted", "rejected")
    except newproj.Failed:
        check(f"rejects {bad!r}", "rejected", "rejected")

try:
    newproj.plan("fine", "labs", "nonsense", "")
    check("rejects unknown lifecycle", "accepted", "rejected")
except newproj.Failed:
    check("rejects unknown lifecycle", "rejected", "rejected")

p = newproj.plan("fine-name", "labs", "warm", "")
check("plan defaults under ~/dev/<area>/", p["path"],
      str(Path.home() / "dev/labs/fine-name"))
check("plan pushes before it registers",
      p["steps"].index("gh repo create --private --source . --push")
      < p["steps"].index("register in registry.toml as lifecycle=warm"), True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
    sys.exit(1)
print("all registry tests passed")
