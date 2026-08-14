"""Fixture: recursive tree traversal crashes on empty/leaf nodes."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('tree.py',
        'class Node:\n'
        '    def __init__(self, value, children=None):\n'
        '        self.value = value\n'
        '        self.children = children if children is not None else []\n\n'
        'def traverse(node):\n'
        '    """Traverse tree depth-first, returning list of values."""\n'
        '    result = [node.value]\n'
        '    for child in node.children:\n'
        '        result.extend(traverse(child))\n'
        '    return result\n\n'
        'def safe_traverse(node):\n'
        '    """Traverse tree, handling None nodes."""\n'
        '    if node is None:\n'
        '        return []\n'
        '    result = [node.value]\n'
        '    for child in node.children:\n'
        '        result.extend(traverse(child))  # BUG: calls traverse, not safe_traverse\n'
        '    return result\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from tree import Node, safe_traverse\n'
        '# Build a tree with a None child\n'
        'root = Node(1, [Node(2, [None]), Node(3)])\n'
        'result = safe_traverse(root)\n'
        'assert result == [1, 2, 3], f"Got {result}"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from tree import Node, safe_traverse\n'
        '# Hidden: None root\n'
        'assert safe_traverse(None) == []\n'
        '# Hidden: single node\n'
        'assert safe_traverse(Node(42)) == [42]\n'
        '# Hidden: nested None children\n'
        'root = Node(0, [None, Node(1, [None, None]), Node(2)])\n'
        'assert safe_traverse(root) == [0, 1, 2]\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
