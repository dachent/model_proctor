"""Fixture: rename get_data to fetch_data across 5 files."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('core.py',
        'def get_data():\n'
        '    """Return sample data."""\n'
        '    return [1, 2, 3]\n')
    w('utils.py',
        'from core import get_data\n\n'
        'def process():\n'
        '    data = get_data()\n'
        '    return len(data)\n')
    w('helpers.py',
        'from core import get_data\n\n'
        'def display():\n'
        '    for item in get_data():\n'
        '        print(item)\n')
    w('extra.py',
        'from core import get_data\n\n'
        'def summarize():\n'
        '    d = get_data()\n'
        '    return sum(d)\n')
    w('main.py',
        'from core import get_data\n'
        'from utils import process\n'
        'from helpers import display\n'
        'from extra import summarize\n\n'
        'def main():\n'
        '    print(get_data())\n'
        '    print(process())\n'
        '    display()\n'
        '    print(summarize())\n')
    w('check.py',
        'import sys, os, glob\n'
        'base = os.path.dirname(os.path.abspath(__file__))\n'
        'for py in glob.glob(os.path.join(base, "*.py")):\n'
        '    name = os.path.basename(py)\n'
        '    if name in ("check.py", "hidden_check.py"):\n'
        '        continue\n'
        '    with open(py) as f:\n'
        '        c = f.read()\n'
        '    if "get_data" in c:\n'
        '        print(f"FAIL: {name} still has get_data"); sys.exit(1)\n'
        '    if "fetch_data" not in c:\n'
        '        print(f"FAIL: {name} missing fetch_data"); sys.exit(1)\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'try:\n'
        '    from core import fetch_data\n'
        '    assert fetch_data() == [1, 2, 3]\n'
        '    from utils import process\n'
        '    assert process() == 3\n'
        '    from extra import summarize\n'
        '    assert summarize() == 6\n'
        '    print("PASS")\n'
        'except Exception as e:\n'
        '    print(f"FAIL: {e}"); sys.exit(1)\n')

if __name__ == '__main__':
    main()
