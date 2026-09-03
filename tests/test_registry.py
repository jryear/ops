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
print("=== identity collision reports, it does not conclude ===")
# Zack's constraint: a shared remote has five plausible causes and only a human
# can choose among them. The finding must state graph facts and stop. These
# words are verdicts that crept in once and must not come back.
VERDICTS = ("stale checkout", "is a clone", "not a fork",
            "duplicate of", "safe to delete", "should be removed")

# The disclaimer legitimately contains "canonical"; strip it before scanning so
# the check tests the factual half, not the sentence that exists to disclaim.
DISCLAIMER = "Git cannot settle which is canonical"
def facts(t):
    return t.split(DISCLAIMER)[0]

import tempfile as _tf, subprocess as _sp, os as _os

def _repo(d, commits, salt=""):
    # `salt` keeps two temp repos from producing byte-identical commits, which
    # git would give the same SHA -- making a "separate histories" test silently
    # compare a repo against itself.
    _sp.run(["git", "init", "-q", "-b", "main", d], check=True)
    for i in range(commits):
        open(_os.path.join(d, f"f{i}.txt"), "w").write(f"{salt}{i}")
        _sp.run(["git", "-C", d, "add", "-A"], check=True)
        _sp.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", f"c{i}"], check=True)

with _tf.TemporaryDirectory() as d:
    a, b = _os.path.join(d, "a"), _os.path.join(d, "b")
    _os.makedirs(a); _repo(a, 3, "alpha-")
    _sp.run(["git", "clone", "-q", a, b], check=True)          # b is contained in a
    txt = reg._lineage({"alpha": a, "beta": b}, ["alpha", "beta"])
    check("states the shared root", "Same root commit" in txt, True)
    check("names what git cannot settle", "cannot settle which is canonical" in txt, True)
    for v in VERDICTS:
        check(f"does not conclude {v!r}", v in facts(txt), False)
    check("does not declare a winner", "canonical" in facts(txt), False)

    # Divergent roots is the dangerous case and must be called out as such.
    c = _os.path.join(d, "c"); _os.makedirs(c); _repo(c, 2, "gamma-")
    txt2 = reg._lineage({"alpha": a, "gamma": c}, ["alpha", "gamma"])
    check("separate histories are flagged", "root commits differ" in txt2, True)
    check("still refuses to pick a winner",
          any(v in facts(txt2) for v in VERDICTS), False)

print()

if FAILS:
    print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
    sys.exit(1)
print("all registry tests passed")
