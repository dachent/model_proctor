# Kimi Code CLI Benchmark Scorecard

Generated from 36 run records in `results.jsonl`.

## Blinding

Key direction: Config-name → blind letter. Tables below unblind the
letter a run row carries; a letter with no key entry is marked `(unkeyed)`.

- Config1 → config **D**
- Config2 → config **A**
- Config3 → config **C**

## Cost source

- `cost_source: "metered"` — 36/36 run rows joined from `results-metered.jsonl` by `(case_id, config, rep)`; every cost figure is metered USD.

## Aggregate — tune set

| Config | N | Success | Rate | Hidden | Hidden% | Wall(s) | Tokens | Cost/Succ ($) | Cost src | 1st-pass |
|--------|---|---------|------|--------|---------|---------|--------|---------------|----------|----------|
| Config2 | 4 | 4 | 1.00 | 4 | 1.00 | 35.21 | 161.50 | $0.1936 | metered | 4/4 |
| B (unkeyed) | 4 | 4 | 1.00 | 4 | 1.00 | 55.87 | 217.50 | $0.2350 | metered | 4/4 |
| Config3 | 4 | 4 | 1.00 | 4 | 1.00 | 45.61 | 187.50 | $0.2191 | metered | 4/4 |

## Aggregate — holdout set

| Config | N | Success | Rate | Hidden | Hidden% | Wall(s) | Tokens | Cost/Succ ($) | Cost src | 1st-pass |
|--------|---|---------|------|--------|---------|---------|--------|---------------|----------|----------|
| Config2 | 2 | 2 | 1.00 | 2 | 1.00 | 42.69 | 201.50 | $0.2400 | metered | 2/2 |
| B (unkeyed) | 2 | 2 | 1.00 | 2 | 1.00 | 59.97 | 141.00 | $0.2407 | metered | 2/2 |
| Config3 | 2 | 2 | 1.00 | 2 | 1.00 | 37.07 | 136.00 | $0.1740 | metered | 2/2 |

## Per-category breakdown (holdout)

### mechanical

| Config | N | Success | Rate | Wall(s) | Tokens |
|--------|---|---------|------|---------|--------|
| Config2 | 1 | 1 | 1.00 | 46.88 | 102 |
| B (unkeyed) | 1 | 1 | 1.00 | 79.10 | 118 |
| Config3 | 1 | 1 | 1.00 | 43.13 | 120 |

### security

| Config | N | Success | Rate | Wall(s) | Tokens |
|--------|---|---------|------|---------|--------|
| Config2 | 1 | 1 | 1.00 | 38.49 | 301 |
| B (unkeyed) | 1 | 1 | 1.00 | 40.84 | 164 |
| Config3 | 1 | 1 | 1.00 | 31.01 | 152 |

## Per-case medians (holdout)

| Case | Config | N | Acc% | Wall(s) | Tokens |
|------|--------|---|------|---------|--------|
| me1_rename_func | Config2 | 1 | 1.00 | 46.88 | 102 |
| me1_rename_func | B (unkeyed) | 1 | 1.00 | 79.10 | 118 |
| me1_rename_func | Config3 | 1 | 1.00 | 43.13 | 120 |
| sec1_path_traversal | Config2 | 1 | 1.00 | 38.49 | 301 |
| sec1_path_traversal | B (unkeyed) | 1 | 1.00 | 40.84 | 164 |
| sec1_path_traversal | Config3 | 1 | 1.00 | 31.01 | 152 |

## Simple-case guardrail

Config3 must not be slower than Config2 on any simple_fix case median.

| Case | Base wall(s) | Cand wall(s) | OK |
|------|-------------|-------------|----|
| sf1_off_by_one | 34.54 | 35.91 | ✗ |

**Guardrail: FAIL**

## Adoption Verdict (holdout only)

Adopted requires all three gates: success rate, metered cost per success, guardrail.

- **Config3** success rate: 1.00 vs **Config2** 1.00 → ≥ ✓
- **Config3** cost/success (metered USD): $0.1740 vs **Config2** $0.2400 → < ✓
- Guardrail (simple_fix wall-clock): FAIL ✗
- **Verdict: NOT ADOPTED** Config3 (failed: guardrail)

## First-pass success (rep 1, exit 0, no timeout)

| Config | 1st-pass | Total | Rate |
|--------|----------|-------|------|
| Config2 | 8 | 8 | 1.00 |
| B (unkeyed) | 6 | 6 | 1.00 |
| Config3 | 8 | 8 | 1.00 |
| Config1 | 2 | 2 | 1.00 |

## Regression proxy (hidden_pass rate)

| Config | Hidden pass | Total | Rate |
|--------|-------------|-------|------|
| Config2 | 10 | 12 | 0.83 |
| B (unkeyed) | 6 | 6 | 1.00 |
| Config3 | 8 | 12 | 0.67 |
| Config1 | 3 | 6 | 0.50 |

## Raw run table

| Case | Config | Rep | Set | Wall(s) | Exit | Timeout | Acc | Hidden | Inv | Tokens | Cost ($) |
|------|--------|-----|-----|---------|------|---------|-----|--------|-----|--------|----------|
| big_explore | Config2 | 1 | showcase | 205.39 | 0 | N | ✓ | ✗ | ✓ | 407 | $0.2995 |
| big_explore | Config2 | 2 | showcase | 56.17 | 0 | N | ✓ | ✗ | ✓ | 344 | $0.3113 |
| big_explore | Config2 | 3 | showcase | 70.64 | 0 | N | ✓ | ✓ | ✓ | 426 | $0.3056 |
| big_explore | Config3 | 1 | showcase | 132.03 | 0 | N | ✓ | ✗ | ✓ | 371 | $0.3609 |
| big_explore | Config3 | 2 | showcase | 64.85 | 0 | N | ✓ | ✗ | ✓ | 379 | $0.3417 |
| big_explore | Config3 | 3 | showcase | 60.12 | 0 | N | ✓ | ✗ | ✓ | 375 | $0.3059 |
| big_explore | Config1 | 1 | showcase | 59.95 | 0 | N | ✓ | ✗ | ✓ | 411 | $0.2942 |
| big_explore | Config1 | 2 | showcase | 82.68 | 0 | N | ✓ | ✗ | ✓ | 433 | $0.2800 |
| big_explore | Config1 | 3 | showcase | 57.85 | 0 | N | ✓ | ✗ | ✓ | 399 | $0.2982 |
| bulk_migrate | Config2 | 1 | showcase | 83.23 | 0 | N | ✓ | ✓ | ✓ | 456 | $0.3343 |
| bulk_migrate | Config2 | 2 | showcase | 86.72 | 0 | N | ✓ | ✓ | ✓ | 363 | $0.4672 |
| bulk_migrate | Config2 | 3 | showcase | 52.35 | 0 | N | ✓ | ✓ | ✓ | 281 | $0.2712 |
| bulk_migrate | Config3 | 1 | showcase | 137.26 | 0 | N | ✓ | ✓ | ✓ | 380 | $0.3559 |
| bulk_migrate | Config3 | 2 | showcase | 175.14 | 0 | N | ✓ | ✓ | ✓ | 436 | $0.4156 |
| bulk_migrate | Config3 | 3 | showcase | 205.81 | 0 | N | ✓ | ✗ | ✓ | 543 | $0.4362 |
| bulk_migrate | Config1 | 1 | showcase | 224.40 | 0 | N | ✓ | ✓ | ✓ | 598 | $0.5461 |
| bulk_migrate | Config1 | 2 | showcase | 296.14 | 0 | N | ✓ | ✓ | ✓ | 577 | $0.6420 |
| bulk_migrate | Config1 | 3 | showcase | 196.50 | 0 | N | ✓ | ✓ | ✓ | 573 | $0.6279 |
| db1_shared_state | Config2 | 1 | tune | 41.56 | 0 | N | ✓ | ✓ | ✓ | 209 | $0.2258 |
| db1_shared_state | B (unkeyed) | 1 | tune | 167.56 | 0 | N | ✓ | ✓ | ✓ | 212 | $0.2269 |
| db1_shared_state | Config3 | 1 | tune | 69.92 | 0 | N | ✓ | ✓ | ✓ | 154 | $0.0930 |
| ex1_call_chain | Config2 | 1 | tune | 35.88 | 0 | N | ✓ | ✓ | ✓ | 178 | $0.2306 |
| ex1_call_chain | B (unkeyed) | 1 | tune | 48.10 | 0 | N | ✓ | ✓ | ✓ | 223 | $0.2313 |
| ex1_call_chain | Config3 | 1 | tune | 49.54 | 0 | N | ✓ | ✓ | ✓ | 203 | $0.2794 |
| me1_rename_func | Config2 | 1 | holdout | 46.88 | 0 | N | ✓ | ✓ | ✓ | 102 | $0.2508 |
| me1_rename_func | B (unkeyed) | 1 | holdout | 79.10 | 0 | N | ✓ | ✓ | ✓ | 118 | $0.2524 |
| me1_rename_func | Config3 | 1 | holdout | 43.13 | 0 | N | ✓ | ✓ | ✓ | 120 | $0.1203 |
| mf1_cache_layer | Config2 | 1 | tune | 32.92 | 0 | N | ✓ | ✓ | ✓ | 145 | $0.0946 |
| mf1_cache_layer | B (unkeyed) | 1 | tune | 61.69 | 0 | N | ✓ | ✓ | ✓ | 231 | $0.2554 |
| mf1_cache_layer | Config3 | 1 | tune | 41.67 | 0 | N | ✓ | ✓ | ✓ | 227 | $0.2533 |
| sec1_path_traversal | Config2 | 1 | holdout | 38.49 | 0 | N | ✓ | ✓ | ✓ | 301 | $0.2291 |
| sec1_path_traversal | B (unkeyed) | 1 | holdout | 40.84 | 0 | N | ✓ | ✓ | ✓ | 164 | $0.2289 |
| sec1_path_traversal | Config3 | 1 | holdout | 31.01 | 0 | N | ✓ | ✓ | ✓ | 152 | $0.2278 |
| sf1_off_by_one | Config2 | 1 | tune | 34.54 | 0 | N | ✓ | ✓ | ✓ | 110 | $0.2236 |
| sf1_off_by_one | B (unkeyed) | 1 | tune | 50.05 | 0 | N | ✓ | ✓ | ✓ | 111 | $0.2263 |
| sf1_off_by_one | Config3 | 1 | tune | 35.91 | 0 | N | ✓ | ✓ | ✓ | 172 | $0.2504 |
