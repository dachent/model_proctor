#!/usr/bin/env python3
"""Bridge to the production kill predicate (#106) for corpus replay (TOOL-039, #110).

Assumed interface (AI-2): #106 ships harnesses/kimi-code/delegate/monitor.py
exposing decide_kill(window) -> verdict dict. Until then this resolves to
None and the production replay suite skips. Override with
REPLAY_PREDICATE="module:function" for local integration experiments.

Only ImportError means "absent": a production module that EXISTS but dies at
import (OSError, AttributeError from its ctypes top level) propagates and
turns the suite red on purpose — a broken production import is a #106 defect,
not a replay skip (see plan Review Focus #5).

Python 3.10, standard library only.
"""
import importlib
import os
import sys
from pathlib import Path

DEFAULT_SPEC = "monitor:decide_kill"


def load_production_decide(environ=None):
    env = os.environ if environ is None else environ
    spec = env.get("REPLAY_PREDICATE", DEFAULT_SPEC)
    module_name, sep, func_name = spec.partition(":")
    if not sep or not module_name or not func_name:
        raise ValueError(
            f"REPLAY_PREDICATE must be 'module:function', got {spec!r}")
    delegate_dir = Path(__file__).resolve().parents[1] / "delegate"
    if str(delegate_dir) not in sys.path:
        sys.path.insert(0, str(delegate_dir))
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, func_name, None)
