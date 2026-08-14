"""Fixture: implement logging system and integrate into service."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('logger.py',
        '# TODO: Implement a simple logging system.\n'
        '# log(level, message): store a log entry.\n'
        '# get_logs(): return all log entries.\n'
        '# Levels: "INFO", "WARNING", "ERROR"\n')
    w('service.py',
        'def process_order(order_id, amount):\n'
        '    """Process a customer order."""\n'
        '    if amount <= 0:\n'
        '        return False\n'
        '    return True\n\n'
        'def cancel_order(order_id):\n'
        '    """Cancel an order."""\n'
        '    return True\n')
    w('main.py',
        'from service import process_order, cancel_order\n\n'
        'def main():\n'
        '    print(process_order(1, 100))\n'
        '    print(cancel_order(1))\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from logger import log, get_logs\n'
        'log("INFO", "test message")\n'
        'logs = get_logs()\n'
        'assert len(logs) >= 1, "No logs found"\n'
        'assert "test message" in str(logs[0])\n'
        'from service import process_order\n'
        'process_order(1, 100)\n'
        'logs2 = get_logs()\n'
        'assert len(logs2) > len(logs), "Service should log"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from logger import log, get_logs\n'
        'log("INFO", "info msg")\n'
        'log("WARNING", "warn msg")\n'
        'log("ERROR", "error msg")\n'
        'logs = get_logs()\n'
        '# Hidden: log entries should distinguish levels\n'
        'log_str = str(logs)\n'
        'assert "INFO" in log_str or "info" in log_str.lower()\n'
        'assert "WARNING" in log_str or "warning" in log_str.lower()\n'
        'assert "ERROR" in log_str or "error" in log_str.lower()\n'
        'from service import process_order\n'
        '# Hidden: error case should log an error\n'
        'before = len(get_logs())\n'
        'process_order(1, -1)\n'
        'after = len(get_logs())\n'
        'assert after > before, "Failed order should log"\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
