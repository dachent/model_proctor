"""Fixture: q11_version_resolver - version constraint resolution with ~= and numeric compare."""
import os, sys

MODULE = '''"""Version resolver.

Implement resolve(requirements, available):
- requirements: dict mapping package name -> constraint string.
- available: dict mapping package name -> list of version strings.
- A constraint string is a comma-separated list of clauses; each clause is
  one of the operators ==, >=, < or ~= followed by a version.
- ~= is the compatible-release operator: ~=X.Y means >=X.Y, <(X+1).0 and
  ~=X.Y.Z means >=X.Y.Z, <X.(Y+1).0.
- ALL clauses of a constraint must hold for a version to satisfy it.
- Versions are dot-separated non-negative integers and are compared
  numerically component by component (so 1.10 > 1.9); when two versions
  have different numbers of components, the shorter one is padded with
  zeros for the comparison (so 1.2 == 1.2.0).
- Resolution chooses the HIGHEST available version of each package that
  satisfies its constraint.
- If any package has no satisfying version (including a required package
  that is absent from available), raise ValueError naming the package.
- The result is a dict mapping every required package to its chosen
  version string; every required package must appear in the result.
"""


def resolve(requirements, available):
    """Resolve every required package to its highest satisfying version."""
    raise NotImplementedError("implement resolve")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolver import resolve

avail = {
    "flask": ["1.0", "2.0", "2.1"],
    "requests": ["2.20.0", "2.25.1", "2.26.0"],
}
res = resolve({"flask": ">=2.0", "requests": "==2.25.1"}, avail)
assert res == {"flask": "2.1", "requests": "2.25.1"}, res
res = resolve({"flask": ">=1.0, <2.1"}, avail)
assert res == {"flask": "2.0"}, res
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolver import resolve

# numeric, not lexicographic comparison: 1.10 > 1.9
res = resolve({"pkg": ">=1.0"}, {"pkg": ["1.9", "1.10"]})
assert res["pkg"] == "1.10", "1.10 must compare greater than 1.9: %s" % res

# ~= two-component form: ~=1.4 means >=1.4, <2.0
res = resolve({"pkg": "~=1.4"}, {"pkg": ["1.3.9", "1.4", "1.4.5", "1.9.9", "2.0"]})
assert res["pkg"] == "1.9.9", "~=1.4 must allow 1.9.9 but exclude 2.0: %s" % res

# ~= three-component form: ~=1.4.2 means >=1.4.2, <1.5.0
res = resolve({"pkg": "~=1.4.2"}, {"pkg": ["1.4.1", "1.4.2", "1.4.9", "1.5.0"]})
assert res["pkg"] == "1.4.9", "~=1.4.2 must allow 1.4.9 but exclude 1.5.0: %s" % res

# combined clauses: all must hold
res = resolve({"pkg": ">=1.2, <1.5, ~=1.0"}, {"pkg": ["1.1", "1.3", "1.6"]})
assert res["pkg"] == "1.3", "combined clauses: %s" % res

# zero padding: 1.2 == 1.2.0
res = resolve({"pkg": "==1.2"}, {"pkg": ["1.2.0"]})
assert res["pkg"] == "1.2.0", "1.2 must equal 1.2.0 under zero padding"

# highest-version selection among several satisfying versions
res = resolve({"pkg": ">=1.0"}, {"pkg": ["1.0", "1.5", "1.3"]})
assert res["pkg"] == "1.5", "must choose the highest satisfying version"

# conflict raises ValueError naming the package
try:
    resolve({"pkg": ">=2.0"}, {"pkg": ["1.0"]})
    raise SystemExit("expected ValueError for unsatisfiable constraint")
except ValueError as e:
    assert "pkg" in str(e), "ValueError must name the package: %s" % e

# required package absent from available raises ValueError naming it
try:
    resolve({"ghost": ">=1.0"}, {})
    raise SystemExit("expected ValueError for missing package")
except ValueError as e:
    assert "ghost" in str(e), "ValueError must name the missing package: %s" % e

# every required package appears in the result
res = resolve({"a": ">=1.0", "b": "<2.0"}, {"a": ["1.0", "2.0"], "b": ["1.5", "2.0"]})
assert res == {"a": "2.0", "b": "1.5"}, res
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('resolver.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
