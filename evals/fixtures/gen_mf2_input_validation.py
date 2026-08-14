"""Fixture: add input validation to API functions."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('validators.py',
        '# TODO: Implement validation functions here.\n'
        '# validate_name(name): must be non-empty string.\n'
        '# validate_age(age): must be integer 0-150.\n'
        '# validate_email(email): must contain @.\n')
    w('api.py',
        'def create_user(name, age, email):\n'
        '    """Create a user. No validation yet."""\n'
        '    return {"name": name, "age": age, "email": email}\n\n'
        'def update_user(user_id, name, age, email):\n'
        '    """Update a user. No validation yet."""\n'
        '    return {"id": user_id, "name": name, "age": age, "email": email}\n')
    w('models.py',
        'class User:\n'
        '    def __init__(self, name, age, email):\n'
        '        self.name = name\n'
        '        self.age = age\n'
        '        self.email = email\n')
    w('main.py',
        'from api import create_user, update_user\n\n'
        'def main():\n'
        '    print(create_user("Alice", 30, "alice@example.com"))\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from api import create_user, update_user\n'
        'try:\n'
        '    create_user("", 30, "a@b.com")\n'
        '    print("FAIL: empty name should raise ValueError"); sys.exit(1)\n'
        'except ValueError:\n'
        '    pass\n'
        'try:\n'
        '    create_user("Alice", -1, "a@b.com")\n'
        '    print("FAIL: negative age should raise ValueError"); sys.exit(1)\n'
        'except ValueError:\n'
        '    pass\n'
        'try:\n'
        '    create_user("Alice", 30, "noat")\n'
        '    print("FAIL: email without @ should raise ValueError"); sys.exit(1)\n'
        'except ValueError:\n'
        '    pass\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from api import create_user, update_user\n'
        '# Hidden: update_user should also validate\n'
        'try:\n'
        '    update_user(1, "", 30, "a@b.com")\n'
        '    print("FAIL: update_user should validate name"); sys.exit(1)\n'
        'except ValueError:\n'
        '    pass\n'
        'try:\n'
        '    create_user("Alice", 200, "a@b.com")\n'
        '    print("FAIL: age 200 should raise ValueError"); sys.exit(1)\n'
        'except ValueError:\n'
        '    pass\n'
        '# Valid input should work\n'
        'result = create_user("Bob", 25, "bob@x.com")\n'
        'assert result["name"] == "Bob"\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
