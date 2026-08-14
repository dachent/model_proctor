"""Fixture: wrong comparison operator in is_passing."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('grades.py',
        'def is_passing(score, threshold=60):\n'
        '    """Return True if score meets the passing threshold."""\n'
        '    return score > threshold\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from grades import is_passing\n'
        'assert is_passing(60) == True, "60 should be passing"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from grades import is_passing\n'
        'assert is_passing(59) == False\n'
        'assert is_passing(60) == True, "boundary 60 should pass"\n'
        'assert is_passing(60, 50) == True\n'
        'assert is_passing(100) == True\n'
        'assert is_passing(50, 50) == True, "boundary equal should pass"\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
