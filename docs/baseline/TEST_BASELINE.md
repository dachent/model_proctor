# Test baseline

Per-suite counts that CI must not fall below (`baseline.json`, compared by `scripts/check_baseline.py`). The table here is informational.

First measured by a CI run (GitHub Actions `windows-latest`, run 44667533765) on `main` at `22f118e`: 810 tests, 4 skipped, 0 failed. Raised for tests added by Plan 1 T3/T4; reduced when the codex and zcode harnesses (124 tests) were removed in the Kimi-only scrub (Plan 1 T6a).

| Suite | Ran | Skipped | Failed |
|---|---|---|---|
| core/tests | 36 | 0 | 0 |
| evals/tests | 26 | 1 | 0 |
| harnesses/kimi-code/delegate/tests | 238 | 0 | 0 |
| harnesses/kimi-code/loop/tests | 53 | 0 | 0 |
| harnesses/kimi-code/replay/tests | 54 | 1 | 0 |
| harnesses/kimi-code/runner/tests | 154 | 0 | 0 |
| harnesses/kimi-code/wirewatch/tests | 15 | 0 | 0 |
| scripts/tests | 122 | 2 | 0 |
| **Total** | **698** | **4** | **0** |

## Skip reasons
- `evals/tests`: machine-local delegate roster not present.
- `harnesses/kimi-code/replay/tests`: production kill predicate absent, lands with #106 (TOOL-035).
- `scripts/tests` (2 skips; the log shows one distinct reason): elevated context, Administrators:F grants write by design; the assertion is about the non-elevated worker threat model.

## Notes
- No pre-existing failures at first measurement.
- Lowering a count is a deliberate, reviewed change to `baseline.json` (CODEOWNERS).
