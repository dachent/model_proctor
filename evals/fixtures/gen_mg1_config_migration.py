"""Fixture: migrate config from v1 flat format to v2 nested format."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('config.py',
        'def load_v1(data):\n'
        '    """Load v1 config (flat keys)."""\n'
        '    return {\n'
        '        "name": data.get("name", ""),\n'
        '        "host": data.get("host", "localhost"),\n'
        '        "port": data.get("port", 8080),\n'
        '        "debug": data.get("debug", False),\n'
        '    }\n\n'
        '# TODO: Implement v2 format (nested structure):\n'
        '# {"app": {"name": ..., "debug": ...}, "server": {"host": ..., "port": ...}}\n'
        '# Also implement migrate_v1_to_v2(v1_config) and a backward-compat shim\n'
        '# (read_v2_as_v1) so old code can still work with v2 configs.\n')
    w('main.py',
        'from config import load_v1\n\n'
        'def main():\n'
        '    cfg = load_v1({"name": "myapp", "host": "0.0.0.0", "port": 3000})\n'
        '    print(cfg)\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from config import load_v1, migrate_v1_to_v2\n'
        'v1 = load_v1({"name": "test", "host": "1.2.3.4", "port": 99, "debug": True})\n'
        'v2 = migrate_v1_to_v2(v1)\n'
        'assert "app" in v2, f"Missing app key: {v2}"\n'
        'assert "server" in v2, f"Missing server key: {v2}"\n'
        'assert v2["app"]["name"] == "test"\n'
        'assert v2["app"]["debug"] == True\n'
        'assert v2["server"]["host"] == "1.2.3.4"\n'
        'assert v2["server"]["port"] == 99\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from config import migrate_v1_to_v2, read_v2_as_v1\n'
        '# Hidden: backward-compat shim should let old code read v2 as v1\n'
        'v2 = {"app": {"name": "x", "debug": False}, "server": {"host": "h", "port": 1}}\n'
        'v1 = read_v2_as_v1(v2)\n'
        'assert v1["name"] == "x", f"name: {v1}"\n'
        'assert v1["host"] == "h"\n'
        'assert v1["port"] == 1\n'
        'assert v1["debug"] == False\n'
        '# Hidden: roundtrip v1 -> v2 -> v1 should preserve data\n'
        'original = {"name": "rt", "host": "rt.host", "port": 42, "debug": True}\n'
        'rt = read_v2_as_v1(migrate_v1_to_v2(original))\n'
        'assert rt == original, f"Roundtrip failed: {rt}"\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
