"""Fixture: q16_calendar_scheduler - greedy priority scheduler with gap fitting."""
import os, sys

MODULE = '''"""Greedy calendar scheduler.

Implement schedule(events, day_start, day_end):
- Each event is a dict with keys: name (str), duration (int minutes,
  > 0), priority (int; LOWER means more important), not_before (int
  minutes since midnight).
- Place events greedily in priority order; ties are broken by earlier
  not_before, then by name in alphabetical order.
- Each event is placed in its earliest fitting slot: the earliest start
  >= max(not_before, day_start) such that [start, start + duration)
  does not overlap any already-placed event and start + duration <=
  day_end.
- Back-to-back packing is allowed: an event may start exactly when
  another ends (end == next start is not an overlap).
- Events that cannot fit are dropped silently.
- Return a list of (name, start, end) tuples sorted by start.
"""


def schedule(events, day_start, day_end):
    """Greedily schedule events; return (name, start, end) tuples sorted by start."""
    raise NotImplementedError("implement schedule")
'''

CHECK = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal import schedule

events = [
    {"name": "standup", "duration": 30, "priority": 1, "not_before": 540},
    {"name": "review", "duration": 60, "priority": 2, "not_before": 600},
]
res = schedule(events, 540, 1080)
assert res == [("standup", 540, 570), ("review", 600, 660)], res
print("PASS")
'''

HIDDEN = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal import schedule

# priority inversion: listed later but higher priority -> scheduled first
events = [
    {"name": "low", "duration": 120, "priority": 5, "not_before": 540},
    {"name": "high", "duration": 60, "priority": 1, "not_before": 540},
]
res = schedule(events, 540, 1080)
assert res == [("high", 540, 600), ("low", 600, 720)], res

# back-to-back packing allowed: end == next start is not an overlap
events = [
    {"name": "a", "duration": 60, "priority": 1, "not_before": 540},
    {"name": "b", "duration": 60, "priority": 2, "not_before": 600},
]
res = schedule(events, 540, 720)
assert res == [("a", 540, 600), ("b", 600, 660)], res

# an event that cannot fit is dropped silently
events = [
    {"name": "big", "duration": 600, "priority": 1, "not_before": 540},
    {"name": "small", "duration": 30, "priority": 2, "not_before": 540},
]
res = schedule(events, 540, 1080)  # only 540 minutes available
assert res == [("small", 540, 570)], res

# tie-breaking: same priority -> earlier not_before, then name
events = [
    {"name": "zed", "duration": 30, "priority": 2, "not_before": 600},
    {"name": "amy", "duration": 30, "priority": 2, "not_before": 600},
    {"name": "bee", "duration": 30, "priority": 2, "not_before": 540},
]
res = schedule(events, 540, 1080)
assert res == [("bee", 540, 570), ("amy", 600, 630), ("zed", 630, 660)], res

# boundary at day_end: an event ending exactly at day_end fits
events = [{"name": "tail", "duration": 60, "priority": 1, "not_before": 1000}]
res = schedule(events, 540, 1060)
assert res == [("tail", 1000, 1060)], res

# not_before pushes the start later even when an earlier slot is free
events = [{"name": "late", "duration": 30, "priority": 1, "not_before": 700}]
res = schedule(events, 540, 1080)
assert res == [("late", 700, 730)], res

# overlapping placement skips ahead to the next gap
events = [
    {"name": "first", "duration": 60, "priority": 1, "not_before": 540},
    {"name": "second", "duration": 30, "priority": 2, "not_before": 540},
    {"name": "third", "duration": 45, "priority": 3, "not_before": 555},
]
res = schedule(events, 540, 1080)
assert res == [("first", 540, 600), ("second", 600, 630), ("third", 630, 675)], res
print("PASS")
'''

def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    def w(name, content):
        with open(os.path.join(d, name), 'w') as f:
            f.write(content)
    w('cal.py', MODULE)
    w('check.py', CHECK)
    w('hidden_check.py', HIDDEN)

if __name__ == '__main__':
    main()
