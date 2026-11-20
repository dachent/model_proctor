# Test baseline

Produced by a CI run (GitHub Actions `windows-latest`, run 44667533765) on commit `032fab5`, which is `main` at `22f118e` plus two temporary CI files (removed in the commit that adds this file). Test counts are therefore those of `22f118e`. `baseline.json` is the machine-readable form; the local table here is informational.

Tools: Python 3.10.11, Windows-10-10.0.26100-SP0.

| Suite | Ran | Skipped | Failed |
|---|---|---|---|
| core/tests | 36 | 0 | 0 |
| evals/tests | 26 | 1 | 0 |
| harnesses/codex/delegate/tests | 78 | 0 | 0 |
| harnesses/codex/skill/model-proctor/tests | 9 | 0 | 0 |
| harnesses/kimi-code/delegate/tests | 238 | 0 | 0 |
| harnesses/kimi-code/loop/tests | 50 | 0 | 0 |
| harnesses/kimi-code/replay/tests | 54 | 1 | 0 |
| harnesses/kimi-code/runner/tests | 154 | 0 | 0 |
| harnesses/kimi-code/wirewatch/tests | 15 | 0 | 0 |
| harnesses/zcode/tests | 37 | 0 | 0 |
| scripts/tests | 113 | 2 | 0 |
| **Total** | **810** | **4** | **0** |

## Skip reasons
- `evals/tests`: machine-local delegate roster not present.
- `harnesses/kimi-code/replay/tests`: production kill predicate absent, lands with #106 (TOOL-035).
- `scripts/tests` (2 skips; the log shows one distinct reason): elevated context, Administrators:F grants write by design; the assertion is about the non-elevated worker threat model.

## Notes
- No pre-existing failures.
- The codex and zcode suites (124 tests) are removed by the Kimi-only scrub; the baseline is regenerated or reduced deliberately in that change.
