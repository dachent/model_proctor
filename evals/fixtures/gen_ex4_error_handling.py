"""Fixture: identify error handling patterns across modules."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('client.py',
        'def send_request(url, payload):\n'
        '    """Send a request and return the response."""\n'
        '    try:\n'
        '        if not url:\n'
        '            raise ValueError("URL is required")\n'
        '        return {"status": 200, "data": payload}\n'
        '    except ValueError as e:\n'
        '        return {"status": 400, "error": str(e)}\n')
    w('server.py',
        'def handle_request(request):\n'
        '    """Handle an incoming server request."""\n'
        '    try:\n'
        '        if request is None:\n'
        '            raise ConnectionError("No connection")\n'
        '        return {"status": "ok"}\n'
        '    except ConnectionError as e:\n'
        '        return {"status": "error", "message": str(e)}\n')
    w('handler.py',
        'import logging\n\n'
        'def process(data):\n'
        '    """Process data with error handling and logging."""\n'
        '    try:\n'
        '        result = data["value"]\n'
        '        logging.info(f"Processed: {result}")\n'
        '        return result\n'
        '    except KeyError:\n'
        '        logging.error("Missing key: value")\n'
        '        return None\n')
    w('check.py',
        'import sys, os\n'
        'p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ANSWER.md")\n'
        'try:\n'
        '    with open(p) as f:\n'
        '        c = f.read()\n'
        'except FileNotFoundError:\n'
        '    print("FAIL: ANSWER.md not found"); sys.exit(1)\n'
        'if "ValueError" not in c:\n'
        '    print("FAIL: missing ValueError"); sys.exit(1)\n'
        'if "try" not in c.lower() and "except" not in c.lower():\n'
        '    print("FAIL: no mention of try/except"); sys.exit(1)\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ANSWER.md")\n'
        'try:\n'
        '    with open(p) as f:\n'
        '        c = f.read()\n'
        'except FileNotFoundError:\n'
        '    print("FAIL: ANSWER.md not found"); sys.exit(1)\n'
        '# Hidden: did the agent notice the logging in handler.py?\n'
        'for kw in ["log", "logging"]:\n'
        '    if kw in c.lower():\n'
        '        print("PASS"); sys.exit(0)\n'
        'print("FAIL: no mention of logging"); sys.exit(1)\n')

if __name__ == '__main__':
    main()
