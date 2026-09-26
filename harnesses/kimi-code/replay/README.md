# Regression replay corpus (TOOL-039, #110)

Recorded production failure signatures, replayed on a simulated clock against
the monitoring kill predicate. The corpus exists so the monitoring redesign
(#105–#109) cannot reintroduce the five incident classes it was designed to
fix — and cannot soften the one control case it must still catch.

## Layout

- `replay_schema.py` — strict case-file validation (task_schema doctrine).
- `replay_driver.py` — simulated-clock replay: `decide(window)` after every
  recorded event, then expectation checks. Never sleeps, never reads the wall
  clock.
- `reference_predicate.py` — the executable specification of the target
  policy; the oracle this corpus was authored against. NOT production code.
- `production_bridge.py` — resolves the #106 production predicate
  (`monitor:decide_kill`, override with `REPLAY_PREDICATE="module:function"`).
  Returns None until #106 lands; the production suite skips until then.
- `gen_corpus.py` — deterministic corpus generator (`python gen_corpus.py
  <out_dir>`); `corpus/` is its byte-stable committed output.
- `corpus/` — the 10 recorded incidents across 5 classes.
- `tests/` — the unittest suite (in CI's `$suites` array).

## The five incident classes

| Class | Incidents | Expected verdict |
|---|---|---|
| `completion_before_stall_kill` | 2026-09-01 x2 | no kill (completion evidence suppresses) |
| `killed_after_success` | 2026-09-12 (46 s) | no kill |
| `monitor_measurement_failure` | 2026-09-16, 2026-09-17 | no kill + `measurement_degraded` |
| `maxminutes_healthy_slow_run` | 2026-08-25 x3 | no kill past the wall-clock backstop |
| `control_genuine_wedge` | 2026-09-25 attempts 06/07 | kill `confirmed_wedge` WITH stack evidence |

## Adding an incident

1. Add the incident to `INCIDENTS` in `gen_corpus.py` with a `source_note`
   stating what the original trace showed and where it lives.
2. `python harnesses/kimi-code/replay/gen_corpus.py harnesses/kimi-code/replay/corpus`
3. Run the suite; commit the generator change and the new fixture together.
   `test_corpus_regeneration_is_byte_stable` enforces corpus == generator
   output, so hand-edited fixtures fail.

The original production traces live outside the repo (durability doctrine);
the committed fixtures are the durable, reviewable reconstruction.
