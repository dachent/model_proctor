"""Fixture: q20_csv_parse - RFC-4180-ish CSV parser with quoting rules."""
import os, sys

MODULE = r'''"""RFC-4180-ish CSV parsing.

Implement parse(text) returning a list of rows, each row a list of
field strings:
- Fields are separated by commas and rows by newlines.
- CRLF line endings are normalized: every '\r\n' is treated as '\n'
  before parsing (a lone '\r' elsewhere is an ordinary character).
- A trailing newline does NOT create an empty final row: "a\n" and "a"
  both parse to [["a"]]. A blank line in the middle is a row containing
  one empty field.
- Empty input returns [].
- A field wrapped in double quotes may contain commas, newlines, and
  literal double quotes escaped as "" (two double-quote characters).
- A quoted field's closing quote must be immediately followed by a
  comma, a newline, or end of input; anything else raises ValueError.
- A quoted field that is never closed raises ValueError.
- Unquoted fields are taken verbatim: no trimming of whitespace, and a
  double quote inside an unquoted field is an ordinary character.
"""


def parse(text):
    """Parse CSV text into a list of rows (lists of field strings)."""
    raise NotImplementedError("implement parse")
'''

CHECK = r'''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csvparse import parse

assert parse("a,b,c") == [["a", "b", "c"]]
assert parse("a,b\nc,d") == [["a", "b"], ["c", "d"]]
assert parse("") == []
print("PASS")
'''

HIDDEN = r'''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csvparse import parse

# quoted field containing a comma
assert parse('"a,b",c') == [["a,b", "c"]]

# quoted field containing an embedded newline
assert parse('"line1\nline2",b') == [["line1\nline2", "b"]]

# escaped double quote inside a quoted field
assert parse('"say ""hi""",x') == [['say "hi"', "x"]]

# CRLF handling: \r\n is the row terminator, also inside quoted fields
assert parse("a,b\r\nc,d\r\n") == [["a", "b"], ["c", "d"]]
assert parse('"p\r\nq",z') == [["p\nq", "z"]]

# a trailing newline does NOT create an empty final row
assert parse("a\n") == [["a"]]
assert parse("a") == [["a"]]
assert parse("a,b\n") == [["a", "b"]]

# empty input returns []
assert parse("") == []

# unquoted fields are verbatim: no trimming
assert parse("  a  , b ") == [["  a  ", " b "]]

# a double quote inside an unquoted field is an ordinary character
assert parse('a"b,c') == [['a"b', "c"]]

# a blank line in the middle is a row containing one empty field
assert parse("a\n\nb") == [["a"], [""], ["b"]]

# malformed: closing quote followed by junk
try:
    parse('"abc"def')
    raise SystemExit("junk after closing quote must raise ValueError")
except ValueError:
    pass

# malformed: unterminated quoted field
try:
    parse('"abc')
    raise SystemExit("unterminated quoted field must raise ValueError")
except ValueError:
    pass

# malformed: closing quote followed by a space
try:
    parse('"abc" ,x')
    raise SystemExit("space after closing quote must raise ValueError")
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
    w('csvparse.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
