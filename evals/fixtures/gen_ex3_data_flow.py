"""Fixture: trace data flow through a pipeline."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('pipeline.py',
        'from transforms import transform\n'
        'from filters import filter_data\n'
        'from aggregator import aggregate\n\n'
        'def run_pipeline(data):\n'
        '    """Run data through the processing pipeline."""\n'
        '    transformed = transform(data)\n'
        '    filtered = filter_data(transformed)\n'
        '    result = aggregate(filtered)\n'
        '    return result\n')
    w('transforms.py',
        'def transform(data):\n'
        '    """Double each value in the data."""\n'
        '    return [x * 2 for x in data]\n')
    w('filters.py',
        'def filter_data(data):\n'
        '    """Remove zero values from the data."""\n'
        '    return [x for x in data if x != 0]\n')
    w('aggregator.py',
        'def aggregate(data):\n'
        '    """Sum all values in the data."""\n'
        '    return sum(data)\n')
    w('check.py',
        'import sys, os\n'
        'p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ANSWER.md")\n'
        'try:\n'
        '    with open(p) as f:\n'
        '        c = f.read()\n'
        'except FileNotFoundError:\n'
        '    print("FAIL: ANSWER.md not found"); sys.exit(1)\n'
        'for kw in ["transform", "filter", "aggregate"]:\n'
        '    if kw not in c.lower():\n'
        '        print(f"FAIL: missing {kw}"); sys.exit(1)\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ANSWER.md")\n'
        'try:\n'
        '    with open(p) as f:\n'
        '        c = f.read()\n'
        'except FileNotFoundError:\n'
        '    print("FAIL: ANSWER.md not found"); sys.exit(1)\n'
        '# Hidden: did the agent describe what each stage does (not just names)?\n'
        'for kw in ["double", "sum", "zero", "remove"]:\n'
        '    if kw in c.lower():\n'
        '        print("PASS"); sys.exit(0)\n'
        'print("FAIL: no description of what stages do"); sys.exit(1)\n')

if __name__ == '__main__':
    main()
