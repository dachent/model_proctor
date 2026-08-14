"""Fixture: path traversal vulnerability in file server."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    # Create a safe data directory with a sample file
    data_dir = os.path.join(d, 'data')
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, 'hello.txt'), 'w') as f:
        f.write('Hello, World!')
    # Create a secret file at the fixture root (should not be accessible via data dir)
    with open(os.path.join(d, 'secret.txt'), 'w') as f:
        f.write('TOP SECRET DATA')
    w('fileserver.py',
        'import os\n\n'
        'BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")\n\n'
        'def read_file(path):\n'
        '    """Read a file from the data directory."""\n'
        '    full_path = os.path.join(BASE_DIR, path)\n'
        '    with open(full_path) as f:\n'
        '        return f.read()\n')
    w('main.py',
        'from fileserver import read_file\n\n'
        'def main():\n'
        '    print(read_file("hello.txt"))\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from fileserver import read_file\n'
        'assert read_file("hello.txt") == "Hello, World!"\n'
        '# Path traversal must be blocked\n'
        'try:\n'
        '    read_file("../secret.txt")\n'
        '    print("FAIL: path traversal not blocked"); sys.exit(1)\n'
        'except (ValueError, PermissionError, FileNotFoundError):\n'
        '    pass\n'
        'except Exception:\n'
        '    pass\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from fileserver import read_file\n'
        '# Hidden: path traversal should be blocked\n'
        'for evil in ["../secret.txt", "../../secret.txt", "..\\\\secret.txt"]:\n'
        '    try:\n'
        '        result = read_file(evil)\n'
        '        print(f"FAIL: {evil} was accessible: {result!r}"); sys.exit(1)\n'
        '    except (ValueError, PermissionError, FileNotFoundError):\n'
        '        pass  # expected: traversal blocked\n'
        '    except Exception:\n'
        '        pass  # any blocking is fine\n'
        '# Valid file should still work\n'
        'assert read_file("hello.txt") == "Hello, World!"\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
