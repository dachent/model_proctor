# Timeout Coherence Implementation Plan

> Executors: use the `subagent-driven-development` skill — one subagent per task, in task order.

**Planning status resolution:** issue #108 carries "Planning REQUIRED — inventory of all timeout knobs across delegate/cascade/runner and supervised payloads, the sizing rule, and where the invariant is enforced." This document IS that planning deliverable: the knob inventory (verified against the code, see Spec), the sizing rule (Task 1), and the enforcement point decision (preflight-style `runner.py timeouts` command + a `load_task` tripwire, Tasks 3 and 5). Executing this plan closes the ticket's planning phase; do not write a separate planning doc.

## Goal

Make the dispatch timeout stack provably ordered — inner self-abort < outer breaker, with margin — under one sizing authority, with a preflight that refuses inverted stacks and one command from which every timeout knob is discoverable. Split `budget.timeout_s`, which today sizes BOTH the dispatch and the verifier (`runner.py:1000` vs `runner.py:1126`).

## Architecture

A new pure-function module `core/timeout_stack.py` becomes the single sizing authority: it owns the margin constants and derives every ceiling (`delegate_ceiling_s`, `runner_breaker_s`, `pilot_breaker_s`) plus a `validate_stack` invariant check. `runner.py` replaces its four `+ 120` literals and pilot its `+ 300` literal with calls into it; the verifier gets its own budget field `verify_timeout_s` (schema-validated, defaulted at `load_task`, backward-compatible fallback for pre-split state files). Discoverability is a new `runner.py timeouts` command that assembles the full stack — task budget, derived layers, delegate knobs fetched via a new `delegate.py --print-timeout-knobs` report — prints it as JSON, and exits 1 with named violations on any inversion.

## Tech Stack

Python 3.10, standard library only (AGENTS.md:115-116). stdlib `unittest`, one discovery dir per suite; CI is `windows-latest` only (`.github/workflows/ci.yml:25-41`) plus a flat-install smoke (`ci.yml:42-47`). No new dependencies.

## Spec

- GitHub issue **dachent/model_proctor#108** — `[TOOL-037] Timeout coherence: order the timeout stack (sub-stage < supervisor < breaker) with one sizing authority` (fetched live 2026-09-26; open, no labels). Acceptance: (1) preflight refuses a configuration where an outer breaker is tighter than the inner self-abort it guards; (2) a wedged inner job yields a named error from the inner layer, not a tree-kill. Related: TOOL-035.
- Inventory doc: `mp_inventory_103-110.md` §3 (knob tables), §3e (nesting order), #108 plan-feeding notes. Every load-bearing reference was re-verified against the worktree; see "Inventory corrections" at the end.
- **Scope note:** the issue body's measured case is the *fsn weekly pipeline* (bridge `subprocess.run` with no timeout, 3h `whole_job_seconds`, 30min HardQuiet breaker). That bridge lives in the other repo and is out of scope here; the invariant it violates is the same rule this plan encodes for the model_proctor stack. The frozen `cascade.py` (AGENTS.md:24-30) is explicitly NOT touched.

### Verified knob chain (the nesting this plan preserves and proves)

`pilot run_runner dispatch call (timeout_s + 300, pilot.py:339)` > `runner.run_delegate deadline (timeout_s + 120, runner.py:721)` > `delegate --timeout timeout_s (+ grace ≤ 60, delegate.py:417-421, + ~30s overhead, delegate.py:17-18)`.

The derivation that justifies the existing margins (and becomes the sizing rule):

```
RUNNER_BREAKER_MARGIN_S = KILL_GRACE_MAX_S(60) + DELEGATE_OVERHEAD_S(30) + MIN_REPORT_MARGIN_S(30) = 120
PILOT_BREAKER_MARGIN_S  = 300   (≥ 120 + 30, margin 180)
```

i.e. the runner's historical `+ 120` is exactly worst-case delegate ceiling + report margin. The constants already nest; the defect is that nothing derives, validates, or co-locates them, and that `budget.timeout_s` serves two scopes.

## Global Constraints

- stdlib-only Python 3.10 for control-plane logic; no new packages.
- Tests: stdlib unittest in the existing per-suite dirs; run the affected suite(s) after every task and all 8 suites before the final commit.
- Do NOT touch `harnesses/kimi-code/cascade/` (frozen research artifact).
- `harnesses/kimi-code/delegate/agents.json` is gitignored; tests use the tracked `agents.example.json` via `DELEGATE_CONFIG`.
- A new core module must be added to `scripts/install.py` (`RUNNER_FILES` + `REQUIRED_AFTER_INSTALL`) or the flat install at `C:\Tools\model-proctor` breaks — the CI smoke at `ci.yml:42-47` only checks that required names exist in the source dirs, so installer drift is silent without this step.
- Repo rule (AGENTS.md:120): **no git mutations without explicit user confirmation.** The commit commands below are exact, but the executor must confirm the user's standing go-ahead before running them. Commit style follows the repo log (`TOOL-037 #108: <subject>`), `git add` with explicit paths only.
- Envelope/status vocabulary (`completed|failed|timeout|interrupted|internal_error`) and exit codes (0/64/70/124/130) are unchanged.

## Review Focus

The five failure modes the spec implies that NO task's happy-path tests exercise — each pinned to its owning task:

1. **Stale pre-split state files** — a state initialized before `verify_timeout_s` existed must fall back to `timeout_s`, not `KeyError` (state budget is frozen at init, `runner.py:839`). → Task 3, `test_absent_verify_timeout_falls_back_to_dispatch_timeout`.
2. **Ceiling disagreement** — the `+ 120` literal lives in four places (`runner.py:721,910,1238,1529`); miss one and orphan marking can fire while a dispatch is legally alive, or accept's in-flight gate uses a different ceiling than the breaker. → Task 5, `BreakerCeilingParity`.
3. **Malformed/missing delegate config during discovery** — the knobs probe must degrade to `knobs_valid: false` + a named violation, never a traceback or a crash on missing keys. → Task 4 (`test_invalid_config_is_reported_not_crash`, `test_missing_config_is_reported_not_crash`) and Task 5 (`test_inverted_delegate_config_is_refused_by_name`).
4. **Float budgets** — the schema allows `timeout_s: 60.5` (`core/tests/test_task_schema.py:35`); derivation arithmetic and the JSON layer report must stay float-safe. → Task 1, `test_float_budgets_stay_float`.
5. **Flat-install drift** — installed runner at `C:\Tools\model-proctor` resolves core modules as siblings; a missing `timeout_stack.py` must refuse loudly (like `_task_schema`'s `task_schema_module_missing`), and `scripts/install.py` must actually ship it. → Task 5, `test_flat_install_layout_resolves_timeout_stack` + `test_flat_install_missing_module_refuses_loudly` + install.py edits.

---

## Task 1 — `core/timeout_stack.py`: the sizing authority

**Files:**
- Create: `core/timeout_stack.py`
- Test: `core/tests/test_timeout_stack.py` (create)
- Modify: `AGENTS.md` (add one bullet under the `core/task_schema.py` bullet, ~line 104)

**Interfaces (every later task consumes these):**

```python
DELEGATE_OVERHEAD_S: float = 30.0
KILL_GRACE_MAX_S: float = 60.0
MIN_REPORT_MARGIN_S: float = 30.0
RUNNER_BREAKER_MARGIN_S: float = 120.0   # derived: 60 + 30 + 30
PILOT_BREAKER_MARGIN_S: float = 300.0
DEFAULT_VERIFY_TIMEOUT_S: float = 600.0

def delegate_ceiling_s(timeout_s, grace_s) -> float
def runner_breaker_s(timeout_s) -> float          # worst-case grace
def pilot_breaker_s(timeout_s) -> float
def stack_layers(timeout_s, grace_s) -> list[tuple[str, str, float]]
    # innermost first: (name, kind, seconds)
def validate_stack(layers, min_margin=MIN_REPORT_MARGIN_S) -> list[str]
    # [] means coherent; otherwise one human-named violation per bad pair
```

**Steps:**

1. Write the failing test `core/tests/test_timeout_stack.py`:

```python
#!/usr/bin/env python3
"""Timeout-stack sizing authority contract tests (#108 TOOL-037).

core/timeout_stack.py is the ONE place margins are derived; harnesses import
it and never re-derive. These tests pin the derivation (the historical
"+ 120" is worst-case grace + overhead + report margin, not a chosen number)
and the ordering invariant #108's preflight must enforce.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import timeout_stack as ts  # noqa: E402


class Derivation(unittest.TestCase):
    def test_runner_margin_is_derived_not_chosen(self):
        # 120 = worst-case kill grace (60) + delegate overhead (30)
        #       + report margin (30).
        self.assertEqual(ts.RUNNER_BREAKER_MARGIN_S, 120.0)
        self.assertEqual(ts.RUNNER_BREAKER_MARGIN_S,
                         ts.KILL_GRACE_MAX_S + ts.DELEGATE_OVERHEAD_S
                         + ts.MIN_REPORT_MARGIN_S)

    def test_shipped_constants_form_a_coherent_stack(self):
        # Constants drift is now the ONLY way the shipped stack inverts;
        # this is the tripwire. Worst-case legal grace must still nest.
        self.assertEqual(
            ts.validate_stack(ts.stack_layers(1800, ts.KILL_GRACE_MAX_S)), [])

    def test_pilot_breaker_exceeds_runner_breaker_with_margin(self):
        self.assertGreaterEqual(
            ts.pilot_breaker_s(0),
            ts.runner_breaker_s(0) + ts.MIN_REPORT_MARGIN_S)

    def test_float_budgets_stay_float(self):
        # The schema allows timeout_s = 60.5; derivation and the layer
        # report must not truncate it.
        layers = ts.stack_layers(60.5, 5.0)
        self.assertTrue(all(isinstance(s, float) for _, _, s in layers))
        self.assertEqual(layers[1][2], 60.5 + 5.0 + ts.DELEGATE_OVERHEAD_S)


class OrderingInvariant(unittest.TestCase):
    def test_inverted_breaker_is_a_named_violation(self):
        layers = [("inner_self_abort", "self_abort", 100.0),
                  ("outer_breaker", "breaker", 100.0)]
        v = ts.validate_stack(layers)
        self.assertEqual(len(v), 1)
        self.assertIn("outer_breaker", v[0])
        self.assertIn("inner_self_abort", v[0])

    def test_breaker_inside_margin_is_refused(self):
        layers = [("inner_self_abort", "self_abort", 100.0),
                  ("outer_breaker", "breaker",
                   100.0 + ts.MIN_REPORT_MARGIN_S - 1)]
        self.assertEqual(len(ts.validate_stack(layers)), 1)

    def test_breaker_at_exact_margin_passes(self):
        layers = [("inner_self_abort", "self_abort", 100.0),
                  ("outer_breaker", "breaker", 100.0 + ts.MIN_REPORT_MARGIN_S)]
        self.assertEqual(ts.validate_stack(layers), [])

    def test_grace_beyond_delegate_cap_would_invert(self):
        # Why the delegate's grace cap (60) exists: grace 61 puts the
        # delegate ceiling inside the runner breaker's report margin.
        v = ts.validate_stack(ts.stack_layers(1800, ts.KILL_GRACE_MAX_S + 1))
        self.assertTrue(any("runner_breaker" in x for x in v))


if __name__ == "__main__":
    unittest.main()
```

2. Run it, expect fail (module does not exist):

```bash
cd mp_wt && python -m unittest discover -s core/tests -v
# expect: ModuleNotFoundError: No module named 'timeout_stack'
```

3. Create `core/timeout_stack.py`:

```python
#!/usr/bin/env python3
"""Shared timeout-stack sizing authority (#108 TOOL-037).

Every timeout guarding the same dispatch nests inner -> outer:

    delegate self-abort (--timeout)
      < delegate wall ceiling (timeout + kill grace + ~30s overhead)
      < runner wrapper breaker (timeout + RUNNER_BREAKER_MARGIN_S)
      < pilot case breaker (timeout + PILOT_BREAKER_MARGIN_S)

Before this module the margins were literals scattered across delegate.py
(docstring), runner.py (x4) and pilot.py, and budget.timeout_s sized BOTH
the dispatch and the verifier — a slow verifier read as a dispatch-scale
event. Margins are derived here once; harnesses import, never re-derive.

An outer breaker exists to kill a wedged inner layer, but it must give that
layer enough past its worst-case self-abort to write its named error
envelope (status=timeout, exit 124). A breaker that preempts the report
turns a named failure into the "silent death" tree-kill signature of #108.

Python 3.10, standard library only. Pure functions; no I/O.
"""
from __future__ import annotations

# Worst-case seconds the delegate spends AFTER its self-abort fires: graceful
# taskkill, grace wait, force taskkill, job close, proc.wait, reader/stdin
# joins (delegate.py docstring lines 17-18).
DELEGATE_OVERHEAD_S = 30.0

# delegate._validate_config refuses default_kill_grace_seconds > 60. The
# runner sizes its breaker for the worst LEGAL grace because it does not
# read the delegate's config on the dispatch path.
KILL_GRACE_MAX_S = 60.0

# Minimum past the inner worst case an outer breaker must wait so the inner
# layer can report before the breaker fires.
MIN_REPORT_MARGIN_S = 30.0

# Runner wrapper breaker margin. Derived, not chosen:
# 60 + 30 + 30 = 120 — the justification for the historical "+ 120".
RUNNER_BREAKER_MARGIN_S = (KILL_GRACE_MAX_S + DELEGATE_OVERHEAD_S
                           + MIN_REPORT_MARGIN_S)

# Pilot (eval driver) breaker around one whole runner dispatch call.
PILOT_BREAKER_MARGIN_S = 300.0

# Verifier budget default now that budget.timeout_s no longer sizes it
# (the split that fixes the runner.py:1000 vs :1126 double-serve).
DEFAULT_VERIFY_TIMEOUT_S = 600.0


def delegate_ceiling_s(timeout_s, grace_s):
    """Worst-case wall clock one delegate dispatch can consume."""
    return float(timeout_s) + float(grace_s) + DELEGATE_OVERHEAD_S


def runner_breaker_s(timeout_s):
    """Runner wrapper deadline for a dispatch of timeout_s (worst-case grace)."""
    return float(timeout_s) + RUNNER_BREAKER_MARGIN_S


def pilot_breaker_s(timeout_s):
    """Pilot subprocess deadline around one runner dispatch of timeout_s."""
    return float(timeout_s) + PILOT_BREAKER_MARGIN_S


def stack_layers(timeout_s, grace_s):
    """The dispatch timeout stack, innermost first: (name, kind, seconds)."""
    return [
        ("delegate_self_abort", "self_abort", float(timeout_s)),
        ("delegate_ceiling", "ceiling", delegate_ceiling_s(timeout_s, grace_s)),
        ("runner_breaker", "breaker", runner_breaker_s(timeout_s)),
        ("pilot_breaker", "breaker", pilot_breaker_s(timeout_s)),
    ]


def validate_stack(layers, min_margin=MIN_REPORT_MARGIN_S):
    """Return the list of ordering violations; empty means the stack nests.

    A violation is any outer layer whose ceiling is not at least min_margin
    past the layer inside it — the configuration #108's preflight refuses.
    """
    violations = []
    for inner, outer in zip(layers, layers[1:]):
        if outer[2] < inner[2] + min_margin:
            violations.append(
                f"{outer[0]} ({outer[2]}s) must exceed {inner[0]} "
                f"({inner[2]}s) by at least {min_margin}s")
    return violations
```

4. Run, expect pass:

```bash
cd mp_wt && python -m unittest discover -s core/tests -v
```

5. Add the AGENTS.md bullet after the `core/task_schema.py` bullet (AGENTS.md:101-104):

```markdown
- `core/timeout_stack.py` — the one timeout sizing authority (#108 TOOL-037):
  margin constants and derived ceilings (delegate ceiling, runner breaker,
  pilot breaker) plus `validate_stack`. Harnesses import it; nothing
  re-derives a timeout margin.
```

6. Commit (after user confirmation per Global Constraints):

```bash
cd mp_wt && git add core/timeout_stack.py core/tests/test_timeout_stack.py AGENTS.md && git commit -m "TOOL-037 #108: core/timeout_stack.py — single timeout sizing authority with ordering invariant"
```

---

## Task 2 — Schema: `verify_timeout_s` budget field

**Files:**
- Modify: `core/task_schema.py` (line 32, and the comment at lines 28-30)
- Test: `core/tests/test_task_schema.py` (modify — add three tests)

**Interfaces:**
- Consumes: nothing new (schema already validates numeric budget fields).
- Produces: `budget.verify_timeout_s` accepted by `validate_task` as an optional positive number, refused as `TaskSchemaError(field="budget.verify_timeout_s")` for non-numeric/zero/negative/bool. Task 3 relies on this exact field name.

**Steps:**

1. Add failing tests to `core/tests/test_task_schema.py` — append to `ValidTasks`:

```python
    def test_verify_timeout_accepted(self):
        t = base()
        t["budget"] = {"timeout_s": 60, "verify_timeout_s": 120}
        task_schema.validate_task(t)
```

and to `Refusals`:

```python
    def test_string_verify_timeout_refused(self):
        self._refused(lambda t: t.update(budget={"verify_timeout_s": "120"}),
                      "budget.verify_timeout_s")

    def test_zero_verify_timeout_refused(self):
        self._refused(lambda t: t.update(budget={"verify_timeout_s": 0}),
                      "budget.verify_timeout_s")
```

2. Run, expect fail (the two refusal tests fail: unknown budget keys currently pass through per the lines 28-30 comment):

```bash
cd mp_wt && python -m unittest core.tests.test_task_schema -v 2>/dev/null || python -m unittest discover -s core/tests -v
```

3. Edit `core/task_schema.py:28-32` — replace:

```python
# Budget fields this schema understands. Unknown budget keys pass through:
# budgets evolve faster than the schema, and an unknown key changes no
# decision the core makes.
_INT_BUDGET_FIELDS = ("max_dispatches", "max_stagnant")
_NUM_BUDGET_FIELDS = ("timeout_s", "max_preflight_age_s")
```

with:

```python
# Budget fields this schema understands. Unknown budget keys pass through:
# budgets evolve faster than the schema, and an unknown key changes no
# decision the core makes. verify_timeout_s (#108) sizes the verifier
# separately from dispatch timeout_s; before the split one knob served
# both scopes.
_INT_BUDGET_FIELDS = ("max_dispatches", "max_stagnant")
_NUM_BUDGET_FIELDS = ("timeout_s", "verify_timeout_s", "max_preflight_age_s")
```

4. Run, expect pass:

```bash
cd mp_wt && python -m unittest discover -s core/tests -v
```

5. Commit:

```bash
cd mp_wt && git add core/task_schema.py core/tests/test_task_schema.py && git commit -m "TOOL-037 #108: schema accepts budget.verify_timeout_s (verifier/dispatch timeout split, 1/2)"
```

---

## Task 3 — Runner: split the verifier timeout off `budget.timeout_s`

**Files:**
- Modify: `harnesses/kimi-code/runner/runner.py` (`load_task` merge at :218-220; `cmd_verify` at :1126, :1142, :1154 — and grep the whole `cmd_verify` body, including the `OSError` branch at :1156+, for any other verifier-scope `timeout_s` use)
- Test: `harnesses/kimi-code/runner/tests/test_m1_lite.py` (modify the coupled test at :105-108; add `VerifyTimeoutSplit` class)

**Interfaces:**
- Consumes: `timeout_stack.DEFAULT_VERIFY_TIMEOUT_S` via a `_timeout_stack()` loader — **note:** Task 5 adds `_timeout_stack()` to runner.py. To keep tasks independently landable, this task adds the loader too (identical code to Task 5's; whichever lands first keeps it, the second resolves the trivial conflict by keeping the existing block). The loader code is given in this task's step 3.
- Produces: `state["budget"]["verify_timeout_s"]` present for all newly initialized tasks; `cmd_verify` sized by it; verifier-timeout receipts and the error emit carry the key `"verify_timeout_s"` (replacing `"timeout_s"` in those two verifier-scope spots — no consumer reads them: accept reads `passed`/`dispatch_seq`, status reads `passed`/`dispatch_seq`/`verifier_nondiscriminating`).

**Steps:**

1. Write the failing tests. First fix the coupled existing test — `test_m1_lite.py:104-108` currently relies on the double-serve (verifier sized by `timeout_s: 2`) and would HANG ~30s then go green after the split. Replace its `slow_task` construction with:

```python
        # Now a task whose verifier sleeps past a 2s VERIFY budget -> A01.
        # #108: verify is sized by verify_timeout_s, not the dispatch knob;
        # timeout_s stays large to prove the decoupling.
        slow_task = make_task(
            self.tmp, ["{python}", "-c", "import time; time.sleep(30)"],
            budget={"max_dispatches": 4, "max_stagnant": 3,
                    "timeout_s": 600, "verify_timeout_s": 2},
            task_id="t_slow")
```

Then append this class to `test_m1_lite.py`:

```python
class VerifyTimeoutSplit(unittest.TestCase):
    """#108: budget.timeout_s sizes dispatch; budget.verify_timeout_s sizes
    the verifier. Before the split one knob served both scopes
    (runner.py:1000 vs :1126)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m1-vts-")
        self.ws = make_workspace(self.tmp, FIXED)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dispatch_timeout_does_not_constrain_verifier(self):
        # Dispatch budget of 1s must not touch a verify that takes ~2s.
        task = make_task(self.tmp, ["{python}", "check.py"],
                         budget={"max_dispatches": 4, "max_stagnant": 3,
                                 "timeout_s": 1, "verify_timeout_s": 60},
                         task_id="t_split")
        rc, out = run_runner("init", "--workspace", self.ws, "--task", task)
        self.assertEqual(rc, 0, out)
        rc, out = run_runner("verify", "--workspace", self.ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"])

    def test_absent_verify_timeout_falls_back_to_dispatch_timeout(self):
        # States initialized before the split carry no verify_timeout_s in
        # their frozen budget; verify must fall back to timeout_s, never
        # KeyError (Review Focus #1).
        task = make_task(
            self.tmp, ["{python}", "-c", "import time; time.sleep(30)"],
            budget={"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 2},
            task_id="t_legacy")
        rc, out = run_runner("init", "--workspace", self.ws, "--task", task)
        self.assertEqual(rc, 0, out)
        state_file = state_path_for(self.ws) / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["budget"].pop("verify_timeout_s", None)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        rc, out = run_runner("verify", "--workspace", self.ws,
                             "--task", task, timeout=60)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "verifier_timeout")
        self.assertEqual(out["verify_timeout_s"], 2)
```

(`state_path_for` already exists at `test_m1_lite.py:77-83`; the state file is `state.json` per `runner.py:492-493`.)

2. Run, expect fail (split test: verifier times out at 60s default → actually the new-class tests fail on `verify_timeout_s` KeyError/absent key and the missing decoupling; the legacy-fallback test fails because the receipt key is still `timeout_s`):

```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -k VerifyTimeoutSplit -v
# full suite: python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

(Discovery runs the whole suite; `-k` filters. Expect the two new tests + the edited A01 test to fail.)

3. Implement in `runner.py`. First add the loader immediately after `_task_schema()` (after :190):

```python
_TIMEOUT_STACK = None


def _timeout_stack():
    """core/timeout_stack.py — the one timeout sizing authority (#108).

    Same dual-layout resolution as _task_schema: repo checkout
    (harnesses/kimi-code/runner/ -> <repo>/core/) and flat install (sibling
    file shipped by scripts/install.py). A missing module is a broken
    install and refuses loudly rather than silently skipping the invariant.
    """
    global _TIMEOUT_STACK
    if _TIMEOUT_STACK is None:
        import importlib.util
        here = Path(__file__).resolve().parent
        candidates = [here / "timeout_stack.py"]
        if len(here.parents) > 2:
            candidates.append(here.parents[2] / "core" / "timeout_stack.py")
        for cand in candidates:
            if cand.is_file():
                spec = importlib.util.spec_from_file_location("timeout_stack", str(cand))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _TIMEOUT_STACK = mod
                break
        else:
            raise SystemExit(_emit({"error": "timeout_stack_module_missing"}, 4))
    return _TIMEOUT_STACK
```

In `load_task`, after the existing merge (:218-220), add:

```python
    task["budget"].setdefault("verify_timeout_s",
                              _timeout_stack().DEFAULT_VERIFY_TIMEOUT_S)
```

In `cmd_verify`, before the `subprocess.run` at :1123-1127, add:

```python
    # #108: the verifier is sized by verify_timeout_s. States initialized
    # before the split carry no such key — fall back to timeout_s, the
    # value that sized the verifier when those states were written.
    verify_timeout_s = state["budget"].get("verify_timeout_s",
                                           state["budget"]["timeout_s"])
```

and change :1126 to `timeout=verify_timeout_s,`. In the `TimeoutExpired` handler change the receipt key at :1142 to `"verify_timeout_s": verify_timeout_s,` and the emit key at :1154 likewise. Grep `cmd_verify`'s body for any remaining verifier-scope `"timeout_s"` (including the `OSError` branch at :1156+) and apply the same rename there; do NOT touch dispatch-scope uses (:985 journal record, :1000 dispatch call) or status/accept uses (:1237, :1507) — those are Task 5.

4. Run, expect pass:

```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

5. Commit:

```bash
cd mp_wt && git add harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/test_m1_lite.py && git commit -m "TOOL-037 #108: split verifier timeout off budget.timeout_s (verify_timeout_s, legacy-state fallback)"
```

---

## Task 4 — Delegate: `--print-timeout-knobs` discovery report

**Files:**
- Modify: `harnesses/kimi-code/delegate/delegate.py` (new `_print_timeout_knobs()` after `load_config` at :370-387; short-circuit in `main()` at :1455-1456; docstring :17-18)
- Test: `harnesses/kimi-code/delegate/tests/test_timeout_knobs.py` (create)

**Interfaces:**
- Consumes: `load_config()` / `_resolve_config_path()` / `ConfigError` (existing).
- Produces: stdout JSON `{knobs_valid: bool, config_path: str, max_timeout_seconds, default_kill_grace_seconds, agents: {name: {default_timeout, minimum_timeout, maximum_timeout}}, error?: str}`, exit 0 when valid, 64 (`EXIT_INVALID`) when not. Task 5's `cmd_timeouts` consumes exactly this shape. The pre-parse short-circuit means the flag works WITHOUT `--workspace`/`--agent` (which argparse otherwise requires at :1462-1468).

**Steps:**

1. Write the failing test `harnesses/kimi-code/delegate/tests/test_timeout_knobs.py`:

```python
#!/usr/bin/env python3
"""--print-timeout-knobs contract tests (#108 TOOL-037).

The runner owns the stack report, but the delegate owns its config — so the
delegate reports its own timeout knobs. A config that fails validation is
still reported (knobs_valid: false) so the caller can NAME the violation
instead of crashing on it (Review Focus #3).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DELEGATE_DIR = Path(__file__).resolve().parents[1]
DELEGATE = DELEGATE_DIR / "delegate.py"
EXAMPLE_CONFIG = DELEGATE_DIR / "agents.example.json"


def run_knobs(env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(DELEGATE), "--print-timeout-knobs"],
                       capture_output=True, text=True, env=env, timeout=30)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


class TimeoutKnobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="knobs-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_example_config_reports_knobs(self):
        rc, out = run_knobs({"DELEGATE_CONFIG": str(EXAMPLE_CONFIG)})
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["knobs_valid"])
        self.assertEqual(out["default_kill_grace_seconds"], 5)
        self.assertEqual(out["max_timeout_seconds"], 7200)
        self.assertEqual(out["agents"]["example-coder"]["default_timeout"], 1800)

    def test_invalid_config_is_reported_not_crash(self):
        # Grace above the delegate's own cap (60) would invert the stack
        # against the runner breaker; the report must surface it by name.
        # (_validate_config checks grace at :417-421, before the agents
        # loop, so this minimal agent is never validated.)
        bad = Path(self.tmp) / "bad.json"
        bad.write_text(json.dumps({
            "allowed_workspace_roots": [self.tmp],
            "max_task_bytes": 1024, "max_timeout_seconds": 7200,
            "default_kill_grace_seconds": 5000,
            "max_stdout_bytes": 1024, "max_stderr_bytes": 1024,
            "agents": {"a": {"command": ["x"], "prompt_delivery": "stdin",
                             "default_timeout": 60, "minimum_timeout": 1,
                             "maximum_timeout": 120}}}), encoding="utf-8")
        rc, out = run_knobs({"DELEGATE_CONFIG": str(bad)})
        self.assertEqual(rc, 64, out)
        self.assertFalse(out["knobs_valid"])
        self.assertIn("default_kill_grace_seconds", out["error"])
        self.assertEqual(out["default_kill_grace_seconds"], 5000)

    def test_missing_config_is_reported_not_crash(self):
        rc, out = run_knobs(
            {"DELEGATE_CONFIG": str(Path(self.tmp) / "nope.json")})
        self.assertEqual(rc, 64, out)
        self.assertFalse(out["knobs_valid"])
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
```

2. Run, expect fail (argparse error — required args missing; envelope `invalid` on stdout, exit 64, no `knobs_valid`):

```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

3. Implement in `delegate.py`. Add after `load_config` (after :387):

```python
def _print_timeout_knobs():
    """#108: emit this delegate's resolved timeout knobs as JSON on stdout.

    Discovery companion for `runner.py timeouts`: the runner owns the stack
    report, but the delegate owns its config, so it reports its own knobs.
    A config that fails validation is still reported (knobs_valid: false)
    so the caller can name the violation instead of crashing on it.
    """
    try:
        cfg = load_config()
        valid, error = True, None
    except ConfigError as e:
        valid, error = False, str(e)
        try:
            cfg = json.loads(_resolve_config_path().read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    out = {
        "knobs_valid": valid,
        "config_path": str(_resolve_config_path()),
        "max_timeout_seconds": cfg.get("max_timeout_seconds"),
        "default_kill_grace_seconds": cfg.get("default_kill_grace_seconds"),
        "agents": {name: {k: a.get(k) for k in
                          ("default_timeout", "minimum_timeout",
                           "maximum_timeout")}
                   for name, a in (cfg.get("agents") or {}).items()
                   if isinstance(a, dict)},
    }
    if error:
        out["error"] = error
    sys.stdout.write(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return 0 if valid else EXIT_INVALID
```

(Note: `_resolve_config_path()` can raise `ConfigError` on a NUL-byte `DELEGATE_CONFIG` — wrap the `str(_resolve_config_path())` for `config_path` in the same try or accept the raise; the ConfigError propagating out of `main`'s short-circuit prints a traceback, so catch it: set `config_path` to `None` in the except path by resolving it inside the first `try` before `load_config()`. Implement it that way: `path = None` then `try: path = _resolve_config_path(); cfg = load_config() ...`.)

Add the short-circuit as the first statement of `main()` (:1455-1456), BEFORE `install_signal_handlers()` and before argparse is built (argparse would otherwise demand `--workspace` and `--agent`/`--model`):

```python
def main():
    if "--print-timeout-knobs" in sys.argv[1:]:
        # #108 discovery report: no dispatch, so none of the dispatch
        # arguments apply; short-circuit before they are required.
        sys.exit(_print_timeout_knobs())
    install_signal_handlers()
```

Update the docstring at :17-18 to name the authority (replace those two lines):

```python
Actual wall-clock ceiling ≈ timeout + default_kill_grace_seconds + overhead
(taskkill invocations, proc.wait, reader joins) ≈ timeout + grace + ~30 s.
The margin constants behind this estimate live in core/timeout_stack.py
(#108) — that module is the sizing authority; update it, not this prose.
```

4. Run, expect pass:

```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
```

5. Commit:

```bash
cd mp_wt && git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/tests/test_timeout_knobs.py && git commit -m "TOOL-037 #108: delegate --print-timeout-knobs discovery report (validates and reports, never crashes)"
```

---

## Task 5 — Runner: derive every breaker/ceiling from the authority + `timeouts` preflight command + installer

**Files:**
- Modify: `harnesses/kimi-code/runner/runner.py` (`_timeout_stack()` loader if Task 3 didn't land it; `run_delegate` deadline :721 + docstring :714-716; `journal_ceiling` :910; accept gate :1236-1238; status ceiling :1529; `load_task` tripwire after :220; new `cmd_timeouts`; `main()` argparse at :1634-1648)
- Modify: `scripts/install.py` (`RUNNER_FILES` :40-42, its comment :35-39, `REQUIRED_AFTER_INSTALL` :45)
- Test: `harnesses/kimi-code/runner/tests/test_timeout_coherence.py` (create)

**Interfaces:**
- Consumes: `core/timeout_stack.py` (Task 1) via `_timeout_stack()`; `delegate.py --print-timeout-knobs` JSON (Task 4); `resolve_delegate` (:699-705).
- Produces: CLI `runner.py timeouts --task <f> [--delegate <path>]` → JSON `{task_id, budget, layers: [{layer, kind, seconds}], constants, delegate, delegate_knobs, coherent: bool, violations: [str]}`, exit 0 coherent / exit 1 with named violations. All four runner ceilings equal `runner_breaker_s(timeout_s)` — `cmd_status`'s `orphaned_dispatch_ids` and the accept gate's live-window now share the dispatch breaker's derivation (consumers: SKILL.md resume protocol reads status; no key renames).

**Steps:**

1. Write the failing test `harnesses/kimi-code/runner/tests/test_timeout_coherence.py`:

```python
#!/usr/bin/env python3
"""Timeout-stack coherence at the runner boundary (#108 TOOL-037).

Pins: every runner ceiling is the DERIVED breaker (a ceiling derived
anywhere else can mark a legally-alive dispatch orphaned — Review Focus
#2); `runner.py timeouts` is the discoverability surface and the preflight
that refuses inverted stacks; the flat install resolves the authority or
refuses loudly (Review Focus #5).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # harnesses/kimi-code
RUNNER = ROOT / "runner" / "runner.py"
DELEGATE = ROOT / "delegate" / "delegate.py"
REPO_ROOT = ROOT.parents[1]
EXAMPLE_CONFIG = ROOT / "delegate" / "agents.example.json"

sys.path.insert(0, str(REPO_ROOT / "core"))
import timeout_stack as ts  # noqa: E402


def run_runner(*argv, env_extra=None, timeout=120):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=timeout)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, **overrides):
    task = {"task_id": "t1", "prompt": "Fix the bug.",
            "features": {"bounded": True},
            "scope": ["math_utils.py"],
            "verifier": {"argv": ["{python}", "check.py"]},
            "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60}}
    task.update(overrides)
    p = Path(tmp) / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp):
    ws = Path(tmp) / "ws"
    ws.mkdir()
    (ws / "math_utils.py").write_text(
        "def sum_to_n(n):\n    return sum(range(1, n + 1))\n", encoding="utf-8")
    (ws / "check.py").write_text("print('PASS')\n", encoding="utf-8")
    return str(ws)


def state_root_for(ws):
    import hashlib
    import re
    ws_res = Path(ws).resolve()
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", ws_res.name)
    digest = hashlib.sha256(str(ws_res).encode("utf-8")).hexdigest()[:8]
    return ws_res.parent / ".runner-state" / f"{key}-{digest}"


class TimeoutsCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tc-")
        self.task = make_task(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_happy_path_reports_the_whole_stack(self):
        rc, out = run_runner(
            "timeouts", "--task", self.task, "--delegate", str(DELEGATE),
            env_extra={"DELEGATE_CONFIG": str(EXAMPLE_CONFIG)})
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["coherent"], out)
        self.assertEqual(out["violations"], [])
        seconds = [l["seconds"] for l in out["layers"]]
        self.assertEqual(seconds, sorted(seconds))
        self.assertEqual([l["layer"] for l in out["layers"]],
                         ["delegate_self_abort", "delegate_ceiling",
                          "runner_breaker", "pilot_breaker"])
        self.assertEqual(out["delegate_knobs"]["default_kill_grace_seconds"], 5)
        self.assertEqual(out["budget"]["verify_timeout_s"],
                         ts.DEFAULT_VERIFY_TIMEOUT_S)

    def test_inverted_delegate_config_is_refused_by_name(self):
        bad = Path(self.tmp) / "bad-agents.json"
        bad.write_text(json.dumps({
            "allowed_workspace_roots": [self.tmp],
            "max_task_bytes": 1024, "max_timeout_seconds": 7200,
            "default_kill_grace_seconds": 5000,
            "max_stdout_bytes": 1024, "max_stderr_bytes": 1024,
            "agents": {"a": {"command": ["x"], "prompt_delivery": "stdin",
                             "default_timeout": 60, "minimum_timeout": 1,
                             "maximum_timeout": 120}}}), encoding="utf-8")
        rc, out = run_runner(
            "timeouts", "--task", self.task, "--delegate", str(DELEGATE),
            env_extra={"DELEGATE_CONFIG": str(bad)})
        self.assertEqual(rc, 1, out)
        self.assertFalse(out["coherent"])
        self.assertTrue(any("delegate_config_invalid" in v
                            for v in out["violations"]), out)


class BreakerCeilingParity(unittest.TestCase):
    """Journal hand-writing idiom from test_dispatch_journal.py:123-150."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tcp-")
        self.ws = make_workspace(self.tmp)
        self.task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", self.ws, "--task", self.task)
        self.assertEqual(rc, 0, out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_open(self, dispatch_id, age_s):
        journal = state_root_for(self.ws) / "journal.jsonl"
        at = time.strftime("%Y-%m-%dT%H:%M:%S",
                           time.localtime(time.time() - age_s))
        with open(journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "dispatch_open", "task_id": "t1",
                "dispatch_id": dispatch_id, "agent": "a",
                "timeout_s": 60, "runner_pid": 0, "at": at}) + "\n")

    def test_status_orphan_ceiling_is_the_derived_breaker(self):
        self._write_open("d-outside", ts.runner_breaker_s(60) + 5)
        self._write_open("d-inside", ts.runner_breaker_s(60) - 5)
        rc, out = run_runner("status", "--workspace", self.ws)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["orphaned_dispatch_ids"], ["d-outside"])
        self.assertIn("d-inside", out["open_journal_ids"])

    def test_named_timeout_envelope_not_breaker_preemption(self):
        # Acceptance #2: a wedged inner job yields the inner layer's named
        # error. The fake delegate emits its own timeout envelope promptly;
        # the runner must pass it through, never its synthetic
        # delegate_wrapper_timeout.
        env = {"FAKE_WORKER_MODE": "timeout"}
        rc, out = run_runner("dispatch", "--workspace", self.ws,
                             "--task", self.task,
                             "--delegate",
                             str(ROOT / "runner" / "tests" / "fake_worker.py"),
                             env_extra=env)
        self.assertEqual(out["envelope_status"], "timeout", out)
        self.assertNotEqual(out.get("envelope_status"), "delegate_wrapper_timeout")


class FlatInstallLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tcf-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _flat(self, with_stack=True):
        flat = Path(self.tmp) / "flat"
        flat.mkdir(exist_ok=True)
        shutil.copy2(RUNNER, flat / "runner.py")
        shutil.copy2(REPO_ROOT / "core" / "task_schema.py",
                     flat / "task_schema.py")
        if with_stack:
            shutil.copy2(REPO_ROOT / "core" / "timeout_stack.py",
                         flat / "timeout_stack.py")
        return flat

    def test_flat_install_layout_resolves_timeout_stack(self):
        flat = self._flat(with_stack=True)
        task = make_task(self.tmp)
        r = subprocess.run([sys.executable, str(flat / "runner.py"),
                            "lane", "--task", task],
                           capture_output=True, text=True, timeout=120)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("Traceback", r.stderr)

    def test_flat_install_missing_module_refuses_loudly(self):
        flat = self._flat(with_stack=False)
        task = make_task(self.tmp)
        r = subprocess.run([sys.executable, str(flat / "runner.py"),
                            "lane", "--task", task],
                           capture_output=True, text=True, timeout=120)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        self.assertEqual(r.returncode, 4, out)
        self.assertEqual(out["error"], "timeout_stack_module_missing")


if __name__ == "__main__":
    unittest.main()
```

2. Run, expect fail (`timeouts` is not a subcommand → argparse exit 2 with empty stdout; parity test fails only after Task 3's verify default is absent from... actually `budget.verify_timeout_s` assertion fails pre-Task-3; run after Tasks 1-4):

```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

3. Implement in `runner.py`:

   a. Ensure `_timeout_stack()` exists (Task 3 step 3 code; skip if present).
   b. `run_delegate` :721 — replace `deadline = time.monotonic() + timeout_s + 120` with:

```python
    deadline = time.monotonic() + _timeout_stack().runner_breaker_s(timeout_s)
```

   and update the docstring lines :714-716 to: "past the derived breaker (`core/timeout_stack.runner_breaker_s`: worst-case delegate ceiling + report margin, #108) the child is killed and a timeout envelope returned (the delegate enforces the same ceiling on its side)."
   c. :910 — replace `journal_ceiling = state["budget"]["timeout_s"] + 120` with:

```python
    journal_ceiling = _timeout_stack().runner_breaker_s(state["budget"]["timeout_s"])
```

   d. :1236-1238 — replace the `_ceiling` expression with:

```python
    _ceiling = _timeout_stack().runner_breaker_s(
        state.get("budget", {}).get("timeout_s", DEFAULT_BUDGET["timeout_s"]))
```

   e. :1529 — replace `ceiling = timeout_s + 120` with:

```python
    ceiling = _timeout_stack().runner_breaker_s(timeout_s)
```

   Also update the adjacent comments at :906-909 and :1229-1232 that say "timeout + 120s" to reference the derived breaker (`runner_breaker_s`, #108).
   f. `load_task` tripwire, immediately after the `verify_timeout_s` setdefault from Task 3:

```python
    # #108 tripwire: margins are constants only this repo can edit, so an
    # inverted stack means a code change broke the derivation — refuse at
    # every command boundary, not in production.
    _stack = _timeout_stack()
    _violations = _stack.validate_stack(
        _stack.stack_layers(task["budget"]["timeout_s"],
                            _stack.KILL_GRACE_MAX_S))
    if _violations:
        raise SystemExit(_emit({"error": "timeout_stack_inverted",
                                "violations": _violations}, 3))
```

   g. New command — add `cmd_timeouts` next to `cmd_status`:

```python
def cmd_timeouts(args):
    """#108: the one place every timeout knob is discoverable, and the
    preflight that refuses an inverted stack (exit 1 with named
    violations). After the derivation rule, no task-file input can invert
    the stack; the remaining vectors are constants drift (tripwired in
    load_task) and delegate config (probed here)."""
    task = load_task(args.task)
    stack = _timeout_stack()
    timeout_s = task["budget"]["timeout_s"]
    delegate_py = resolve_delegate(args.delegate)
    try:
        r = subprocess.run([sys.executable, delegate_py,
                            "--print-timeout-knobs"],
                           capture_output=True, text=True, timeout=15)
        knobs = json.loads(r.stdout) if r.stdout.strip() else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        knobs = {"knobs_valid": False, "error": f"knobs probe failed: {e}"}
    grace = knobs.get("default_kill_grace_seconds")
    if isinstance(grace, bool) or not isinstance(grace, (int, float)):
        grace = stack.KILL_GRACE_MAX_S
    violations = stack.validate_stack(stack.stack_layers(timeout_s, grace))
    # A legal config edit must not invert the stack either: also check the
    # worst grace the delegate's own validation permits.
    violations += stack.validate_stack(
        stack.stack_layers(timeout_s, stack.KILL_GRACE_MAX_S))
    if not knobs.get("knobs_valid", False):
        violations.append("delegate_config_invalid: "
                          + str(knobs.get("error", "unknown")))
    return _emit({
        "task_id": task["task_id"],
        "budget": task["budget"],
        "layers": [{"layer": n, "kind": k, "seconds": s}
                   for n, k, s in stack.stack_layers(timeout_s, grace)],
        "constants": {
            "DELEGATE_OVERHEAD_S": stack.DELEGATE_OVERHEAD_S,
            "KILL_GRACE_MAX_S": stack.KILL_GRACE_MAX_S,
            "MIN_REPORT_MARGIN_S": stack.MIN_REPORT_MARGIN_S,
            "RUNNER_BREAKER_MARGIN_S": stack.RUNNER_BREAKER_MARGIN_S,
            "PILOT_BREAKER_MARGIN_S": stack.PILOT_BREAKER_MARGIN_S,
            "DEFAULT_VERIFY_TIMEOUT_S": stack.DEFAULT_VERIFY_TIMEOUT_S,
        },
        "delegate": delegate_py,
        "delegate_knobs": knobs,
        "coherent": not violations,
        "violations": violations,
    }, 0 if not violations else 1)
```

   h. `main()` argparse (:1634-1648) — after the journal block add:

```python
    # #108: timeout-stack discovery + inversion preflight.
    p = sub.add_parser("timeouts")
    p.add_argument("--task", required=True)
    p.add_argument("--delegate", default=None)
```

   and add `"timeouts": cmd_timeouts,` to the dispatch dict at :1644-1647.

4. Edit `scripts/install.py` — `RUNNER_FILES` (:40-42) becomes:

```python
RUNNER_FILES = [(KIMI, "runner", "runner.py"), (KIMI, "runner", "pilot.py"),
                (ROOT, "core", "task_schema.py"),
                (ROOT, "core", "timeout_stack.py"),
                (ROOT, "evals", "pricing.yaml")]
```

   extend the comment above it (:35-39) with "timeout_stack.py is the shared sizing authority (#108): the installed runner resolves it as a sibling (see runner._timeout_stack).", and `REQUIRED_AFTER_INSTALL` (:45) becomes:

```python
REQUIRED_AFTER_INSTALL = ["runner.py", "delegate.py", "catalog.py",
                          "task_schema.py", "timeout_stack.py", "pricing.yaml"]
```

5. Run, expect pass (both runner suites + the CI smoke equivalent):

```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v && python -c "import sys; sys.path.insert(0, 'scripts'); import install; missing = [n for n in install.REQUIRED_AFTER_INSTALL if not (install.KIMI / 'delegate' / n).is_file() and not (install.KIMI / 'runner' / n).is_file() and not (install.ROOT / 'core' / n).is_file() and not (install.ROOT / 'evals' / n).is_file()]; print('missing:', missing); sys.exit(1 if missing else 0)"
```

6. Commit:

```bash
cd mp_wt && git add harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/test_timeout_coherence.py scripts/install.py && git commit -m "TOOL-037 #108: derive all runner ceilings from core/timeout_stack; add runner timeouts preflight command; ship module in flat install"
```

---

## Task 6 — Pilot: derive the case breaker

**Files:**
- Modify: `harnesses/kimi-code/runner/pilot.py` (new `_timeout_stack()` loader after :34; dispatch timeout at :338-339)
- Test: `harnesses/kimi-code/runner/tests/test_timeout_coherence.py` (append `PilotBreaker` class)

**Interfaces:**
- Consumes: `core/timeout_stack.pilot_breaker_s` (Task 1).
- Produces: no signature change; `run_runner(..., timeout=...)` at :338-339 now receives the derived breaker. `run_runner`'s 900s default (:69) covers init/verify/accept calls and is NOT part of the dispatch nesting chain — leave it, note it in the module docstring if touched.

**Steps:**

1. Append the failing test to `test_timeout_coherence.py`:

```python
class PilotBreaker(unittest.TestCase):
    def test_pilot_resolves_the_authority_and_nests(self):
        # pilot.py is import-safe (module level defines paths/constants
        # only). This pins that pilot's loader resolves the same authority
        # and that its breaker nests outside the runner's with margin.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pilot", str(ROOT / "runner" / "pilot.py"))
        pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pilot)
        stack = pilot._timeout_stack()
        # importlib loads a fresh module OBJECT, so identity with the test's
        # own import is wrong to assert; equality of the derived constants is
        # what "same authority" means here.
        self.assertEqual(stack.RUNNER_BREAKER_MARGIN_S,
                         ts.RUNNER_BREAKER_MARGIN_S)
        self.assertEqual(stack.PILOT_BREAKER_MARGIN_S,
                         ts.PILOT_BREAKER_MARGIN_S)
        self.assertGreaterEqual(
            stack.pilot_breaker_s(600),
            stack.runner_breaker_s(600) + stack.MIN_REPORT_MARGIN_S)
```

(The call site itself — `pilot.py:339` — is verified by reading; a behavioral pin needs a full pilot run, which spends real tokens and is excluded.)

2. Run, expect fail (`pilot._timeout_stack` does not exist → AttributeError):

```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

3. Implement in `pilot.py` — after the path constants (:27-34):

```python
_TIMEOUT_STACK = None


def _timeout_stack():
    """core/timeout_stack.py — the one timeout sizing authority (#108).
    Dual layout like runner._timeout_stack: flat install (sibling file) or
    repo checkout (REPO_ROOT/core/)."""
    global _TIMEOUT_STACK
    if _TIMEOUT_STACK is None:
        import importlib.util
        here = Path(__file__).resolve().parent
        candidates = [here / "timeout_stack.py",
                      REPO_ROOT / "core" / "timeout_stack.py"]
        for cand in candidates:
            if cand.is_file():
                spec = importlib.util.spec_from_file_location(
                    "timeout_stack", str(cand))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _TIMEOUT_STACK = mod
                break
        else:
            raise RuntimeError("timeout_stack.py not found (broken install)")
    return _TIMEOUT_STACK
```

and change :338-339 to:

```python
        rc, disp = run_runner(*disp_argv, timeout=_timeout_stack().pilot_breaker_s(
            task["budget"]["timeout_s"]))
```

4. Run, expect pass:

```bash
cd mp_wt && python -m unittest discover -s harnesses/kimi-code/runner/tests -v
```

5. Commit:

```bash
cd mp_wt && git add harnesses/kimi-code/runner/pilot.py harnesses/kimi-code/runner/tests/test_timeout_coherence.py && git commit -m "TOOL-037 #108: pilot case breaker derived from core/timeout_stack"
```

---

## Final verification (after Task 6)

```bash
cd mp_wt && for s in harnesses/kimi-code/delegate/tests harnesses/codex/delegate/tests harnesses/codex/skill/model-proctor/tests harnesses/kimi-code/runner/tests core/tests scripts/tests evals/tests harnesses/zcode/tests; do python -m unittest discover -s "$s" || exit 1; done
```

All 8 CI suites green on Windows. Then `git log --oneline -6` shows the six `TOOL-037 #108` commits.

## Self-review

- **Spec coverage:** acceptance (1) "preflight refuses inverted stack" → `cmd_timeouts` exit 1 (Task 5, `test_inverted_delegate_config_is_refused_by_name`) + `load_task` tripwire for constants drift + delegate's existing grace cap now justified by `test_grace_beyond_delegate_cap_would_invert` (Task 1). Acceptance (2) "named error, not tree-kill" → margin derivation guarantees the delegate's timeout envelope always beats the runner breaker; pinned by `test_named_timeout_envelope_not_breaker_preemption` (Task 5). "One sizing authority" → Task 1 + all call sites derived (Tasks 3, 5, 6). "Discoverable from one place" → `runner.py timeouts` (Task 5) + delegate self-report (Task 4). Double-serve fix → Tasks 2-3.
- **Placeholder scan:** every step contains the real code/commands; no TBDs. The one deliberate judgment call left to the implementer is the `config_path` None-handling in `_print_timeout_knobs`, spelled out inline in Task 4 step 3.
- **Type consistency across tasks:** `stack_layers` returns `(str, str, float)` triples consumed by `validate_stack` (Task 1) and serialized as `{"layer", "kind", "seconds"}` in `cmd_timeouts` (Task 5) — matches the test's `l["seconds"]`/`l["layer"]` reads. `_timeout_stack()` is defined identically in Tasks 3 and 5 with an explicit conflict-resolution note. `verify_timeout_s` flows schema (Task 2) → `load_task` default (Task 3) → state budget → `cmd_verify` + receipts (Task 3) → `timeouts` report assertion (Task 5). `--print-timeout-knobs` JSON shape produced in Task 4 == consumed in Task 5 (`knobs_valid`, `default_kill_grace_seconds`, `error`).
- **Review-focus pinning:** #1 stale state → Task 3 legacy-fallback test; #2 ceiling disagreement → Task 5 `BreakerCeilingParity`; #3 malformed config → Tasks 4/5 named-violation tests; #4 float budgets → Task 1 float test; #5 flat-install drift → Task 5 flat-layout tests + install.py + CI smoke command in step 5.
- **Behavior compatibility:** numeric ceilings are unchanged for all legal configs (derived 120 == literal 120; pilot 300 == literal 300); the only user-visible change is the verifier timeout defaulting to 600 instead of `timeout_s` for newly initialized tasks — intended by the ticket.

## Inventory corrections found while surveying

1. **Issue body vs inventory scope (clarification, not error):** #108's measured case is the fsn weekly pipeline's bridge (no-timeout `subprocess.run`, 3h runtime, 30min breaker), fetched live from GitHub. The inventory's §3e correctly scopes the mp-side chain; the fsn bridge is out of this repo's scope and noted as such in Spec.
2. **Inventory missed a test collision:** `test_m1_lite.py:104-108` (`A01VerifierTimeout.test_timeout_writes_refused_receipt_and_blocks_stale_green`) relies on the coupled knob — it sets `budget.timeout_s: 2` to time out the VERIFIER. After the split this test would hang ~30s and then go green. Task 3 step 1 fixes it in place; without that fix the suite goes red for a non-obvious reason.
3. **Inventory §3b incomplete on verifier-scope `timeout_s`:** besides `runner.py:1000` (dispatch) and `:1126` (verifier subprocess), the verifier-timeout receipt and error emit also carry the key `"timeout_s"` at `runner.py:1142` and `:1154` — both must be renamed to `verify_timeout_s` in Task 3 or the receipt lies about which budget fired.
4. **Installer coupling not in the inventory:** a new core module silently breaks the flat install at `C:\Tools\model-proctor` unless `scripts/install.py:40-42,45` is updated — the CI smoke (`ci.yml:42-47`) checks only that required names exist in source dirs, not that `RUNNER_FILES` copies them. Task 5 step 4 covers it.
5. **Verified exact:** `delegate.py:17-18` (wall-ceiling prose), `:417-421` (grace cap 60), `:664-683` (`validate_timeout`); `runner.py:106` (DEFAULT_BUDGET), `:218-220` (budget merge), `:721`, `:910`, `:1237-1238`, `:1529` (four `+ 120` sites), `:1508` (stall), `:957` (preflight age); `pilot.py:69` (900s default), `:339` (`+ 300`); `core/task_schema.py:31-32` (budget field tuples); `_task_schema` loader at `runner.py:161-190` as the importlib idiom to mirror; journal `journal.jsonl` / state `state.json` filenames (`runner.py:492-501`).
