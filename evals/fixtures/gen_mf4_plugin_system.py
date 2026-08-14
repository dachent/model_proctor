"""Fixture: implement plugin registration and execution system."""
import os, sys

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('plugins.py',
        '# TODO: Implement a plugin system.\n'
        '# register_plugin(name, func): register a plugin by name.\n'
        '# run_plugin(name, *args): run a registered plugin with args.\n'
        '# list_plugins(): return list of registered plugin names.\n')
    w('plugin_base.py',
        'class PluginBase:\n'
        '    """Base class for plugins."""\n'
        '    name = "base"\n'
        '    def run(self, *args):\n'
        '        raise NotImplementedError\n')
    w('app.py',
        'def execute_task(task_name, *args):\n'
        '    """Execute a task by name."""\n'
        '    return None\n')
    w('main.py',
        'from app import execute_task\n\n'
        'def main():\n'
        '    print(execute_task("hello"))\n')
    w('check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from plugins import register_plugin, run_plugin, list_plugins\n'
        'def greet(name):\n'
        '    return f"Hello, {name}!"\n'
        'register_plugin("greet", greet)\n'
        'assert "greet" in list_plugins()\n'
        'result = run_plugin("greet", "World")\n'
        'assert result == "Hello, World!", f"Got {result}"\n'
        'from app import execute_task\n'
        '# app.execute_task should use the plugin system\n'
        'result2 = execute_task("greet", "World")\n'
        'assert result2 == "Hello, World!", f"execute_task got {result2}"\n'
        'print("PASS")\n')
    w('hidden_check.py',
        'import sys, os\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from plugins import register_plugin, run_plugin, list_plugins\n'
        '# Hidden: running unregistered plugin should raise/return error\n'
        'try:\n'
        '    run_plugin("nonexistent")\n'
        '    print("FAIL: should handle unknown plugin"); sys.exit(1)\n'
        'except (KeyError, ValueError, LookupError):\n'
        '    pass  # expected\n'
        '# Hidden: multiple plugins coexist\n'
        'register_plugin("add", lambda a, b: a + b)\n'
        'register_plugin("mul", lambda a, b: a * b)\n'
        'assert run_plugin("add", 2, 3) == 5\n'
        'assert run_plugin("mul", 2, 3) == 6\n'
        'assert len(list_plugins()) >= 2\n'
        'print("PASS")\n')

if __name__ == '__main__':
    main()
