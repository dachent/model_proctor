"""Fixture: q17_json_diff - structural JSON diff with element-wise list semantics."""
import os, sys

MODULE = '''"""Structural JSON diff.

Implement diff(a, b) and apply_diff(a, ops) where a and b are JSON-like
structures (dicts, lists, strings, numbers, booleans, None).

diff(a, b) returns a list of ops. Each op is a dict:
- {"op": "set", "path": [...], "value": v}
- {"op": "delete", "path": [...]}
A path is a list of dict keys (strings) and list indices (ints).

Rules:
- Dicts recurse: keys in both are recursed into; keys only in b produce
  set ops; keys only in a produce delete ops.
- Lists are compared ELEMENT-WISE by index: index i of a is diffed
  against index i of b. If b has extra elements they are added with set
  ops whose final path component is an index equal to the list length at
  apply time (a set at index == len(list) APPENDS). If a has extra
  elements they are removed with delete ops emitted from the HIGHEST
  index downward.
- Scalars (and any value whose type differs from its counterpart, and
  any container compared against a different kind of container) produce
  a single set op at that path when the values are not equal.
- Ops are emitted so that applying them in order transforms a into b;
  dict keys are processed in sorted order.
- diff returns [] when a == b.

apply_diff(a, ops) returns the patched structure. It must NOT mutate a
(the input and all its nested parts must be unchanged). A delete op
removes the dict key or list element at its path. Applying ops in order
must satisfy apply_diff(a, diff(a, b)) == b.
"""


def diff(a, b):
    """Return the op list that transforms a into b."""
    raise NotImplementedError("implement diff")


def apply_diff(a, ops):
    """Return a patched COPY of a; never mutate the input."""
    raise NotImplementedError("implement apply_diff")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jdiff import diff, apply_diff

a = {"name": "app", "tags": ["x", "y"], "cfg": {"debug": True}}
b = {"name": "app", "tags": ["x", "z"], "cfg": {"debug": False}}
ops = diff(a, b)
assert apply_diff(a, ops) == b, apply_diff(a, ops)
assert diff(a, a) == [], "diff of identical structures must be empty"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jdiff import diff, apply_diff

# nested dict recursion: a deep change is a deep path, not a top-level set
a = {"cfg": {"db": {"host": "a", "port": 1}, "debug": True}}
b = {"cfg": {"db": {"host": "b", "port": 1}, "debug": True}}
ops = diff(a, b)
assert ops == [{"op": "set", "path": ["cfg", "db", "host"], "value": "b"}], ops
assert apply_diff(a, ops) == b

# lists compared element-wise: only the differing index is set
a = {"x": [1, 2, 3]}
b = {"x": [1, 2, 4]}
ops = diff(a, b)
assert ops == [{"op": "set", "path": ["x", 2], "value": 4}], ops
assert apply_diff(a, ops) == b

# list growth: set ops append at index == len(list)
a = [1, 2]
b = [1, 2, 3, 4]
ops = diff(a, b)
assert ops == [{"op": "set", "path": [2], "value": 3},
               {"op": "set", "path": [3], "value": 4}], ops
assert apply_diff(a, ops) == b

# list shrink: delete ops emitted from the highest index downward
a = [1, 2, 3, 4]
b = [1, 2]
ops = diff(a, b)
assert ops == [{"op": "delete", "path": [3]},
               {"op": "delete", "path": [2]}], ops
assert apply_diff(a, ops) == b

# delete ops for keys present in a but removed from b
a = {"keep": 1, "gone": 2}
b = {"keep": 1}
ops = diff(a, b)
assert ops == [{"op": "delete", "path": ["gone"]}], ops
assert apply_diff(a, ops) == b

# apply_diff must not mutate the input
a = {"x": [1, 2], "y": {"z": 1}}
snapshot = {"x": [1, 2], "y": {"z": 1}}
b = {"x": [1, 3, 4], "y": {"z": 2}}
ops = diff(a, b)
out = apply_diff(a, ops)
assert a == snapshot, "diff/apply_diff must not mutate the input"
assert out == b

# a value whose type changes produces a whole-value set at that path
a = {"v": {"nested": 1}}
b = {"v": 5}
assert diff(a, b) == [{"op": "set", "path": ["v"], "value": 5}]

# round-trip equality on a nested mixed fixture
a = {"users": [{"name": "amy", "roles": ["admin"]}, {"name": "bob", "roles": []}],
     "meta": {"count": 2, "tags": ["a", "b", "c"]}, "active": True}
b = {"users": [{"name": "amy", "roles": ["admin", "ops"]}],
     "meta": {"count": 3}, "active": True, "extra": None}
assert apply_diff(a, diff(a, b)) == b
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('jdiff.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
