# Dispatch Budgets Never Span the Payload's Lifetime — Implementation Plan

**Planning status resolution:** this document IS the planning that mp#104 / TOOL-033 required. Executing it closes the ticket's planning phase; no separate design sign-off is needed.

> Executors: use the `subagent-driven-development` skill. Each task below is one subagent dispatch (or one inline TDD cycle), executed in order, each ending in its own commit.

## Goal

Give the runner's budget clock phase semantics so no budget ever spans a detached payload's lifetime: (1) split the verifier off `budget.timeout_s` onto its own `verify_timeout_s`, (2) add a per-agent `on_timeout: kill_tree|report_detached` config knob, (3) add the delegate envelope status `payload_running_detached` — budget expiry on a detached payload is a report, never a kill — and (4) teach the runner's dispatch/accept/status surfaces the detached state.

## Architecture

The runner's budget model becomes three phases: **launch** (payload in dispatcher custody, clocked by `timeout_s`, delegate-enforced with the runner's `+120` wrapper backstop), **verify** (clocked by the new `verify_timeout_s`, never borrowing the dispatch clock), and **monitor** (a reported-detached payload — clock-free by design; the journal pair closes, state carries a `detached` marker, and `accept` refuses until an explicit, counted override). The delegate remains the single launch authority; the new envelope state flows through the existing stdout-JSON contract, so runner changes are additive reads.

## Tech Stack

Python 3.10, stdlib only (control-plane rule, AGENTS.md:115-116); stdlib `unittest`, one discovery dir per suite; CI `windows-latest` only. No new dependencies.

## Spec

- Inventory: `mp_inventory_103-110.md` at the session root, section "#104 / TOOL-033 — budget spans payload lifetime" plus section 3b (runner budgets) and 3e (nesting order).
- Ticket: GitHub `dachent/model_proctor` issue #104 (`[TOOL-033]`).
- Verified surfaces (all re-read in the worktree `mp_wt` @ 8982e24):
  - `harnesses/kimi-code/runner/runner.py:106` (`DEFAULT_BUDGET`), `:218-220` (budget merge in `load_task`), `:839` (init stores merged `task["budget"]` on state), `:999-1001` (dispatch timeout), `:1126` (verifier `subprocess.run(timeout=state["budget"]["timeout_s"])` — the double-serve defect), `:1142,:1154` (verifier_timeout receipt/emit echo `timeout_s`), `:721,:735-757` (wrapper deadline `timeout_s + 120`, `proc.kill()`), `:1003-1041` (`cmd_dispatch` envelope handling + failure append + journal finished + output), `:1236-1252` (accept in-flight gate), `:1299-1319` (`--allow-zero-dispatch` override idiom), `:1489-1576` (`cmd_status`), `:1596-1640` (argparse).
  - `harnesses/kimi-code/delegate/delegate.py:49-53` (exit codes — note: these are EXIT codes, not the status vocabulary; statuses are string literals at `_make_result` call sites), `:1187-1188` (deadline anchored at Popen), `:1249-1265` (wait loop), `:1305-1337` (timeout branch — kill at `:1307`), `:1403-1427` (`_make_result`), `:528-534` (`allow_breakaway` validation), `:444-447` (model-template breakaway guard), `:344-367` (`resolve_model_agent` forces `allow_breakaway` off at `:365`), `:95-107` (documented breakaway residual: taskkill `/T` reaches escaped payloads while the intermediate parent lives).
  - `core/task_schema.py:31-32` (`_INT_BUDGET_FIELDS`, `_NUM_BUDGET_FIELDS`).
  - `harnesses/kimi-code/delegate/agents.example.json` (tracked; `agents.json` is gitignored).
  - Tests: `harnesses/kimi-code/runner/tests/{fake_worker.py,test_m1_lite.py:95-120,test_dispatch_journal.py,test_task_schema_gate.py}`, `harnesses/kimi-code/delegate/tests/{test_delegate.py:237-343 (helpers + `_ENVELOPE_KEYS`),:1058-1090 (timeout lifecycle idiom),test_breakaway.py}`.

## Global Constraints

- Stdlib-only Python 3.10; `windows-latest` CI; unittest discover per suite.
- Envelope/exit-code vocabulary is shared across delegate, runner, cascade (frozen), and evals — additive only: new status, new exit code, new keys; nothing renamed or removed except where a task says so explicitly.
- The live install `C:\Tools\model-proctor\` and the gitignored live `agents.json` are OUT OF SCOPE for this repo change; the plan changes the tracked example and validation only.
- `cascade/` is a frozen research artifact — do not modify it. `harnesses/codex/` is a separate adapter with its own `_Deadline` — out of scope.
- Every commit message carries `(TOOL-033, #104)`; `git add` explicit paths only; no git mutations beyond the per-task commits without user confirmation (AGENTS.md:121).
- Per-suite test commands (run from the repo root of the worktree):
  - `python -m unittest discover -s harnesses/kimi-code/delegate/tests -v`
  - `python -m unittest discover -s harnesses/kimi-code/runner/tests -v`
  - `python -m unittest discover -s core/tests -v`
  - Full gate (CI parity, run before the final commit): all eight suites — the three above plus `harnesses/kimi-code/cascade/tests`, `harnesses/codex/delegate/tests`, `scripts/tests`, `evals/tests`, `harnesses/zcode/tests`.

## Review Focus

Five input classes / failure modes the spec implies but no task's tests exercise — most likely to bite first. Each names its owning task for the pinning evidence (test or code-read):

1. **Wrapper-deadline race against a wedged detached delegate.** A healthy delegate in `report_detached` mode exits at its own `--timeout`, far inside the runner's `timeout_s + 120` wrapper deadline (`runner.py:721`). But if the delegate *wedges* after budget expiry (before emitting the envelope), the wrapper still `proc.kill()`s it (`runner.py:738,:754`), the delegate's job handle closes, and KILL_ON_JOB_CLOSE reaps whatever never escaped. That is correct-by-design (containment fallback), but no test exercises a wedged-detached delegate. Owner: Task 4, code-read evidence — the wrapper path is unchanged and the detached envelope is emitted by the delegate before the wrapper deadline can fire in every non-wedged case.
2. **Interruption must still kill.** `on_timeout: report_detached` changes only the `timed_out` branch; the SIGINT/KeyboardInterrupt path (`delegate.py:1271-1303`) must still run `kill_process_tree` — an operator Ctrl+C is an explicit kill order, not a budget event. No test pins this divergence (the existing harness drives the delegate via `subprocess.run`, which cannot deliver a signal cross-platform). Owner: Task 3, code-read evidence — the interrupt branch is untouched.
3. **Pre-fix state and journal shapes.** States initialized before this change have no `verify_timeout_s` in `state["budget"]`, no `detached`/`dispatch_id` keys on dispatch entries, and no `detached` field on `dispatch_finished` journal records. Every new read must be `.get`-based with the legacy default. Owner: Task 1 (fallback test) and Task 4 (pre-fix state test).
4. **Config that lies about survivability.** `report_detached` without `allow_breakaway` is a lie (nothing can outlive the job) — validated away; the model-dispatch template could smuggle it in — guarded and force-off at resolve; the *live* `agents.json` is gitignored, so CI cannot pin the production config — the operator must merge the new knob by hand. Owner: Task 2 (validation tests), ops note in Task 5.
5. **`child_pid` is informational; Windows recycles PIDs.** Nothing may kill by `child_pid` later; the accept gate deliberately reads the state's `detached` flag, not pid liveness (the runner has no process probe — that is #106's documented gap, out of scope). Owner: Task 4, code-read evidence — grep confirms no `taskkill`/`OpenProcess` in `runner.py`.

---

## Task 1 — Split the verifier off `budget.timeout_s` onto `verify_timeout_s`

The real defect in the ticket: one knob, two scopes (`runner.py:1000` dispatch vs `runner.py:1126` verify). This task splits the budget schema and migrates the one test that was (unwittingly) riding the double-serve.

**Files**
- Modify: `core/task_schema.py` (`_NUM_BUDGET_FIELDS`, line 32)
- Modify: `harnesses/kimi-code/runner/runner.py` (lines 106, ~1048, 1126, 1142, 1154)
- Test: `harnesses/kimi-code/runner/tests/test_m1_lite.py` (migrate line 107; add two tests)
- Test: `harnesses/kimi-code/runner/tests/test_task_schema_gate.py` (add two tests)

**Interfaces**
- Consumes: existing `load_task` merge (`runner.py:217-220`) — `task["budget"].setdefault(k, v)` over `DEFAULT_BUDGET` picks the new key up with no change.
- Produces: task.json budget key `verify_timeout_s` (positive number; default `600` via `DEFAULT_BUDGET`); `cmd_verify` reads `state["budget"].get("verify_timeout_s", state["budget"]["timeout_s"])` so pre-split states keep exact legacy behavior; `verifier_timeout` refusal receipt and error emit carry `verify_timeout_s` (renamed from `timeout_s` — no consumer reads that key; verified by grep across `harnesses/`, `core/`, `evals/`).

**Steps**

1. Write the failing tests. In `harnesses/kimi-code/runner/tests/test_m1_lite.py`, add `import time` to the imports, then inside `class A01VerifierTimeout` add:

```python
    def test_verify_timeout_is_independent_of_dispatch_timeout(self):
        # TOOL-033: budget.timeout_s used to size BOTH dispatch and verify
        # (runner.py:1000 vs :1126). A large dispatch budget must not extend
        # the verifier's clock.
        slow_task = make_task(
            self.tmp, ["{python}", "-c", "import time; time.sleep(30)"],
            budget={"max_dispatches": 4, "max_stagnant": 3,
                    "timeout_s": 600, "verify_timeout_s": 2},
            task_id="t_split")
        rc, out = run_runner("init", "--workspace", self.ws,
                             "--task", slow_task, "--reinit")
        self.assertEqual(rc, 0, out)
        t0 = time.monotonic()
        rc, out = run_runner("verify", "--workspace", self.ws,
                             "--task", slow_task, timeout=60)
        wall = time.monotonic() - t0
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "verifier_timeout")
        self.assertEqual(out["verify_timeout_s"], 2)
        self.assertLess(wall, 30)

    def test_pre_split_state_falls_back_to_dispatch_timeout(self):
        # States initialized before TOOL-033 carry no verify_timeout_s; the
        # verifier must keep the legacy behavior exactly (timeout_s).
        slow_task = make_task(
            self.tmp, ["{python}", "-c", "import time; time.sleep(30)"],
            budget={"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 2,
                    "verify_timeout_s": 600},
            task_id="t_legacy")
        rc, out = run_runner("init", "--workspace", self.ws,
                             "--task", slow_task, "--reinit")
        self.assertEqual(rc, 0, out)
        state_path = Path(out["state_dir"]) / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["budget"]["verify_timeout_s"]  # pre-TOOL-033 state shape
        state_path.write_text(json.dumps(state), encoding="utf-8")
        rc, out = run_runner("verify", "--workspace", self.ws,
                             "--task", slow_task, timeout=60)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["error"], "verifier_timeout")
        self.assertEqual(out["verify_timeout_s"], 2)  # fell back to timeout_s
```

   And migrate the existing double-serve rider at `test_m1_lite.py:107` — in `test_timeout_writes_refused_receipt_and_blocks_stale_green`, change:

```python
            budget={"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 2},
```
to:
```python
            budget={"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60,
                    "verify_timeout_s": 2},
```

   In `harnesses/kimi-code/runner/tests/test_task_schema_gate.py`, inside `class TaskSchemaGate` add:

```python
    def test_string_verify_timeout_refused(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp, budget={"max_dispatches": 4,
                                           "max_stagnant": 3,
                                           "timeout_s": 60,
                                           "verify_timeout_s": "600"})
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 3, out)
        self.assertEqual(out["error"], "task_schema_invalid")
        self.assertEqual(out["field"], "budget.verify_timeout_s")

    def test_verify_timeout_positive_number_accepted(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp, budget={"max_dispatches": 4,
                                           "max_stagnant": 3,
                                           "timeout_s": 60,
                                           "verify_timeout_s": 300})
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
```

2. Run, expect fail:
   `python -m unittest discover -s harnesses/kimi-code/runner/tests -p "test_m1_lite.py" -v`
   `python -m unittest discover -s harnesses/kimi-code/runner/tests -p "test_task_schema_gate.py" -v`
   Expect: the two new m1_lite tests fail (`verify_timeout_s` key absent from the error emit; the split test's verifier gets 600s and the run hits the 60s wrapper timeout), the string test fails (schema passes it through), and — after the migration edit only, before the runner change — `test_timeout_writes_refused_receipt_and_blocks_stale_green` fails because the verifier now gets the 600s default instead of 2s. That last failure is the double-serve defect made visible.

3. Implement. Three edits:

   `core/task_schema.py:32`:
```python
_NUM_BUDGET_FIELDS = ("timeout_s", "verify_timeout_s", "max_preflight_age_s")
```

   `harnesses/kimi-code/runner/runner.py:106`:
```python
# TOOL-033 phase semantics: timeout_s owns the DISPATCH (payload-launch)
# phase only; verify_timeout_s owns the VERIFY phase. Neither spans a
# detached payload's lifetime — the monitor phase is clock-free by design.
DEFAULT_BUDGET = {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 1800,
                  "verify_timeout_s": 600}
```

   In `cmd_verify`, immediately after `check_state_identity(state, task, ws, sroot)` (line 1049), add:
```python
    # TOOL-033: the verifier runs on its own budget. Pre-split states carry
    # no verify_timeout_s; they keep the legacy behavior exactly (timeout_s).
    verify_timeout_s = state["budget"].get("verify_timeout_s",
                                           state["budget"]["timeout_s"])
```
   Then change line 1126 `timeout=state["budget"]["timeout_s"],` to `timeout=verify_timeout_s,`; change line 1142 `"timeout_s": state["budget"]["timeout_s"],` to `"verify_timeout_s": verify_timeout_s,`; change line 1154 `"timeout_s": state["budget"]["timeout_s"],` to `"verify_timeout_s": verify_timeout_s},` (keeping the surrounding dict shape — line 1154 is the last key of the error emit, ending `"receipt": receipt}, 1)` on the next line; only the key name and value expression change).

4. Run, expect pass:
   `python -m unittest discover -s harnesses/kimi-code/runner/tests -p "test_m1_lite.py" -v`
   `python -m unittest discover -s harnesses/kimi-code/runner/tests -p "test_task_schema_gate.py" -v`
   Then the full runner + core suites:
   `python -m unittest discover -s harnesses/kimi-code/runner/tests -v`
   `python -m unittest discover -s core/tests -v`

5. Commit:
```
git add core/task_schema.py harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/test_m1_lite.py harnesses/kimi-code/runner/tests/test_task_schema_gate.py
git commit -m "feat(kimi): split verifier timeout from dispatch budget (TOOL-033, #104)"
```

---

## Task 2 — Delegate config surface: per-agent `on_timeout` with breakaway coupling

New agents.json knob that selects what budget expiry means. Coupled to `allow_breakaway` because a detached report is a lie when nothing can outlive the job.

**Files**
- Modify: `harnesses/kimi-code/delegate/delegate.py` (exit codes ~:52, `_validate_agent` after :534, `_validate_config` template guard after :447, `resolve_model_agent` :365)
- Modify: `harnesses/kimi-code/delegate/agents.example.json`
- Test: `harnesses/kimi-code/delegate/tests/test_breakaway.py` (extend — the natural home, per the inventory's #103 note)

**Interfaces**
- Consumes: existing `_validate_agent(name, agent, global_max_timeout, check_executable=False)` (`delegate.py:450`); `ab = agent.get("allow_breakaway", False)` already computed at :532.
- Produces: agents.json per-agent key `on_timeout: "kill_tree" | "report_detached"` (default `"kill_tree"`); delegate exit code `EXIT_DETACHED = 125`; `resolve_model_agent` output agent dicts always carry `on_timeout: "kill_tree"`.

**Steps**

1. Write the failing tests. In `harnesses/kimi-code/delegate/tests/test_breakaway.py`, extend the import line `from test_delegate import DelegateTestBase, make_agent` — no change needed yet (these tests need only those two); add `import os` is NOT needed here. Append after `TestBreakawayValidation`:

```python
class TestOnTimeoutValidation(DelegateTestBase):
    """TOOL-033: on_timeout is typed, valued, and coupled to breakaway."""

    def _agent_with(self, on_timeout, allow_breakaway):
        a = make_agent(self.echo_script)
        a["on_timeout"] = on_timeout
        a["allow_breakaway"] = allow_breakaway
        return self._config({"test-agent": a})

    def test_bad_value_rejected(self):
        for bad in ("detach", True, 1, None, ""):
            with self.subTest(value=bad):
                cfg = self._agent_with(bad, True)
                out, err, rc = self._run("test-agent", task="hello", config=cfg)
                self._assert_result(out, err, rc, "invalid", 64)

    def test_report_detached_requires_breakaway(self):
        cfg = self._agent_with("report_detached", False)
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "invalid", 64)

    def test_report_detached_with_breakaway_accepted(self):
        cfg = self._agent_with("report_detached", True)
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)

    def test_absent_defaults_to_kill_tree(self):
        cfg = self._config({"test-agent": make_agent(self.echo_script)})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        self._assert_result(out, err, rc, "completed", 0)


class TestOnTimeoutTemplateGuard(DelegateTestBase):
    """Model-mode dispatch always owns its payload's lifetime."""

    def _template(self):
        tpl = make_agent(self.echo_script)
        tpl["command"] = [sys.executable, "-m", "{model}", "-p"]
        return tpl

    def test_template_report_detached_rejected(self):
        tpl = self._template()
        tpl["allow_breakaway"] = True   # reach the template guard, not the
        tpl["on_timeout"] = "report_detached"  # agent-level coupling refusal
        cfg = self._config({"test-agent": make_agent(self.echo_script)},
                           extra={"model_dispatch_template": tpl})
        out, err, rc = self._run("test-agent", task="hello", config=cfg)
        result = self._assert_result(out, err, rc, "invalid", 64)
        self.assertIn("on_timeout", result["error"])

    def test_resolve_model_agent_forces_kill_tree(self):
        tpl = self._template()
        tpl["allow_breakaway"] = False
        agent = delegate.resolve_model_agent(
            {"model_dispatch_template": tpl}, "vendor/model-x", write=False)
        self.assertEqual(agent["on_timeout"], "kill_tree")
        self.assertFalse(agent["allow_breakaway"])
```

2. Run, expect fail:
   `python -m unittest discover -s harnesses/kimi-code/delegate/tests -p "test_breakaway.py" -v`
   Expect: `test_bad_value_rejected`, `test_report_detached_requires_breakaway`, `test_template_report_detached_rejected` fail (config loads clean today); `test_resolve_model_agent_forces_kill_tree` fails with `KeyError`/`AssertionError` (no `on_timeout` key on the resolved agent).

3. Implement in `harnesses/kimi-code/delegate/delegate.py`:

   a. Exit codes (after `EXIT_TIMEOUT = 124`, line 52):
```python
EXIT_TIMEOUT = 124
# Budget expiry on a detached payload: reported, not killed (TOOL-033).
EXIT_DETACHED = 125
EXIT_INTERRUPTED = 130
```

   b. In `_validate_agent`, immediately after the allow_breakaway block (after line 534):
```python
    # on_timeout (TOOL-033) — budget-phase semantics. "kill_tree" (default)
    # preserves the legacy everything-dies-with-the-dispatch guarantee.
    # "report_detached" turns budget expiry into a report instead of a kill;
    # it is only coherent for breakaway-capable agents — without
    # allow_breakaway nothing can outlive the job, so the report would lie.
    ot = agent.get("on_timeout", "kill_tree")
    if ot not in ("kill_tree", "report_detached"):
        raise ConfigError(
            f"Agent '{name}': on_timeout must be 'kill_tree' or "
            f"'report_detached', not {ot!r}")
    if ot == "report_detached" and not ab:
        raise ConfigError(
            f"Agent '{name}': on_timeout 'report_detached' requires "
            "allow_breakaway: true (without breakaway no payload can "
            "outlive the job, so the detached report would be a lie)")
```

   c. In `_validate_config`, immediately after the template allow_breakaway guard (after line 447):
```python
        if tpl.get("on_timeout", "kill_tree") != "kill_tree":
            raise ConfigError(
                "model_dispatch_template.on_timeout must be 'kill_tree' "
                "(model-mode dispatch always owns its payload's lifetime)")
```

   d. In `resolve_model_agent`, at line 365 after `agent["allow_breakaway"] = False`:
```python
    agent["allow_breakaway"] = False
    agent["on_timeout"] = "kill_tree"
```

   e. `harnesses/kimi-code/delegate/agents.example.json`: add `"on_timeout": "kill_tree",` immediately after `"allow_breakaway": false` in `model_dispatch_template` (line 29) and in the `example-coder` agent (line 57).

4. Run, expect pass:
   `python -m unittest discover -s harnesses/kimi-code/delegate/tests -p "test_breakaway.py" -v`
   Then the full delegate suite:
   `python -m unittest discover -s harnesses/kimi-code/delegate/tests -v`

5. Commit:
```
git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/agents.example.json harnesses/kimi-code/delegate/tests/test_breakaway.py
git commit -m "feat(kimi): agents.json on_timeout knob with breakaway coupling (TOOL-033, #104)"
```

---

## Task 3 — Delegate: `payload_running_detached` envelope state on budget expiry

The timeout branch learns to report instead of kill — but only for agents that opted in, and only on Windows where Job Objects define custody. Design decision (pin it in review): "never kill" covers the **breakaway-escaped payload**. The delegate's direct child never left the job, so the job close in `_cleanup_handles` still reaps it — this is exactly the documented residual at `delegate.py:95-107`, and it is what prevents taskkill `/T` from ever reaching the escaped payload through parent-PID links.

**Files**
- Modify: `harnesses/kimi-code/delegate/delegate.py` (`_make_result` :1403-1427, timeout branch :1305-1337)
- Test: `harnesses/kimi-code/delegate/tests/test_delegate.py` (`_ENVELOPE_KEYS` :315-333 gains `child_pid`)
- Test: `harnesses/kimi-code/delegate/tests/test_breakaway.py` (new Windows-only behavior class)

**Interfaces**
- Consumes: `agent.get("on_timeout", "kill_tree")` (Task 2); existing readers/joins/`extract_child_session_id`.
- Produces: envelope key `"child_pid": int | None` (new `_make_result` keyword `child_pid=None`, appended last — all existing call sites are keyword-called); envelope status `"payload_running_detached"`; exit code 125. Runner consumes these in Task 4.

**Steps**

1. Write the failing tests. In `harnesses/kimi-code/delegate/tests/test_breakaway.py`:
   - Change the imports at the top: add `import os` and `import time` after `import sys`, and extend the test_delegate import to `from test_delegate import DelegateTestBase, make_agent, _PID_SLEEPER`.
   - In `test_delegate.py`, add to `_ENVELOPE_KEYS` after `"child_home": (str, type(None)),`:
```python
    "child_pid": (int, type(None)),
```
   - Append to `test_breakaway.py`:

```python
@unittest.skipUnless(_IS_WINDOWS, "detached budget semantics need Job Objects")
class TestReportDetached(DelegateTestBase):
    """TOOL-033: budget expiry on a detached payload is reported, never killed.

    "Never kill" covers the breakaway-escaped payload. The direct child never
    left the job, so the job close still reaps it — the tests pin both halves.
    """

    def _sleeper_config(self, script_name, pid_name, on_timeout):
        sleeper = self._script(script_name, _PID_SLEEPER)
        pid_file = os.path.join(self.workspace, pid_name)
        a = make_agent(sleeper, prompt_delivery="argument",
                       extra_args=[pid_file],
                       default_timeout=3, minimum_timeout=1,
                       maximum_timeout=300)
        a["allow_breakaway"] = True
        if on_timeout is not None:
            a["on_timeout"] = on_timeout
        return self._config({"test-agent": a},
                            extra={"default_kill_grace_seconds": 2}), pid_file

    def test_budget_expiry_reports_detached_never_kills(self):
        cfg, pid_file = self._sleeper_config("det_sleeper", "det_pid.txt",
                                             "report_detached")
        t0 = time.monotonic()
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        wall = time.monotonic() - t0
        result = self._assert_result(out, err, rc,
                                     "payload_running_detached", 125)
        self.assertIsNone(result["child_exit_code"])
        self.assertLess(wall, 20)  # no grace burn — no kill sequence ran
        with open(pid_file) as f:
            child_pid = int(f.read().strip())
        self.assertEqual(result["child_pid"], child_pid)
        self.assertIn("reported, not killed", result["error"])
        # Direct-child containment is unchanged: it never left the job, so
        # the job close reaps it. Only breakaway-escaped payloads survive.
        time.sleep(2)
        self.assertFalse(delegate.is_pid_alive(child_pid),
                         "direct child must still be reaped by job close")

    def test_kill_tree_remains_the_default_with_breakaway(self):
        # allow_breakaway alone must NOT change timeout behavior — the
        # on_timeout opt-in is a separate, deliberate config act.
        cfg, pid_file = self._sleeper_config("kill_sleeper", "kill_pid.txt",
                                             None)
        out, err, rc = self._run("test-agent", task="ignored", config=cfg)
        result = self._assert_result(out, err, rc, "timeout", 124)
        self.assertIsNone(result["child_pid"])
```

2. Run, expect fail:
   `python -m unittest discover -s harnesses/kimi-code/delegate/tests -p "test_breakaway.py" -v`
   Expect: `test_budget_expiry_reports_detached_never_kills` fails with status `timeout`/rc 124 (old behavior) and, once `_ENVELOPE_KEYS` is edited, EVERY existing delegate test also fails on the missing `child_pid` key — that is the schema pinning doing its job.
   `python -m unittest discover -s harnesses/kimi-code/delegate/tests -v` → confirm the schema failures are all "Missing key: child_pid".

3. Implement in `harnesses/kimi-code/delegate/delegate.py`:

   a. `_make_result` (:1403-1407) — add the keyword (last position) and the envelope key:
```python
def _make_result(status, agent=None, child_exit_code=None, duration=None,
                 stdout_text="", stderr_text="", stdout_trunc=False, stderr_trunc=False,
                 stdout_log_trunc=False, stderr_log_trunc=False,
                 run_dir=None, acl_warning=False, job_warning=False,
                 child_session_id=None, child_home=None, child_pid=None, error=None):
```
   and in the returned dict after `"child_home": child_home,`:
```python
        "child_pid": child_pid,
```

   b. Timeout branch (:1305-1307) — insert the report_detached path at the top of `if timed_out:`, before the existing `kill_process_tree` call:
```python
    # Handle timeout
    if timed_out:
        # TOOL-033: budget expiry on a detached payload is a REPORT, never a
        # kill. taskkill /T would reach breakaway-escaped payloads through
        # parent-PID links while the worker is still alive (see :95-107), so
        # no kill sequence runs on this path at all. The job close below
        # still reaps the direct child (it never left the job); only payloads
        # that escaped via CREATE_BREAKAWAY_FROM_JOB survive — that survival
        # is the allow_breakaway contract the agent config opted into.
        # Non-Windows has no job custody, so it keeps the legacy kill path.
        if _IS_WINDOWS and agent.get("on_timeout", "kill_tree") == "report_detached":
            _cleanup_handles(proc_handle, job)
            job = None
            proc_handle = None
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            stdout_reader.join(timeout=10)
            stderr_reader.join(timeout=10)
            if stdin_thread:
                stdin_thread.join(timeout=5)
            duration = time.monotonic() - start_time
            stdout_text, stdout_trunc, stdout_log_trunc, _ = stdout_reader.get_result()
            stderr_text, stderr_trunc, stderr_log_trunc, _ = stderr_reader.get_result()
            result = _make_result(
                "payload_running_detached",
                agent=agent_name,
                duration=duration,
                stdout_text=stdout_text,
                stderr_text=stderr_text,
                stdout_trunc=stdout_trunc,
                stderr_trunc=stderr_trunc,
                stdout_log_trunc=stdout_log_trunc,
                stderr_log_trunc=stderr_log_trunc,
                run_dir=run_dir,
                acl_warning=acl_warning,
                job_warning=job_warning,
                child_session_id=extract_child_session_id(stdout_text, stderr_text),
                child_home=child_home,
                child_pid=proc.pid,
                error="dispatch budget expired; payload detached — "
                      "reported, not killed",
            )
            return result, EXIT_DETACHED
        kill_process_tree(proc.pid, cfg["default_kill_grace_seconds"], job)
```
   (The rest of the existing timeout branch is unchanged.)

4. Run, expect pass:
   `python -m unittest discover -s harnesses/kimi-code/delegate/tests -v`
   (Full suite — the `_ENVELOPE_KEYS` change touches every envelope assertion.)

5. Commit:
```
git add harnesses/kimi-code/delegate/delegate.py harnesses/kimi-code/delegate/tests/test_breakaway.py harnesses/kimi-code/delegate/tests/test_delegate.py
git commit -m "feat(kimi): payload_running_detached envelope state on budget expiry (TOOL-033, #104)"
```

---

## Task 4 — Runner: monitor phase — dispatch, accept gate, status, and the fake worker

The runner stops assuming dispatch lifetime == payload lifetime. Also fixes (drive-by, same file and failure class): `run_delegate`'s error tails at `runner.py:769,774` reference an undefined `r` — a NameError on the unparseable-envelope path, which is exactly the failure mode a new envelope state must degrade through cleanly. The dead duplicate branch at `:767-769` is removed.

**Files**
- Modify: `harnesses/kimi-code/runner/runner.py` (`run_delegate` :763-774, `cmd_dispatch` :1003-1041, `cmd_accept` after :1252 and :1316-1318, `cmd_status` :1561, argparse :1627-1633)
- Modify: `harnesses/kimi-code/runner/tests/fake_worker.py` (new `detached` mode)
- Test: `harnesses/kimi-code/runner/tests/test_detached_budget.py` (new file)

**Interfaces**
- Consumes: delegate envelope `payload_running_detached` + `child_pid` (Task 3); `_journal_append` (:504), `classify_and_recommend` (:675), the `--allow-zero-dispatch` override idiom (:1299-1319).
- Produces: state dispatch entries gain `"dispatch_id": str`, `"detached": bool`, `"child_pid": int | None`; journal `dispatch_finished` records gain `"detached": bool`; `accept --allow-detached-payload` flag with `allow_detached_payload_count` on state; `status` output key `detached_dispatch_ids: [str]`; dispatch output keys `detached`, `child_pid`; `fake_worker.py` env mode `FAKE_WORKER_MODE=detached` (exit 125, status `payload_running_detached`, `child_pid` = own pid).

**Steps**

1. Write the failing tests. New file `harnesses/kimi-code/runner/tests/test_detached_budget.py`:

```python
#!/usr/bin/env python3
"""TOOL-033 (#104): a detached payload's lifetime is outside every budget.

dispatch reports payload_running_detached without recording a failure and
closes its journal pair (a detached payload is not an orphan); accept refuses
while a detached payload may be mutating the tree, with a counted override;
status surfaces detached dispatches; pre-TOOL-033 state shapes read clean.

Run: python -m unittest discover -s runner/tests -v
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "runner" / "runner.py"
FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"

BUGGY = 'def sum_to_n(n):\n    return sum(range(1, n))\n'
FIXED = 'def sum_to_n(n):\n    return sum(range(1, n + 1))\n'
CHECK = (
    'import sys, os\n'
    'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
    'from math_utils import sum_to_n\n'
    'assert sum_to_n(5) == 15, f"sum_to_n(5)={sum_to_n(5)}, expected 15"\n'
    'print("PASS")\n'
)


def run_runner(*argv, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), *argv],
                       capture_output=True, text=True, env=env, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return r.returncode, out


def make_task(tmp, task_id="t1"):
    task = {
        "task_id": task_id,
        "prompt": "Fix the bug.",
        "features": {"bounded": True, "known_location": True,
                     "objective_acceptance": True},
        "scope": ["math_utils.py"],
        "verifier": {"argv": ["{python}", "check.py"]},
        "budget": {"max_dispatches": 4, "max_stagnant": 3, "timeout_s": 60},
    }
    p = Path(tmp) / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    return str(p)


def make_workspace(tmp):
    ws = Path(tmp) / "ws"
    ws.mkdir()
    (ws / "math_utils.py").write_text(BUGGY, encoding="utf-8")
    (ws / "check.py").write_text(CHECK, encoding="utf-8")
    return str(ws)


def journal_lines(sdir):
    jr = Path(sdir) / "journal.jsonl"
    if not jr.is_file():
        return []
    return [json.loads(ln) for ln in jr.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


class DetachedBudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="runner-detached-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _setup_ready(self):
        ws = make_workspace(self.tmp)
        task = make_task(self.tmp)
        rc, out = run_runner("init", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        return ws, task, Path(out["state_dir"])

    def _dispatch_detached(self, ws, task):
        env = {"FAKE_WORKER_MODE": "detached",
               "FAKE_WORKER_WRITE": "math_utils.py",
               "FAKE_WORKER_CONTENT": FIXED}
        return run_runner("dispatch", "--workspace", ws, "--task", task,
                          "--delegate", str(FAKE_WORKER), env_extra=env)

    def test_detached_dispatch_is_reported_not_failed(self):
        ws, task, sdir = self._setup_ready()
        rc, out = self._dispatch_detached(ws, task)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "payload_running_detached")
        self.assertTrue(out["detached"])
        self.assertIsInstance(out["child_pid"], int)
        self.assertEqual(out["recommendation"]["action"], "monitor_detached")
        # Detached is a custody state, not a failure: nothing appended to
        # state["failures"], so the provider circuit breaker cannot trip on it.
        rc, st = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, st)
        self.assertEqual(st["failure_count"], 0, st)
        self.assertEqual(st["detached_dispatch_ids"], [out["dispatch_id"]])
        # The journal pair still closes — a detached payload is not an orphan.
        events = [e["event"] for e in journal_lines(sdir)
                  if e["event"].startswith("dispatch")]
        self.assertEqual(events, ["dispatch_open", "dispatch_finished"])
        self.assertTrue(journal_lines(sdir)[-1]["detached"])
        self.assertEqual(journal_lines(sdir)[-1]["envelope_status"],
                         "payload_running_detached")

    def test_accept_refuses_while_detached_payload_in_flight(self):
        ws, task, sdir = self._setup_ready()
        rc, out = self._dispatch_detached(ws, task)
        self.assertEqual(rc, 0, out)
        rc, out = run_runner("verify", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["passed"], out)
        # Journal pair is closed and the receipt is green, yet the payload
        # may still be writing: acceptance must refuse.
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertEqual(rc, 1)
        self.assertFalse(out["accepted"])
        self.assertTrue(out["reason"].startswith("detached_payload_in_flight"),
                        out)
        # The reviewed-decision override works and is counted on state.
        rc, out = run_runner("accept", "--workspace", ws, "--task", task,
                             "--allow-detached-payload")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["accepted"])
        rc, st = run_runner("status", "--workspace", ws)
        self.assertEqual(st["allow_detached_payload_count"], 1)

    def test_pre_fix_state_without_detached_keys_reads_clean(self):
        # Review-focus #3: states written before TOOL-033 have no detached
        # keys anywhere; every new read must default to the legacy shape.
        ws, task, sdir = self._setup_ready()
        state_path = sdir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["dispatches"] = [{"agent": "glm-flash-worker",
                                "status": "completed",
                                "duration_seconds": 1.0,
                                "at": "2026-09-25T00:00:00"}]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        rc, st = run_runner("status", "--workspace", ws)
        self.assertEqual(rc, 0, st)
        self.assertEqual(st["detached_dispatch_ids"], [])
        rc, out = run_runner("accept", "--workspace", ws, "--task", task)
        self.assertNotEqual(out.get("reason", "").startswith(
            "detached_payload_in_flight"), True)

    def test_unparseable_delegate_envelope_is_internal_error_not_crash(self):
        # Drive-by fix: run_delegate's error tails referenced an undefined
        # `r` (NameError) on exactly the path a foreign envelope takes.
        ws, task, sdir = self._setup_ready()
        rc, out = run_runner("dispatch", "--workspace", ws, "--task", task,
                             "--delegate", str(FAKE_WORKER),
                             env_extra={"FAKE_WORKER_MODE": "garbage"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["envelope_status"], "internal_error")
        self.assertEqual(out["failure_class"], "provider_or_tool")
```

   Note: the `garbage` mode does not exist yet — step 3 adds it to the fake worker.

2. Run, expect fail:
   `python -m unittest discover -s harnesses/kimi-code/runner/tests -p "test_detached_budget.py" -v`
   Expect: all four tests fail — `KeyError` on the fake worker (`detached`/`garbage` modes unknown), then after the fake supports them: missing `detached`/`child_pid` output keys, `failure_count` 1 instead of 0, accept wrongly succeeds, `detached_dispatch_ids` absent.

3. Implement.

   a. `harnesses/kimi-code/runner/tests/fake_worker.py` — mode table (:37-41) and envelope (:43-54):
```python
    mode = os.environ.get("FAKE_WORKER_MODE", "completed")
    status, child_rc, exit_code = {
        "completed": ("completed", 0, 0),
        "failed": ("failed", 1, 0),
        "timeout": ("timeout", None, 124),
        "detached": ("payload_running_detached", None, 125),
        "garbage": ("garbage", None, 0),
    }[mode]

    if mode == "garbage":
        sys.stdout.write("this is not json\n")
        return exit_code

    sys.stdout.write(json.dumps({
        "schema_version": 1,
        "status": status,
        "agent": args.agent,
        "child_exit_code": child_rc,
        "child_pid": os.getpid() if mode == "detached" else None,
        "duration_seconds": 0.01,
        "stdout": f"fake worker {args.agent} (mode={mode})",
        "stderr": "",
        "run_dir": None,
        "error": None if status in ("completed", "failed") else f"fake_{status}",
        "child_session_id": "fake-session-0001" if status == "completed" else None,
    }) + "\n")
    return exit_code
```
   Also update the module docstring's mode list (:8) to `completed (default) | failed | timeout | detached | garbage`.

   b. `runner.py` `run_delegate` tail (:763-774) — remove the dead duplicate and fix the NameError:
```python
    line = (out or "").strip().splitlines()
    if not line:
        return {"status": "internal_error", "error": "empty delegate output",
                "stderr": (err or "")[-500:], "agent": agent}
    try:
        return json.loads(line[-1])
    except json.JSONDecodeError:
        return {"status": "internal_error", "error": "unparseable delegate envelope",
                "stdout_tail": (out or "")[-500:], "agent": agent}
```

   c. `cmd_dispatch` — after `envelope_status = envelope.get("status")` (:1003):
```python
    envelope_status = envelope.get("status")
    # TOOL-033: a detached report is a custody state, not a failure — it must
    # not count against max_stagnant or trip the provider circuit breaker,
    # and no budget clock follows the payload into its detached life.
    detached = envelope_status == "payload_running_detached"
```
   In the `state["dispatches"].append({...})` dict (:1004-1013), add three keys after `"agent": agent, "status": envelope_status,`:
```python
        "dispatch_id": dispatch_id, "detached": detached,
        "child_pid": envelope.get("child_pid"),
```
   Change the failure guard (:1014) to:
```python
    if envelope_status not in ("completed", "failed", "payload_running_detached"):
```
   In the `dispatch_finished` journal record (:1024-1030), add after `"envelope_status": envelope_status,`:
```python
        "detached": detached,
```
   Replace `cls, rec = classify_and_recommend(state, state["lane"])` (:1031) with:
```python
    cls, rec = classify_and_recommend(state, state["lane"])
    if detached:
        rec = {"action": "monitor_detached",
               "note": "payload outlives the dispatch budget by design; "
                       "accept refuses until --allow-detached-payload"}
```
   In the emit dict (:1032-1041), add after `"child_home": envelope.get("child_home"),`:
```python
        "detached": detached, "child_pid": envelope.get("child_pid"),
```

   d. `cmd_accept` — immediately after the in-flight gate block (after line 1252's `}, 1))`):
```python
    # TOOL-033: a detached payload outlived its dispatch budget — its journal
    # pair is closed, but the payload may still be mutating the tree. Same
    # refuse-unless-explicit-override pattern as --allow-zero-dispatch.
    _detached = sorted(str(d.get("dispatch_id"))
                       for d in state.get("dispatches", [])
                       if d.get("detached"))
    if _detached and not getattr(args, "allow_detached_payload", False):
        raise SystemExit(_emit({
            "accepted": False,
            "reason": "detached_payload_in_flight: a dispatch's payload is "
                      "running detached; the tree may be mutating under this "
                      "acceptance",
            "detached_dispatch_ids": _detached,
            "hint": "confirm the detached payload has finished (status lists "
                    "it under detached_dispatch_ids), then re-run accept "
                    "with --allow-detached-payload as a reviewed decision "
                    "(counted on state)",
        }, 1))
```
   In the success path, after the `allow_zero_dispatch` counter (:1316-1318):
```python
    if getattr(args, "allow_detached_payload", False):
        state["allow_detached_payload_count"] = int(
            state.get("allow_detached_payload_count", 0)) + 1
```

   e. argparse (:1627-1633) — inside `if name == "accept":`, after the `--allow-zero-dispatch` argument:
```python
            p.add_argument("--allow-detached-payload", action="store_true",
                           help="accept while a detached payload may still be "
                                "running, as a reviewed decision (counted on "
                                "state)")
```

   f. `cmd_status` — in the `out` dict after `"orphaned_dispatch_ids": orphans,` (:1561):
```python
        "detached_dispatch_ids": sorted(
            str(d.get("dispatch_id")) for d in state.get("dispatches", [])
            if d.get("detached")),
```

4. Run, expect pass:
   `python -m unittest discover -s harnesses/kimi-code/runner/tests -p "test_detached_budget.py" -v`
   Then the full runner suite (accept/dispatch/state tests all touch these code paths):
   `python -m unittest discover -s harnesses/kimi-code/runner/tests -v`

5. Commit:
```
git add harnesses/kimi-code/runner/runner.py harnesses/kimi-code/runner/tests/fake_worker.py harnesses/kimi-code/runner/tests/test_detached_budget.py
git commit -m "feat(kimi): runner monitors detached payloads, never spans their lifetime (TOOL-033, #104)"
```

---

## Task 5 — Docs, live-config ops note, and the full gate

Behavior the docs describe has changed; bring them in line (AGENTS.md documents the runner acceptance gate, so it must change too).

**Files**
- Modify: `harnesses/kimi-code/delegate/README.md` (status list :79, status table :107-111, exit-code table :123, agent-field table ~:62-78, wall-clock note :130)
- Modify: `harnesses/kimi-code/skill/model-proctor/SKILL.md` (budgets bullet :145-147, resume protocol :155-169)
- Modify: `AGENTS.md` (runner paragraph, :34-55)
- Test: none — this task is docs + the full-suite gate.

**Interfaces**
- Consumes: everything above. Produces: documentation matching the shipped behavior; an ops note for the gitignored live `agents.json`.

**Steps**

1. `harnesses/kimi-code/delegate/README.md`:
   - Status list (:79): `"status": "completed|failed|timeout|invalid|internal_error|interrupted|payload_running_detached",`
   - Status table (after the `timeout` row, :111): `| \`payload_running_detached\` | Budget expired on an \`on_timeout: report_detached\` agent; the breakaway-escaped payload keeps running — reported, never killed. \`child_pid\` carries the (reaped) direct child's pid |`
   - Exit-code table (after the 124 row, :123): `| 125 | Budget expired on a detached payload (reported, not killed) |`
   - Agent-field table (after the `allow_breakaway` row): `| \`on_timeout\` | No | \`kill_tree\` (default) or \`report_detached\`; the latter requires \`allow_breakaway: true\` and turns budget expiry into a report instead of a kill |`
   - Wall-clock note (:130): append `With \`on_timeout: report_detached\` no kill sequence runs, so the ceiling is ≈ \`timeout + ~30 s\` (reader joins, no grace).`
2. `harnesses/kimi-code/skill/model-proctor/SKILL.md`:
   - Budgets bullet (:145-147): append a sentence: `verify runs on its own budget.verify_timeout_s (default 600), split from dispatch timeout_s (TOOL-033); neither clock follows a detached payload.`
   - Resume protocol (:155-169), after the orphans bullet: `- \`detached_dispatch_ids\` non-empty means a payload outlived its dispatch budget and may still be mutating the tree; \`accept\` refuses until \`--allow-detached-payload\` is supplied — a reviewed, counted exception, same standing as \`--allow-zero-dispatch\`.`
3. `AGENTS.md` runner paragraph (:34-55): append after the `init refuses to re-baseline` sentence: `Budgets have phase semantics (TOOL-033): dispatch timeout_s, a separate verify_timeout_s, and a clock-free monitor phase for detached payloads (envelope status payload_running_detached); acceptance refuses while a detached payload may be in flight, with accept --allow-detached-payload as the counted override.`
4. Run the full CI-parity gate (all eight suites):
```
python -m unittest discover -s harnesses/kimi-code/delegate/tests -v
python -m unittest discover -s harnesses/kimi-code/runner/tests -v
python -m unittest discover -s harnesses/kimi-code/cascade/tests -v
python -m unittest discover -s harnesses/codex/delegate/tests -v
python -m unittest discover -s core/tests -v
python -m unittest discover -s scripts/tests -v
python -m unittest discover -s evals/tests -v
python -m unittest discover -s harnesses/zcode/tests -v
```
5. Commit:
```
git add harnesses/kimi-code/delegate/README.md harnesses/kimi-code/skill/model-proctor/SKILL.md AGENTS.md
git commit -m "docs(kimi): detached budget phase semantics (TOOL-033, #104)"
```
6. Ops handoff (not a commit): the live install `C:\Tools\model-proctor\agents.json` is gitignored; note in the issue's final-evidence section that operators wanting detached dispatches must add `"on_timeout": "report_detached"` (with `allow_breakaway: true`) to the specific agent there — validation refuses the combination without breakaway, so a half-applied edit fails safe.

---

## Self-review pass

- **Spec coverage.** Ticket asks: (a) budget phase semantics — Task 1 splits launch vs verify, Tasks 3-4 add the clock-free monitor phase; (b) new envelope state `payload_running_detached` with "report, never kill" on budget expiry — Task 3; (c) config surface changes in agents.json — Task 2 (tracked example + validation; live gitignored file is an ops note in Task 5); (d) migration of call sites — Task 1 migrates `test_m1_lite.py:107` (the only test riding the double-serve, found by grep), Task 4 migrates `cmd_dispatch`/`cmd_accept`/`cmd_status`/`fake_worker.py`. Orphan/accept ceilings (`runner.py:910,:1237,:1508,:1529`) were evaluated and deliberately left on `timeout_s`: a detached dispatch's journal pair closes at report time, so it can never read as an orphan — the accept gate's NEW detached check replaces the lifetime assumption instead of extending the ceiling. Verified-no-change: `pilot.py:339` (`timeout_s + 300` outer wrapper — still a valid outer backstop), cascade (frozen), codex adapter (separate `_Deadline`).
- **Placeholder scan.** Every step carries real code or a real command; no TBDs. Two intentional non-code items: the ops handoff (Task 5 step 6 — the live config is untracked by design) and the review-focus code-read pins where a test is impossible in the existing harness (interruption signaling, wedged delegate).
- **Type consistency across tasks.** `child_pid`: `int | None` in `_make_result` (Task 3), `_ENVELOPE_KEYS` `(int, type(None))` (Task 3), fake worker emits `int` only in detached mode (Task 4), runner stores/emits via `envelope.get("child_pid")` uncoerced (Task 4). `detached`: `bool` everywhere, `.get`-defaulted `False`. `verify_timeout_s`: positive number per `_require_num`; fallback `state["budget"]["timeout_s"]` is the same type. Exit code 125 defined in Task 2, first produced in Task 3, mirrored by the fake in Task 4 — Task 2's tests do not depend on 125 being emitted, so the ordering is safe.
- **Review-focus pins.** #1 → Task 4 code-read (wrapper path unchanged); #2 → Task 3 code-read (interrupt branch untouched); #3 → `test_pre_split_state_falls_back_to_dispatch_timeout` (Task 1) and `test_pre_fix_state_without_detached_keys_reads_clean` (Task 4); #4 → `TestOnTimeoutValidation` + `TestOnTimeoutTemplateGuard` (Task 2) + Task 5 ops note; #5 → Task 4 design note (accept reads the state flag, never pid liveness).
- **Inventory corrections found while surveying** (the plan wins over the inventory):
  1. Inventory #104 note cites envelope statuses at `delegate.py:49-53`; those lines are EXIT codes. Statuses are string literals at `_make_result` call sites — the plan adds the new status there.
  2. Inventory says the fix seam is envelope construction `delegate._make_result` + runner synthetic envelopes; surveying found the actual decision point is the timeout branch `delegate.py:1305-1337`, and that is where the plan puts the branch (the `_make_result` change is additive-only).
  3. Pre-existing defect found adjacent to the seam, not in the inventory: `runner.py:767-769` is a dead duplicate branch and `:769,:774` reference an undefined `r` (NameError on the unparseable-envelope path). Fixed as a scoped drive-by in Task 4 with a regression test (`garbage` mode), because a new envelope state must degrade through exactly that path.
