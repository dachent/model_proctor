"""Fixture: off-by-one bug in sum_to_n."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('math_utils.py',
        'def sum_to_n(n):\n'
        '    """Return sum of integers from 1 to n."""\n'
        '    return sum(range(1, n))\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from math_utils import sum_to_n\n'
        'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from math_utils import sum_to_n\n'
        'assert sum_to_n(1) == 1\n'
        'assert sum_to_n(10) == 55\n'
        'assert sum_to_n(0) == 0\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
