"""Fixture: shared mutable default argument bug."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('container.py',
        'def add_item(item, items=[]):\n'
        '    """Add an item to a list and return the list."""\n'
        '    items.append(item)\n'
        '    return items\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from container import add_item\n'
        'a = add_item("apple")\n'
        'b = add_item("banana")\n'
        'assert a == ["apple"], f"First call: {a}"\n'
        'assert b == ["banana"], f"Second call leaked: {b}"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from container import add_item\n'
        '# Hidden: explicit list argument should work\n'
        'explicit = []\n'
        'add_item("x", explicit)\n'
        'add_item("y", explicit)\n'
        'assert explicit == ["x", "y"], f"Explicit list: {explicit}"\n'
        '# Hidden: default should still work after fix\n'
        'c = add_item(1)\n'
        'd = add_item(2)\n'
        'assert c == [1], f"First: {c}"\n'
        'assert d == [2], f"Second: {d}"\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
