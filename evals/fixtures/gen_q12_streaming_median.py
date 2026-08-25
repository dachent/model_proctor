"""Fixture: q12_streaming_median - median with duplicates and removal."""
import os, sys

MODULE = '''"""Streaming median with removal.

Implement class MedianTracker:
- MedianTracker(): create an empty tracker.
- add(x): add one numeric value; duplicate values are supported.
- remove(x): remove ONE instance of x; raises ValueError if x is not
  present.
- median(): return the current median; when the count of values is even,
  return the LOWER of the two middle values; raises ValueError when the
  tracker is empty.
- add, remove and median may be interleaved in any order.
"""


class MedianTracker:
    def __init__(self):
        """Create an empty tracker."""
        raise NotImplementedError("implement MedianTracker.__init__")

    def add(self, x):
        """Add one value; duplicates are supported."""
        raise NotImplementedError("implement MedianTracker.add")

    def remove(self, x):
        """Remove ONE instance of x; raise ValueError if absent."""
        raise NotImplementedError("implement MedianTracker.remove")

    def median(self):
        """Return the median; even count -> lower middle; empty -> ValueError."""
        raise NotImplementedError("implement MedianTracker.median")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from median import MedianTracker

t = MedianTracker()
t.add(10)
t.add(4)
t.add(7)
assert t.median() == 7, "median of [4, 7, 10] should be 7"
t.remove(4)
t.add(1)
assert t.median() == 7, "median of [1, 7, 10] should be 7"
t.add(3)
t.add(20)
assert t.median() == 7, "median of [1, 3, 7, 10, 20] should be 7"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from median import MedianTracker

# duplicates are supported
t = MedianTracker()
t.add(2)
t.add(2)
t.add(2)
assert t.median() == 2
t.remove(2)  # removes ONE instance only
assert t.median() == 2
t.remove(2)
assert t.median() == 2
t.remove(2)

# empty after removals raises ValueError
try:
    t.median()
    raise SystemExit("median of an empty tracker must raise ValueError")
except ValueError:
    pass

# remove of an absent value raises ValueError
t = MedianTracker()
t.add(1)
try:
    t.remove(2)
    raise SystemExit("remove of absent value must raise ValueError")
except ValueError:
    pass
t.remove(1)
try:
    t.remove(1)
    raise SystemExit("remove past the last instance must raise ValueError")
except ValueError:
    pass

# even count -> LOWER of the two middle values
t = MedianTracker()
t.add(10)
t.add(20)
assert t.median() == 10, "even count must return the lower middle value, got %r" % t.median()
t.add(30)
t.add(40)
assert t.median() == 20, "median of [10, 20, 30, 40] must be 20, got %r" % t.median()
t.add(5)
assert t.median() == 20, "median of [5, 10, 20, 30, 40] must be 20"

# deterministic 500-op interleaved sequence cross-checked against an oracle
import random
rng = random.Random(20260825)
t = MedianTracker()
oracle = []
for i in range(500):
    if oracle and rng.random() < 0.4:
        v = oracle[rng.randrange(len(oracle))]
        t.remove(v)
        oracle.remove(v)
    else:
        v = rng.randrange(50)
        t.add(v)
        oracle.append(v)
    s = sorted(oracle)
    if not s:
        try:
            t.median()
            raise SystemExit("median of empty tracker must raise ValueError")
        except ValueError:
            pass
    else:
        want = s[(len(s) - 1) // 2]
        got = t.median()
        assert got == want, "op %d: median %r != oracle %r" % (i, got, want)
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('median.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
