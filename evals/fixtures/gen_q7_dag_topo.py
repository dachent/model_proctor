"""Fixture: q7_dag_topo - deterministic topological sort."""
import os, sys

MODULE = '''"""Deterministic topological sort.

Implement topo_sort(nodes, edges):
- nodes is a list of names; edges is a list of (from, to) tuples.
- Return a valid topological ordering of all nodes.
- When several nodes are simultaneously available, the lexicographically
  smallest name is chosen first (fully deterministic output).
- A cycle raises ValueError.
- An edge referencing an unknown node raises ValueError.
- Empty nodes returns [].
"""


def topo_sort(nodes, edges):
    """Return a deterministic topological ordering; see module docstring."""
    raise NotImplementedError("implement topo_sort")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dag import topo_sort

assert topo_sort(["a", "b", "c"], [("a", "b"), ("b", "c")]) == ["a", "b", "c"], \\
    "linear chain must come out in order"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dag import topo_sort

# diamond with lexicographic tie-break (exact expected list)
out = topo_sort(["a", "b", "c", "d"],
                [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
assert out == ["a", "b", "c", "d"], "diamond tie-break failed: got %r" % (out,)

# a newly freed smaller node must jump ahead of an already-available larger one
out = topo_sort(["a", "b", "c"], [("b", "a")])
assert out == ["b", "a", "c"], "smallest available node must be chosen first: got %r" % (out,)

# disconnected nodes are included, in lexicographic order
assert topo_sort(["b", "a"], []) == ["a", "b"], "disconnected nodes must be included in lexicographic order"

# empty input
assert topo_sort([], []) == [], "empty nodes must return []"

# cycle raises ValueError
try:
    topo_sort(["a", "b"], [("a", "b"), ("b", "a")])
    raise AssertionError("a cycle must raise ValueError")
except ValueError:
    pass

# an edge referencing an unknown node raises ValueError
try:
    topo_sort(["a"], [("a", "zzz")])
    raise AssertionError("an unknown node must raise ValueError")
except ValueError:
    pass
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('dag.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
