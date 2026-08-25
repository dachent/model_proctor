"""Fixture: q14_diff_apply - simplified unified diff with original-text coordinates."""
import os, sys

MODULE = r'''"""Simplified unified diff application.

Implement apply_patch(text, patch):
- text is split into lines on '\n' and the result is the lines joined
  on '\n'.
- patch consists of one or more hunks. A hunk starts with a header line
  '@@ <start> <count> @@' where start is a 1-based line number in the
  ORIGINAL text and count is the number of original lines the hunk
  covers.
- Within a hunk body (all lines until the next header or end of patch):
  lines starting with '-' delete an original line, lines starting with
  '+' insert a new line, and lines starting with ' ' (a single space)
  are context lines. Any other body line is malformed and raises
  ValueError.
- The context and deletion lines of a hunk, in order, must exactly equal
  the count original lines starting at start (context lines must match
  the original exactly); any mismatch or a wrong number of consumed
  lines raises ValueError.
- A hunk replaces the slice of original lines [start, start + count - 1]
  with its kept context lines and inserted '+' lines, in order. With
  count 0 the slice is empty and the '+' lines are inserted immediately
  before original line start (start may be one past the last line to
  append at the end).
- Hunks are given in ascending start order and never overlap; line
  numbers always refer to the ORIGINAL text, so earlier hunks must not
  shift where later hunks apply.
- apply_patch returns the patched string.
"""


def apply_patch(text, patch):
    """Apply the simplified unified diff patch to text and return the result."""
    raise NotImplementedError("implement apply_patch")
'''

CHECK = r'''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diff import apply_patch

text = "alpha\nbeta\ngamma"
patch = "@@ 2 1 @@\n-beta\n+BETA"
assert apply_patch(text, patch) == "alpha\nBETA\ngamma"

patch2 = "@@ 1 3 @@\n alpha\n-beta\n+B\n+extra\n gamma"
assert apply_patch(text, patch2) == "alpha\nB\nextra\ngamma"
print("PASS")
'''

HIDDEN = r'''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diff import apply_patch

text = "l1\nl2\nl3\nl4\nl5\nl6"

# multiple hunks: line numbers always refer to the ORIGINAL text
patch = "@@ 2 1 @@\n-l2\n+L2\n+L2b\n@@ 5 1 @@\n-l5\n+L5"
assert apply_patch(text, patch) == "l1\nL2\nL2b\nl3\nl4\nL5\nl6", \
    "hunk 2 must apply to original line 5 despite hunk 1 inserting a line"

# a deletion before a later hunk must not shift it either
patch = "@@ 1 2 @@\n-l1\n-l2\n@@ 3 1 @@\n l3\n+after3"
assert apply_patch(text, patch) == "l3\nafter3\nl4\nl5\nl6"

# context mismatch raises ValueError
try:
    apply_patch(text, "@@ 2 1 @@\n WRONG")
    raise SystemExit("context mismatch must raise ValueError")
except ValueError:
    pass

# malformed headers raise ValueError
for bad in ["@@ two 1 @@\n a", "nonsense", "@@ 2 @@\n a"]:
    try:
        apply_patch(text, bad)
        raise SystemExit("expected ValueError for %r" % bad)
    except ValueError:
        pass

# body line without a valid prefix raises ValueError
try:
    apply_patch(text, "@@ 1 1 @@\nl1")
    raise SystemExit("unprefixed body line must raise ValueError")
except ValueError:
    pass

# wrong number of consumed lines raises ValueError
try:
    apply_patch(text, "@@ 1 2 @@\n l1")
    raise SystemExit("consumed-lines != count must raise ValueError")
except ValueError:
    pass

# insertion-only hunk (count 0)
assert apply_patch(text, "@@ 3 0 @@\n+new") == "l1\nl2\nnew\nl3\nl4\nl5\nl6"
assert apply_patch("a\nb", "@@ 3 0 @@\n+c") == "a\nb\nc", \
    "start one past the last line appends at the end"

# deletion-only hunk
assert apply_patch(text, "@@ 3 2 @@\n-l3\n-l4") == "l1\nl2\nl5\nl6"

# exact full output equality on a combined patch
patch = "@@ 1 1 @@\n l1\n@@ 2 2 @@\n-l2\n-l3\n+X\n@@ 6 1 @@\n-l6\n+l6!"
assert apply_patch(text, patch) == "l1\nX\nl4\nl5\nl6!"
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('diff.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
