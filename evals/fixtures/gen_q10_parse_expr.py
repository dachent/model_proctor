"""Fixture: q10_parse_expr - arithmetic expression evaluator."""
import os, sys

MODULE = '''"""Arithmetic expression evaluator.

Implement evaluate(expr):
- expr is a string with + - * / (standard precedence), parentheses,
  unary minus, and arbitrary whitespace.
- Return the result as a float.
- Division by zero raises ZeroDivisionError.
- A malformed expression raises ValueError.
"""


def evaluate(expr):
    """Evaluate an arithmetic expression string; see module docstring."""
    raise NotImplementedError("implement evaluate")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from expr import evaluate

assert evaluate("2 + 3 * 4") == 14.0, "got %r" % (evaluate("2 + 3 * 4"),)
print("PASS")
'''

HIDDEN = r'''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from expr import evaluate

# unary minus
assert evaluate("-3 + 1") == -2.0, "leading unary minus: got %r" % (evaluate("-3 + 1"),)
assert evaluate("2 * -3") == -6.0, "unary minus after an operator: got %r" % (evaluate("2 * -3"),)
assert evaluate("--5") == 5.0, "double unary minus: got %r" % (evaluate("--5"),)

# parentheses and precedence
assert evaluate("(2 + 3) * (4 - 1)") == 15.0, "grouped parentheses"
assert evaluate("((8))") == 8.0, "nested parentheses"
assert evaluate("10 - 2 * 3") == 4.0, "standard precedence"
assert evaluate("10 / 4") == 2.5, "division must return a float"

# arbitrary whitespace
assert evaluate("   6\t *\t2  ") == 12.0, "arbitrary whitespace must be ignored"

# result type
assert isinstance(evaluate("7"), float), "the result must be a float"

# division by zero
try:
    evaluate("1/0")
    raise AssertionError("division by zero must raise ZeroDivisionError")
except ZeroDivisionError:
    pass

# malformed expressions
for bad in ("1 +", "(2"):
    try:
        evaluate(bad)
        raise AssertionError("malformed expression %r must raise ValueError" % bad)
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
    w('expr.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
