"""Fixture: trace call chain across 4 modules."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('main.py',
        'from service import handle_request\n\n'
        'def process_request(req):\n'
        '    """Process an incoming request."""\n'
        '    result = handle_request(req)\n'
        '    return result\n')
    w('service.py',
        'from repository import fetch_data\n\n'
        'def handle_request(req):\n'
        '    """Handle a request by fetching data."""\n'
        '    try:\n'
        '        data = fetch_data(req["id"])\n'
        '        return data\n'
        '    except Exception as e:\n'
        '        print(f"Error: {e}")\n'
        '        return None\n')
    w('repository.py',
        'from database import query_database\n\n'
        'def fetch_data(item_id):\n'
        '    """Fetch data for an item from the database."""\n'
        '    return query_database(item_id)\n')
    w('database.py',
        'def query_database(item_id):\n'
        '    """Query the database for an item."""\n'
        '    return {"id": item_id, "name": "item_" + str(item_id)}\n')
    w('check.py',
        'import sys, os\n'
        'p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ANSWER.md")\n'
        'try:\n'
        '    with open(p) as f:\n'
        '        c = f.read()\n'
        'except FileNotFoundError:\n'
        '    print("FAIL: ANSWER.md not found"); sys.exit(1)\n'
        'for kw in ["process_request", "handle_request", "fetch_data", "query_database"]:\n'
        '    if kw not in c:\n'
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
        '# Hidden: did the agent notice the error handling in service.py?\n'
        'for kw in ["try", "except", "error"]:\n'
        '    if kw in c.lower():\n'
        '        print("PASS"); sys.exit(0)\n'
        'print("FAIL: no mention of error handling"); sys.exit(1)\n')

if __name__ == '__main__':
    main()
