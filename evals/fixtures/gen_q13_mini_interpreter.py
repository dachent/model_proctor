"""Fixture: q13_mini_interpreter - tiny language with truncating integer division."""
import os, sys

MODULE = '''"""Mini interpreter.

Implement run(program) where program is a string of lines:
- Each line is either an assignment `<name> = <expr>` or a print
  statement `print <expr>` (a line whose first token is `print`).
- Blank lines and lines whose first non-space character is # are ignored.
- Expressions support: integer literals, variable names (matching
  [A-Za-z_][A-Za-z0-9_]*), the binary operators + - * / with standard
  precedence (* and / bind tighter than + and -), parentheses, and
  unary minus.
- / is integer division truncated toward zero: -7 / 2 == -3 and
  7 / -2 == -3.
- Using an undefined variable raises NameError.
- Division by zero raises ZeroDivisionError.
- A line that is neither a valid assignment nor a valid print statement,
  or an expression with malformed syntax, raises ValueError.
- run returns the list of printed values as strings, in the order
  printed.
"""


def run(program):
    """Execute program and return the list of printed values as strings."""
    raise NotImplementedError("implement run")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from interp import run

out = run("x = 1 + 2\\nprint x\\ny = x * 4\\nprint y\\nprint (x + y) / 5")
assert out == ["3", "12", "3"], out
out = run("total = 2 * 10\\nprint total")
assert out == ["20"], out
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from interp import run

# variable reuse / redefinition
out = run("x = 1\\nx = 2\\nprint x")
assert out == ["2"], out

# unary minus
out = run("x = -3\\nprint x\\nprint -x\\nprint -(2 + 3)\\nprint 10 + -4")
assert out == ["-3", "3", "-5", "6"], out

# integer division truncated toward zero
out = run("print -7 / 2\\nprint 7 / -2\\nprint -7 / -2\\nprint 7 / 2")
assert out == ["-3", "-3", "3", "3"], "division must truncate toward zero: %s" % out

# comments and blank lines are ignored
out = run("# comment\\n\\nx = 5\\n   # indented comment\\nprint x\\n\\n")
assert out == ["5"], out

# undefined variable raises NameError
try:
    run("print z")
    raise SystemExit("undefined variable must raise NameError")
except NameError:
    pass

# division by zero raises ZeroDivisionError
try:
    run("print 1 / 0")
    raise SystemExit("division by zero must raise ZeroDivisionError")
except ZeroDivisionError:
    pass

# malformed lines and expressions raise ValueError
for bad in ["x =", "= 3", "print", "x = 1 +", "hello world", "x = 2 * (3 +"]:
    try:
        run(bad)
        raise SystemExit("expected ValueError for %r" % bad)
    except ValueError:
        pass

# print order is preserved
out = run("a = 1\\nprint a\\nb = 2\\nprint b\\nprint a + b")
assert out == ["1", "2", "3"], out

# precedence, parentheses, left associativity
out = run("print 2 + 3 * 4\\nprint (2 + 3) * 4\\nprint 10 - 4 - 3")
assert out == ["14", "20", "3"], out

# printed values are strings
out = run("print 42")
assert out == ["42"] and isinstance(out[0], str), out
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('interp.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
