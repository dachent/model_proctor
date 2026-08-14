"""Fixture: wrong string method in capitalize_words."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('formatter.py',
        'def capitalize_words(s):\n'
        '    """Capitalize each word in the string."""\n'
        '    words = s.split()\n'
        '    return \' \'.join(w.lower() for w in words)\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from formatter import capitalize_words\n'
        'assert capitalize_words("hello world") == "Hello World"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from formatter import capitalize_words\n'
        'assert capitalize_words("") == ""\n'
        'assert capitalize_words("a") == "A"\n'
        'assert capitalize_words("HELLO WORLD") == "Hello World"\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
