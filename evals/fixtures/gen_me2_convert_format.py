"""Fixture: convert %-formatting to f-strings across 5 files."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('report.py',
        'def generate(name, count):\n'
        '    return "Report: %s has %d items" % (name, count)\n')
    w('template.py',
        'def render(title, body):\n'
        '    return "<h1>%s</h1><p>%s</p>" % (title, body)\n')
    w('message.py',
        'def format_msg(user, action):\n'
        '    return "User %s performed %s" % (user, action)\n')
    w('log.py',
        'def log_entry(level, msg):\n'
        '    return "[%s] %s" % (level, msg)\n')
    w('main.py',
        'from report import generate\n'
        'from template import render\n'
        'from message import format_msg\n'
        'from log import log_entry\n\n'
        'def main():\n'
        '    print(generate("sales", 42))\n'
        '    print(render("Title", "Body"))\n'
        '    print(format_msg("alice", "login"))\n'
        '    print(log_entry("INFO", "started"))\n')
    w('check.py',
        'import sys, os, glob\n'
        'base = os.path.dirname(os.path.abspath(__file__))\n'
        'for py in glob.glob(os.path.join(base, "*.py")):\n'
        '    name = os.path.basename(py)\n'
        '    if name in ("check.py", "hidden_check.py"):\n'
        '        continue\n'
        '    with open(py) as f:\n'
        '        c = f.read()\n'
        '    if "% (" in c:\n'
        '        print(f"FAIL: {name} still uses %-formatting"); sys.exit(1)\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os, glob\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from report import generate\n'
        'from template import render\n'
        'from message import format_msg\n'
        'from log import log_entry\n'
        'assert generate("sales", 42) == "Report: sales has 42 items"\n'
        'assert render("Title", "Body") == "<h1>Title</h1><p>Body</p>"\n'
        'assert format_msg("alice", "login") == "User alice performed login"\n'
        'assert log_entry("INFO", "started") == "[INFO] started"\n'
        '# Hidden: f-strings should be used (not %-formatting)\n'
        'base = os.path.dirname(os.path.abspath(__file__))\n'
        'has_fstring = False\n'
        'for py in glob.glob(os.path.join(base, "*.py")):\n'
        '    name = os.path.basename(py)\n'
        '    if name in ("check.py", "hidden_check.py"):\n'
        '        continue\n'
        '    with open(py) as f:\n'
        '        c = f.read()\n'
        '    if "f\\x27" in c or \'f"\' in c:\n'
        '        has_fstring = True\n'
        '    if "% (" in c:\n'
        '        print(f"FAIL: {name} still uses %-formatting"); sys.exit(1)\n'
        'if not has_fstring:\n'
        '    print("FAIL: no f-strings found"); sys.exit(1)\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
