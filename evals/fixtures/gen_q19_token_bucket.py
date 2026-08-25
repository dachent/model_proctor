"""Fixture: q19_token_bucket - continuous-refill token bucket with fractional costs."""
import os, sys

MODULE = '''"""Token bucket rate limiter.

Implement class TokenBucket:
- TokenBucket(capacity, refill_per_second): capacity > 0,
  refill_per_second >= 0.
- The bucket starts FULL: it holds `capacity` tokens at time 0.
- Tokens refill continuously at refill_per_second tokens per second, up
  to but never above capacity.
- allow(t, cost=1.0): t is a float time in seconds and calls arrive
  with non-decreasing t; cost may be fractional. Whether or not the
  call is allowed, the stored level is first refilled for the elapsed
  time since the previous call:
  level = min(capacity, level + (t - t_prev) * refill_per_second).
  Then, if level >= cost, allow returns True and deducts cost;
  otherwise it returns False and deducts nothing.
"""


class TokenBucket:
    def __init__(self, capacity, refill_per_second):
        """Create a full bucket; capacity > 0, refill_per_second >= 0."""
        raise NotImplementedError("implement TokenBucket.__init__")

    def allow(self, t, cost=1.0):
        """Refill up to time t, then deduct cost iff the level covers it."""
        raise NotImplementedError("implement TokenBucket.allow")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bucket import TokenBucket

b = TokenBucket(2.0, 0.5)
assert b.allow(0.0) is True, "bucket starts full"
assert b.allow(0.0) is True
assert b.allow(0.0) is False, "bucket is drained"
assert b.allow(2.0) is True, "2 seconds at 0.5/s refills one token"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bucket import TokenBucket

# fractional refill arithmetic (all values are exact binary fractions)
b = TokenBucket(1.0, 0.5)
assert b.allow(0.0, 1.0) is True      # level 1 -> 0
assert b.allow(1.0, 0.6) is False, "level 0.5 < 0.6 must be rejected"
assert b.allow(1.5, 0.75) is True, "0.5 + 0.25 = 0.75 must cover cost 0.75"
assert b.allow(1.5, 0.1) is False, "level is now 0"

# burst up to capacity, then rejection
b = TokenBucket(3.0, 1.0)
assert [b.allow(0.0) for _ in range(3)] == [True, True, True]
assert b.allow(0.0) is False, "burst beyond capacity must be rejected"

# level is capped at capacity: no over-fill
b = TokenBucket(2.0, 1.0)
assert b.allow(0.0) is True           # level 1
assert b.allow(100.0) is True, "refill must cap at capacity"
assert b.allow(100.0) is True
assert b.allow(100.0) is False, "over-fill would have allowed a third call"

# a cost larger than capacity is always False
b = TokenBucket(2.0, 1.0)
assert b.allow(0.0, 2.5) is False
assert b.allow(1000.0, 2.5) is False, "cost > capacity can never be allowed"

# a rejected call deducts nothing (the refill still accumulates)
b = TokenBucket(2.0, 0.25)
assert b.allow(0.0, 1.5) is True      # level 0.5
assert b.allow(0.0, 1.0) is False, "0.5 < 1.0: rejected"
assert b.allow(0.0, 0.5) is True, "rejection must not have deducted anything"
assert b.allow(4.0, 1.0) is True, "4 seconds at 0.25/s refills exactly 1.0"
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('bucket.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
