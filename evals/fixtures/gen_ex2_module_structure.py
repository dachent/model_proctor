"""Fixture: identify validation module in a multi-module project."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('validators.py',
        'def validate_input(data):\n'
        '    """Validate input data."""\n'
        '    if not data:\n'
        '        raise ValueError("Empty input")\n'
        '    return True\n\n'
        'def validate_output(data):\n'
        '    """Validate output data."""\n'
        '    if data is None:\n'
        '        raise ValueError("Null output")\n'
        '    return True\n\n'
        'def sanitize_data(data):\n'
        '    """Sanitize data by removing dangerous characters."""\n'
        '    return str(data).replace("<", "").replace(">", "")\n')
    w('models.py',
        'class User:\n'
        '    def __init__(self, name, email):\n'
        '        self.name = name\n'
        '        self.email = email\n\n'
        'class Product:\n'
        '    def __init__(self, name, price):\n'
        '        self.name = name\n'
        '        self.price = price\n')
    w('utils.py',
        'def format_date(dt):\n'
        '    return dt.strftime("%Y-%m-%d")\n\n'
        'def parse_int(s):\n'
        '    return int(s.strip())\n')
    w('main.py',
        'from validators import validate_input, validate_output, sanitize_data\n'
        'from models import User, Product\n'
        'from utils import format_date, parse_int\n\n'
        'def run(data):\n'
        '    validate_input(data)\n'
        '    clean = sanitize_data(data)\n'
        '    return clean\n')
    w('check.py',
        'import sys, os\n'
        'p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ANSWER.md")\n'
        'try:\n'
        '    with open(p) as f:\n'
        '        c = f.read()\n'
        'except FileNotFoundError:\n'
        '    print("FAIL: ANSWER.md not found"); sys.exit(1)\n'
        'if "validators" not in c:\n'
        '    print("FAIL: missing validators"); sys.exit(1)\n'
        'if "validate_input" not in c:\n'
        '    print("FAIL: missing validate_input"); sys.exit(1)\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ANSWER.md")\n'
        'try:\n'
        '    with open(p) as f:\n'
        '        c = f.read()\n'
        'except FileNotFoundError:\n'
        '    print("FAIL: ANSWER.md not found"); sys.exit(1)\n'
        '# Hidden: did the agent find sanitize_data (not obviously named "validate")?\n'
        'if "sanitize_data" not in c:\n'
        '    print("FAIL: missing sanitize_data"); sys.exit(1)\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
