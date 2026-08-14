"""Fixture: add type hints to functions across 4 files."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('operations.py',
        'def add(a, b):\n'
        '    return a + b\n\n'
        'def multiply(a, b):\n'
        '    return a * b\n')
    w('calculations.py',
        'def average(numbers):\n'
        '    return sum(numbers) / len(numbers)\n\n'
        'def max_value(numbers):\n'
        '    return max(numbers)\n')
    w('conversions.py',
        'def to_string(value):\n'
        '    return str(value)\n\n'
        'def to_int(value):\n'
        '    return int(value)\n')
    w('main.py',
        'from operations import add, multiply\n'
        'from calculations import average, max_value\n'
        'from conversions import to_string, to_int\n\n'
        'def main():\n'
        '    print(add(1, 2))\n'
        '    print(multiply(3, 4))\n'
        '    print(average([1, 2, 3]))\n'
        '    print(to_string(42))\n')
    w('check.py',
        'import sys, os, glob, re\n'
        'base = os.path.dirname(os.path.abspath(__file__))\n'
        'for py in glob.glob(os.path.join(base, "*.py")):\n'
        '    name = os.path.basename(py)\n'
        '    if name in ("check.py", "hidden_check.py"):\n'
        '        continue\n'
        '    with open(py) as f:\n'
        '        c = f.read()\n'
        '    if "def " in c and "->" not in c:\n'
        '        print(f"FAIL: {name} has functions without return type hints"); sys.exit(1)\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os, glob, re\n'
        'base = os.path.dirname(os.path.abspath(__file__))\n'
        '# Hidden: check that parameter annotations exist (not just return types)\n'
        'for py in glob.glob(os.path.join(base, "*.py")):\n'
        '    name = os.path.basename(py)\n'
        '    if name in ("check.py", "hidden_check.py"):\n'
        '        continue\n'
        '    with open(py) as f:\n'
        '        c = f.read()\n'
        '    for m in re.finditer(r\'def (\\w+)\\(([^)]*)\\)\', c):\n'
        '        params = m.group(2).strip()\n'
        '        if params and params != "self" and ":" not in params:\n'
        '            print(f"FAIL: {name}.{m.group(1)} has untyped params"); sys.exit(1)\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
