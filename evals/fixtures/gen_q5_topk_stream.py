"""Fixture: q5_topk_stream - streaming top-k tracker."""
import os, sys

MODULE = '''"""Streaming top-k.

Implement class TopK:
- TopK(k): track the k largest values added so far; k may be 0, in which
  case result() always returns [].
- add(x): add a value.
- result(): return the k largest values added so far as a list sorted
  descending; equal values are ordered by insertion (earlier first).
- result() may be called any number of times interleaved with add() and
  must reflect all adds so far.
"""


class TopK:
    def __init__(self, k):
        """Create a tracker for the k largest values (k may be 0)."""
        raise NotImplementedError("implement TopK.__init__")

    def add(self, x):
        """Add a value to the stream."""
        raise NotImplementedError("implement TopK.add")

    def result(self):
        """Return the k largest values so far, descending; see module docstring."""
        raise NotImplementedError("implement TopK.result")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from topk import TopK

t = TopK(3)
for x in [4, 1, 7, 3, 9]:
    t.add(x)
assert t.result() == [9, 7, 4], "result must be the 3 largest values, descending: got %r" % (t.result(),)
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from topk import TopK

# k = 0
t = TopK(0)
t.add(5)
assert t.result() == [], "k=0: result() must always return []"

# duplicate values are all kept
t = TopK(2)
t.add(5)
t.add(5)
assert t.result() == [5, 5], "equal values must both be kept: got %r" % (t.result(),)
t.add(5)
assert t.result() == [5, 5], "duplicates beyond k must be dropped: got %r" % (t.result(),)

# interleaved add/result calls
t = TopK(2)
t.add(1)
assert t.result() == [1]
t.add(3)
assert t.result() == [3, 1]
t.add(2)
assert t.result() == [3, 2], "result must reflect all adds so far: got %r" % (t.result(),)
assert t.result() == [3, 2], "repeated result calls must be consistent"
t.add(10)
assert t.result() == [10, 3]

# k larger than the number of added items
t = TopK(5)
t.add(2)
t.add(1)
assert t.result() == [2, 1], "fewer items than k: return all of them, descending"
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('topk.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
