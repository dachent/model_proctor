"""Fixture: q6_config_precedence - layered config with type coercion."""
import os, sys

MODULE = '''"""Layered configuration.

Implement load_config(defaults, file_cfg, env, cli):
- All four arguments are dicts; values in file_cfg, env and cli are strings.
- Precedence is cli > env > file_cfg > defaults.
- Any key in file_cfg, env or cli that is NOT present in defaults raises
  ValueError.
- String values are coerced to the type of the corresponding default,
  supporting str, int and bool: bool accepts "true"/"false"
  case-insensitive; int is parsed from decimal strings.
- Return the merged dict.
"""


def load_config(defaults, file_cfg, env, cli):
    """Merge configuration layers; see module docstring."""
    raise NotImplementedError("implement load_config")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config

defaults = {"host": "localhost", "port": 8080}
merged = load_config(defaults, {"port": "9090"}, {}, {})
assert merged == {"host": "localhost", "port": 9090}, \\
    "file_cfg must override defaults and coerce '9090' to int: got %r" % (merged,)
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config

# full precedence chain: cli > env > file_cfg > defaults
defaults = {"a": 1, "b": 2, "c": 3, "d": 4}
merged = load_config(defaults,
                     {"a": "10", "b": "20", "c": "30"},
                     {"a": "100", "b": "200"},
                     {"a": "1000"})
assert merged == {"a": 1000, "b": 200, "c": 30, "d": 4}, \\
    "precedence must be cli > env > file_cfg > defaults: got %r" % (merged,)

# bool coercion, both cases, case-insensitive
assert load_config({"flag": True}, {"flag": "false"}, {}, {}) == {"flag": False}, \\
    "'false' must coerce to False"
assert load_config({"flag": False}, {}, {"flag": "TrUe"}, {}) == {"flag": True}, \\
    "'TrUe' must coerce to True (case-insensitive)"

# int coercion from decimal strings
out = load_config({"n": 0}, {}, {}, {"n": "42"})
assert out == {"n": 42} and isinstance(out["n"], int), "'42' must become int 42"

# str values pass through
assert load_config({"s": "x"}, {"s": "hello"}, {}, {}) == {"s": "hello"}

# unknown keys raise ValueError, in file_cfg, in env, and in cli
try:
    load_config({"a": 1}, {"zzz": "1"}, {}, {})
    raise AssertionError("unknown file_cfg key must raise ValueError")
except ValueError:
    pass
try:
    load_config({"a": 1}, {}, {"zzz": "1"}, {})
    raise AssertionError("unknown env key must raise ValueError")
except ValueError:
    pass
try:
    load_config({"a": 1}, {}, {}, {"zzz": "1"})
    raise AssertionError("unknown cli key must raise ValueError")
except ValueError:
    pass

# empty override layers return the defaults
assert load_config({"a": 1}, {}, {}, {}) == {"a": 1}
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('config.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
