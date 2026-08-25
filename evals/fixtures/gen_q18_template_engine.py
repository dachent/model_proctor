"""Fixture: q18_template_engine - {{ }} substitution with HTML escaping and {{{ }}} raw."""
import os, sys

MODULE = '''"""Minimal template engine.

Implement render(template, context):
- Literal text passes through unchanged.
- {{ name }} substitutes a value from context; whitespace inside the
  braces is ignored; dotted names do nested lookup ({{ user.name }}
  looks up context["user"]["name"]). Names are dot-separated
  identifiers.
- Substituted values are HTML-escaped: & -> &amp;, < -> &lt;, > -> &gt;,
  " -> &quot;, ' -> &#x27; (& is escaped first). Non-string values are
  converted with str() before escaping.
- {{{ name }}} substitutes the value WITHOUT escaping (same lookup
  rules).
- A missing key at any point of the lookup raises KeyError.
- A {{ or {{{ tag that is never closed raises ValueError; a name that
  is empty or not dot-separated identifiers raises ValueError.
- Single braces and any other text are literal.
"""


def render(template, context):
    """Render the template against context and return the resulting string."""
    raise NotImplementedError("implement render")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tmpl import render

assert render("Hello, {{ name }}!", {"name": "World"}) == "Hello, World!"
assert render("{{user.name}} is {{user.age}}",
              {"user": {"name": "Amy", "age": 3}}) == "Amy is 3"
assert render("no tags here", {}) == "no tags here"
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tmpl import render

# each escape character
assert render("{{ v }}", {"v": "&"}) == "&amp;"
assert render("{{ v }}", {"v": "<"}) == "&lt;"
assert render("{{ v }}", {"v": ">"}) == "&gt;"
assert render("{{ v }}", {"v": chr(34)}) == "&quot;"
assert render("{{ v }}", {"v": chr(39)}) == "&#x27;"
assert render("{{ v }}", {"v": "<a & 'b'>"}) == "&lt;a &amp; &#x27;b&#x27;&gt;"

# raw triple-brace substitutes WITHOUT escaping
assert render("{{{ v }}}", {"v": "<b>&</b>"}) == "<b>&</b>"
assert render("{{{ user.name }}}", {"user": {"name": "<x>"}}) == "<x>"

# whitespace inside braces is ignored
assert render("{{   name   }}", {"name": "x"}) == "x"

# adjacent substitutions
assert render("{{a}}{{b}}{{{c}}}", {"a": "1", "b": "2", "c": "3"}) == "123"

# missing key raises KeyError (top-level and nested)
for tmpl, ctx in [("{{ missing }}", {}), ("{{ user.name }}", {"user": {}})]:
    try:
        render(tmpl, ctx)
        raise SystemExit("expected KeyError for %r" % tmpl)
    except KeyError:
        pass

# unclosed tags and empty names raise ValueError
for bad in ["Hello {{ name", "{{{ name }}", "{{ }}"]:
    try:
        render(bad, {"name": "x"})
        raise SystemExit("expected ValueError for %r" % bad)
    except ValueError:
        pass

# literal text passes through unchanged
assert render("a { b } c", {}) == "a { b } c"
assert render("100%", {}) == "100%"

# non-string values are converted with str() before escaping
assert render("{{ n }}", {"n": 42}) == "42"
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('tmpl.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
