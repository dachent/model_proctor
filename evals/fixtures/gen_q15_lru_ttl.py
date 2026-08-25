"""Fixture: q15_lru_ttl - LRU cache with per-entry TTL and lazy expiry."""
import os, sys

MODULE = '''"""LRU cache with per-entry TTL.

Implement class LRUCache:
- LRUCache(capacity, ttl_seconds): capacity >= 1, ttl_seconds > 0.
- get(key, now=0.0) and put(key, value, now=0.0) take an optional float
  timestamp `now` in seconds (0.0 when omitted).
- An entry put at time t is live while now < t + ttl_seconds and is
  expired when now >= t + ttl_seconds; the TTL is anchored to the LAST
  put of that key.
- get does NOT extend the TTL, but a successful get DOES refresh the
  entry's LRU recency.
- get of an expired or missing key returns -1.
- Expired entries are treated as absent for capacity purposes: they are
  removed lazily and never force an eviction.
- When a put of a new key would exceed capacity, the
  least-recently-used LIVE entry is evicted.
- put on an existing key updates the value, refreshes recency, and
  re-anchors the TTL to the new put time.
"""


class LRUCache:
    def __init__(self, capacity, ttl_seconds):
        """Create a cache; capacity >= 1, ttl_seconds > 0."""
        raise NotImplementedError("implement LRUCache.__init__")

    def get(self, key, now=0.0):
        """Return the value for a live key, else -1; refresh recency, not TTL."""
        raise NotImplementedError("implement LRUCache.get")

    def put(self, key, value, now=0.0):
        """Insert or update key; evict the LRU live entry if at capacity."""
        raise NotImplementedError("implement LRUCache.put")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cache import LRUCache

c = LRUCache(2, 10.0)
c.put(1, 'a', now=0.0)
c.put(2, 'b', now=0.0)
assert c.get(1, now=1.0) == 'a', "get(1) should return 'a'"
c.put(3, 'c', now=1.0)  # full: evicts least-recently-used live key 2
assert c.get(2, now=1.0) == -1, "key 2 should have been evicted"
assert c.get(3, now=1.0) == 'c'

c2 = LRUCache(2, 10.0)
c2.put('x', 1, now=0.0)
assert c2.get('x', now=9.5) == 1, "entry should be live before the TTL"
assert c2.get('x', now=10.0) == -1, "at exactly put_time + ttl the entry is expired"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cache import LRUCache

# expiry boundary: live strictly before, expired at exactly t + ttl
c = LRUCache(2, 5.0)
c.put('k', 'v', now=2.0)
assert c.get('k', now=6.999) == 'v'
assert c.get('k', now=7.0) == -1, "entry must expire at exactly put_time + ttl"

# get refreshes recency but NOT the TTL
c = LRUCache(2, 10.0)
c.put('a', 1, now=0.0)
assert c.get('a', now=9.0) == 1, "still live"
assert c.get('a', now=10.5) == -1, "get must not extend the TTL"

# put on an existing key re-anchors the TTL and updates the value
c = LRUCache(2, 10.0)
c.put('a', 1, now=0.0)
c.put('a', 2, now=8.0)
assert c.get('a', now=17.5) == 2, "re-put must re-anchor the TTL"
assert c.get('a', now=18.0) == -1, "re-anchored TTL boundary"

# expired entries are freed for capacity and never force eviction of live ones
c = LRUCache(2, 10.0)
c.put('x', 1, now=0.0)   # expires at 10
c.put('e', 9, now=8.0)   # expires at 18
c.get('x', now=8.5)      # x still live; recency: e, x
c.put('y', 2, now=11.0)  # x expired -> live entries are just {e}; y fits
assert c.get('e', now=11.5) == 9, "expired x must not force eviction of live e"
assert c.get('y', now=11.5) == 2

# capacity 1 with mixed expiry
c = LRUCache(1, 5.0)
c.put('a', 1, now=0.0)
c.get('a', now=4.0)      # recency refresh only; TTL NOT extended
assert c.get('a', now=5.0) == -1, "capacity-1: get must not extend TTL"
c.put('b', 2, now=5.0)   # a expired -> b fits without issue
assert c.get('b', now=9.9) == 2
assert c.get('b', now=10.0) == -1

# at-capacity eviction picks the least-recently-used LIVE entry
c = LRUCache(2, 100.0)
c.put('a', 1, now=0.0)
c.put('b', 2, now=0.0)
c.get('a', now=1.0)      # b is now least recently used
c.put('c', 3, now=2.0)
assert c.get('b', now=2.0) == -1, "b was the LRU live entry"
assert c.get('a', now=2.0) == 1
assert c.get('c', now=2.0) == 3

# default now is 0.0
c = LRUCache(1, 100.0)
c.put('k', 'v')
assert c.get('k') == 'v'
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('cache.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
