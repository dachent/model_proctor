"""Fixture: q1_lru_cache - implement an O(1) average-time LRU cache."""
import os, sys

MODULE = '''"""LRU cache.

Implement class LRUCache with O(1) average-time operations:
- LRUCache(capacity): create a cache; capacity >= 1.
- get(key): return the cached value, or -1 if the key is absent;
  a successful get refreshes the entry's recency.
- put(key, value): insert or update an entry; put on an existing key
  updates the value and refreshes its recency.
- When the cache is full, put evicts the least-recently-used entry.
- Both get and put must run in O(1) average time.
"""


class LRUCache:
    def __init__(self, capacity):
        """Create an LRU cache with the given capacity (capacity >= 1)."""
        raise NotImplementedError("implement LRUCache.__init__")

    def get(self, key):
        """Return the value for key, or -1 if absent; refresh recency."""
        raise NotImplementedError("implement LRUCache.get")

    def put(self, key, value):
        """Insert or update key; evict the least-recently-used entry if full."""
        raise NotImplementedError("implement LRUCache.put")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cache import LRUCache

c = LRUCache(2)
c.put(1, 'a')
c.put(2, 'b')
assert c.get(1) == 'a', "get(1) should return 'a'"
assert c.get(2) == 'b', "get(2) should return 'b'"
c.put(3, 'c')  # cache is full: evicts least-recently-used key 1
assert c.get(1) == -1, "key 1 should have been evicted"
assert c.get(3) == 'c', "get(3) should return 'c'"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cache import LRUCache

# capacity = 1 behavior
c = LRUCache(1)
c.put(1, 'a')
assert c.get(1) == 'a', "capacity=1: get(1) should return 'a'"
c.put(2, 'b')
assert c.get(1) == -1, "capacity=1: put(2, 'b') must evict key 1"
assert c.get(2) == 'b', "capacity=1: get(2) should return 'b'"

# missing key returns -1
assert LRUCache(2).get(99) == -1, "missing key must return -1"

# get refreshes recency
c = LRUCache(2)
c.put(1, 'a')
c.put(2, 'b')
assert c.get(1) == 'a'  # now key 2 is the least-recently-used
c.put(3, 'c')
assert c.get(2) == -1, "get(1) refreshed recency; key 2 must be evicted"
assert c.get(1) == 'a'
assert c.get(3) == 'c'

# put on an existing key updates the value and refreshes recency
c = LRUCache(2)
c.put(1, 'a')
c.put(2, 'b')
c.put(1, 'x')  # update: key 1 becomes most recently used
c.put(3, 'c')  # must evict key 2
assert c.get(1) == 'x', "put on existing key must update the value"
assert c.get(2) == -1, "put on existing key refreshed recency; key 2 must be evicted"
assert c.get(3) == 'c'

# multi-step eviction chain
c = LRUCache(3)
c.put(1, 'a')
c.put(2, 'b')
c.put(3, 'c')
c.get(1)        # recency: 2, 3, 1
c.put(4, 'd')   # evicts key 2
assert c.get(2) == -1, "chain: key 2 must be evicted first"
c.put(5, 'e')   # evicts key 3
assert c.get(3) == -1, "chain: key 3 must be evicted second"
assert c.get(1) == 'a'
assert c.get(4) == 'd'
assert c.get(5) == 'e'
c.put(6, 'f')   # evicts key 1
assert c.get(1) == -1, "chain: key 1 must be evicted third"
assert c.get(4) == 'd'
assert c.get(5) == 'e'
assert c.get(6) == 'f'
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
