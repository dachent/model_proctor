"""Fixture: q8_text_justify - greedy text justification."""
import os, sys

MODULE = '''"""Greedy text justification.

Implement justify(words, width) returning a list of strings:
- Pack as many words per line as possible, greedily.
- For non-last lines with more than one word, distribute spaces so gaps
  differ by at most one space, and left gaps receive the extra space.
- Every returned line is exactly `width` characters long.
- A line containing a single word is left-justified (padded on the right).
- The last line is left-justified with single spaces between words.
- A word longer than width raises ValueError.
"""


def justify(words, width):
    """Justify words into lines of exactly width chars; see module docstring."""
    raise NotImplementedError("implement justify")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from justify import justify

assert justify(["a", "b", "c"], 5) == ["a b c"], "got %r" % (justify(["a", "b", "c"], 5),)
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from justify import justify

# multi-line output with uneven gaps: the extra space goes to left gaps
out = justify(["This", "is", "an", "example", "of", "text", "justification."], 16)
assert out == ["This    is    an",
               "example  of text",
               "justification.  "], "got %r" % (out,)
assert all(len(line) == 16 for line in out), "every line must be exactly width characters"

# single-word interior line is left-justified; a word exactly width is fine
out = justify(["aa", "bbbbbbbb", "cc"], 8)
assert out == ["aa      ", "bbbbbbbb", "cc      "], "got %r" % (out,)
assert all(len(line) == 8 for line in out), "every line must be exactly width characters"

# the last line is left-justified with single spaces between words
out = justify(["word", "x", "yy"], 8)
assert out == ["word   x", "yy      "], "got %r" % (out,)
assert all(len(line) == 8 for line in out), "every line must be exactly width characters"

# a word longer than width raises ValueError
try:
    justify(["toolongword"], 4)
    raise AssertionError("a word longer than width must raise ValueError")
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
    w('justify.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
