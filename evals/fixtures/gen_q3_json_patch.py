"""Fixture: q3_json_patch - apply a restricted JSON Patch subset."""
import os, sys

MODULE = '''"""Restricted JSON Patch.

Implement apply_patch(doc, ops):
- ops is a list of operation dicts; each op dict has keys:
  - op: one of "add", "remove", "replace",
  - path: an RFC 6901 JSON Pointer,
  - value: required for "add" and "replace".
- Pointer segments unescape "~0" to "~" and "~1" to "/".
- For arrays, the index "-" means append (valid for "add" only).
- "remove" of a non-existent path raises KeyError.
- apply_patch returns a NEW document and never mutates the input.
"""


def apply_patch(doc, ops):
    """Apply the restricted JSON Patch ops to doc; see module docstring."""
    raise NotImplementedError("implement apply_patch")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patch import apply_patch

doc = {"a": 1}
out = apply_patch(doc, [{"op": "replace", "path": "/a", "value": 2},
                        {"op": "add", "path": "/b", "value": 3}])
assert out == {"a": 2, "b": 3}, "replace then add on a flat dict: got %r" % (out,)
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patch import apply_patch

# ~0 and ~1 unescaping
out = apply_patch({"a~b": 1, "c/d": 2},
                  [{"op": "replace", "path": "/a~0b", "value": 10},
                   {"op": "replace", "path": "/c~1d", "value": 20}])
assert out == {"a~b": 10, "c/d": 20}, "pointer segments must unescape ~0 to ~ and ~1 to /: got %r" % (out,)

# "-" appends to an array
out = apply_patch({"l": [1, 2]}, [{"op": "add", "path": "/l/-", "value": 3}])
assert out == {"l": [1, 2, 3]}, "index - must append for add: got %r" % (out,)

# nested paths
out = apply_patch({"a": {"b": {"c": 1}}}, [{"op": "replace", "path": "/a/b/c", "value": 9}])
assert out == {"a": {"b": {"c": 9}}}, "nested paths must be traversed: got %r" % (out,)

# replace at an array index
out = apply_patch({"l": [1, 2, 3]}, [{"op": "replace", "path": "/l/1", "value": 9}])
assert out == {"l": [1, 9, 3]}, "replace at an array index must replace that element: got %r" % (out,)

# remove of an existing key
out = apply_patch({"a": 1, "b": 2}, [{"op": "remove", "path": "/a"}])
assert out == {"b": 2}, "remove must delete the key: got %r" % (out,)

# remove of a non-existent path raises KeyError
try:
    apply_patch({"a": 1}, [{"op": "remove", "path": "/missing"}])
    raise AssertionError("remove of a non-existent path must raise KeyError")
except KeyError:
    pass

# the input document is never mutated
doc = {"a": {"x": 1}, "l": [1]}
apply_patch(doc, [{"op": "replace", "path": "/a/x", "value": 2},
                  {"op": "add", "path": "/l/-", "value": 5}])
assert doc == {"a": {"x": 1}, "l": [1]}, "the input document must not be mutated"
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('patch.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
