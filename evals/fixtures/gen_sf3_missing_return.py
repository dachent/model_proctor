"""Fixture: missing return statement in divide."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('calculator.py',
        'def divide(a, b):\n'
        '    """Divide a by b."""\n'
        '    if b == 0:\n'
        '        raise ValueError("Cannot divide by zero")\n'
        '    result = a / b\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from calculator import divide\n'
        'assert divide(10, 2) == 5.0, f"divide(10,2)={divide(10,2)}"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from calculator import divide\n'
        'assert divide(0, 5) == 0.0\n'
        'assert divide(7, 1) == 7.0\n'
        'assert divide(100, 4) == 25.0\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
