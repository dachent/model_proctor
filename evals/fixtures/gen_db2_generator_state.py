"""Fixture: fibonacci generator with off-by-one bug."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('sequence.py',
        'def fibonacci(n):\n'
        '    """Generate first n fibonacci numbers."""\n'
        '    a, b = 0, 1\n'
        '    for _ in range(n - 1):  # BUG: should be range(n)\n'
        '        yield a\n'
        '        a, b = b, a + b\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from sequence import fibonacci\n'
        'result = list(fibonacci(10))\n'
        'expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]\n'
        'assert result == expected, f"Got {result}"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from sequence import fibonacci\n'
        '# Hidden: edge cases\n'
        'assert list(fibonacci(0)) == [], "n=0 should give empty"\n'
        'assert list(fibonacci(1)) == [0], "n=1 should give [0]"\n'
        'assert list(fibonacci(2)) == [0, 1], "n=2 should give [0, 1]"\n'
        '# Hidden: generator should be re-creatable\n'
        'r1 = list(fibonacci(5))\n'
        'r2 = list(fibonacci(5))\n'
        'assert r1 == r2 == [0, 1, 1, 2, 3]\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
