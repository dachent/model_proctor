"""Fixture: implement cache layer and integrate into data_loader."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('cache.py',
        '# TODO: Implement a Cache class with get(key) and set(key, value) methods.\n'
        '# get should return the cached value or None if not found.\n'
        '# set should store the value.\n')
    w('data_loader.py',
        'def load_data(key):\n'
        '    """Load data for a key. This is expensive."""\n'
        '    return {"key": key, "value": key * 10}\n')
    w('main.py',
        'from data_loader import load_data\n\n'
        'def main():\n'
        '    print(load_data(1))\n'
        '    print(load_data(2))\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from cache import Cache\n'
        'c = Cache()\n'
        'c.set("a", 1)\n'
        'assert c.get("a") == 1\n'
        'assert c.get("b") is None\n'
        'from data_loader import load_data\n'
        'r1 = load_data(1)\n'
        'r2 = load_data(1)\n'
        'assert r1 == r2, "Second call should return cached result"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from cache import Cache\n'
        'c = Cache()\n'
        'c.set("x", {"nested": True})\n'
        'assert c.get("x") == {"nested": True}\n'
        '# Hidden: cache should not share state between instances\n'
        'c2 = Cache()\n'
        'assert c2.get("x") is None, "Cache instances should be isolated"\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
