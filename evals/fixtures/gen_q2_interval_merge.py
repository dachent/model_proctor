"""Fixture: q2_interval_merge - merge closed intervals."""
import os, sys

MODULE = '''"""Merge closed intervals.

Implement merge(intervals):
- intervals is a list of [a, b] closed intervals with a <= b.
- The input may be unsorted.
- Merge intervals that overlap OR touch: [1, 2] and [2, 3] merge to [1, 3].
- Return the merged intervals as a list of [a, b] lists sorted by start.
- The input list must not be mutated.
- Empty input returns [].
"""


def merge(intervals):
    """Merge a list of [a, b] closed intervals; see module docstring."""
    raise NotImplementedError("implement merge")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from intervals import merge

assert merge([[1, 3], [2, 5]]) == [[1, 5]], "overlapping intervals must merge"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from intervals import merge

# touching intervals merge
assert merge([[1, 2], [2, 3]]) == [[1, 3]], "touching intervals [1,2] and [2,3] must merge to [1,3]"

# unsorted input, result sorted by start
assert merge([[5, 7], [1, 2], [2, 4]]) == [[1, 4], [5, 7]], "unsorted input must be handled; result sorted by start"

# negative endpoints
assert merge([[-5, -2], [-3, -1]]) == [[-5, -1]], "negative endpoints must merge"

# nested intervals
assert merge([[1, 10], [2, 3], [4, 5]]) == [[1, 10]], "nested intervals must merge into the outer one"

# input list must not be mutated
inp = [[3, 4], [1, 2]]
out = merge(inp)
assert inp == [[3, 4], [1, 2]], "the input list must not be mutated"
assert out == [[1, 2], [3, 4]]

# empty input
assert merge([]) == [], "empty input must return []"
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('intervals.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
