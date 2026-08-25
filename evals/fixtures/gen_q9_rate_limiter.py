"""Fixture: q9_rate_limiter - sliding-window rate limiter."""
import os, sys

MODULE = '''"""Sliding-window rate limiter.

Implement class RateLimiter:
- RateLimiter(limit, window_seconds): allow at most `limit` calls per
  sliding window of `window_seconds` seconds.
- allow(t): t is a float timestamp in seconds. Return True and record the
  call when fewer than `limit` calls were allowed within the sliding
  interval (t - window_seconds, t]; a call exactly at t - window_seconds
  counts as expired. Otherwise return False.
- Only allowed calls are recorded.
- Calls may arrive with any t (not necessarily non-decreasing).
"""


class RateLimiter:
    def __init__(self, limit, window_seconds):
        """Create a limiter allowing `limit` calls per `window_seconds`."""
        raise NotImplementedError("implement RateLimiter.__init__")

    def allow(self, t):
        """Return True and record the call if within quota; see module docstring."""
        raise NotImplementedError("implement RateLimiter.allow")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from limiter import RateLimiter

rl = RateLimiter(2, 10.0)
assert rl.allow(1.0), "first call must be allowed"
assert rl.allow(2.0), "second call must be allowed"
assert not rl.allow(3.0), "third call within the window must be rejected"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from limiter import RateLimiter

# boundary: a call exactly at t - window counts as expired
rl = RateLimiter(1, 10.0)
assert rl.allow(0.0) is True
assert rl.allow(10.0) is True, "the call at t=0 is exactly t-window=0 and counts as expired"
assert rl.allow(10.5) is False, "the call at t=10 still lies in (0.5, 10.5]"

# sliding window: allowed again just after the oldest call expires
rl = RateLimiter(2, 10.0)
assert rl.allow(0.0) is True
assert rl.allow(5.0) is True
assert rl.allow(9.0) is False
assert rl.allow(11.0) is True, "oldest call expired at t=11; quota must be available"

# rejected calls do not consume quota
rl = RateLimiter(1, 10.0)
assert rl.allow(0.0) is True
assert rl.allow(5.0) is False
assert rl.allow(10.0) is True, "the rejected call at t=5 must not consume quota"

# out-of-order timestamps follow the (t - window, t] rule
rl = RateLimiter(2, 10.0)
assert rl.allow(10.0) is True
assert rl.allow(5.0) is True
assert rl.allow(7.0) is True, "only the t=5 call lies in (-3, 7]; the t=10 call is outside it"
assert rl.allow(8.0) is False, "the t=5 and t=7 calls both lie in (-2, 8]"
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('limiter.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
