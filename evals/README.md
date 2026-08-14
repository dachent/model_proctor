# Kimi Code CLI Benchmark Harness

A/B/C benchmark comparing Kimi Code CLI configurations on a fixed suite of 20 coding tasks.

## Overview

We built a multi-model routing harness for Kimi Code CLI. This benchmark answers: **does the routing harness beat plain Kimi Code on success rate, wall-clock, and cost per successful task?**

Three configurations are compared:

- **Config A** — baseline plain Kimi Code (empty skills dir `evals/skills/A`)
- **Config B** — intermediate variant (skills in `evals/skills/B`)
- **Config C** — candidate routing harness (skills in `evals/skills/C`)

Config isolation uses `kimi.exe --skills-dir <dir> -p "<prompt>"`, which replaces all auto-discovered skills with only the specified directory. Each config maps to a skills directory; the runner does not author skill files.

## Files

```
evals/
├── cases.yaml          # 20 pre-registered cases (JSON-in-YAML, see below)
├── fixtures/           # 20 deterministic fixture generators (gen_<name>.py)
├── skills/             # Config skills directories (A/, B/, C/)
│   ├── A/              # empty (baseline)
│   ├── B/              # (you author these)
│   └── C/              # (you author these)
├── run_eval.py         # runner: launches kimi, records results
├── report.py           # scorecard generator (blinded)
├── README.md           # this file
├── results.jsonl       # one JSON line per run (created by run_eval.py)
├── blinding-key.json   # random Config1/2/3 → A/B/C mapping (created by run_eval.py)
└── scorecard.md        # generated scorecard (created by report.py)
```

## cases.yaml format

The file uses **JSON syntax** (JSON is valid YAML). This avoids a YAML parser dependency — the runner uses Python's stdlib `json` module. The file contains a top-level array of 20 case objects.

### Case fields

| Field | Description |
|-------|-------------|
| `id` | Unique case identifier |
| `category` | `simple_fix`, `exploration`, `mechanical`, `multifile`, `debugging`, `migration`, `security` |
| `set` | `tune` or `holdout` — pre-registered, fixed |
| `fixture` | Generator name → `evals/fixtures/gen_<name>.py` |
| `task_prompt` | Exact prompt sent to the headless kimi run |
| `acceptance` | `{command, expect_exit}` — machine-checkable pass/fail |
| `hidden_test` | `{command, expect_exit}` — check not visible in task_prompt |
| `invariants` | Optional list of behavioral checks (`file_contains`, `file_exists`, `command`) |
| `timeout_seconds` | Per-run timeout (120–240s depending on complexity) |
| `initially_failing` | `true` if acceptance/hidden should fail on unmodified fixture (used by self-test) |

### Category distribution (20 cases)

| Category | Count | Tune | Holdout |
|----------|-------|------|---------|
| simple_fix | 4 | 2 | 2 |
| exploration | 4 | 2 | 2 |
| mechanical | 3 | 1 | 2 |
| multifile | 4 | 1 | 3 |
| debugging | 3 | 1 | 2 |
| migration | 1 | 1 | 0 |
| security | 1 | 0 | 1 |
| **Total** | **20** | **8** | **12** |

## Methodology

### Config isolation

Each run launches `kimi.exe --skills-dir <dir> -p "<prompt>"` with `cwd = fixture dir`. The `--skills-dir` flag replaces all auto-discovered skills with only the specified directory, providing clean config isolation. No other config differences exist between runs.

### Fixture determinism

Each fixture generator (`gen_<name>.py`) builds a small self-contained Python project into a target directory given as `argv[1]`. Generators are deterministic — fixed content, no timestamps, seeded randomness only if needed. Each run regenerates the fixture fresh into its own run directory, so no state leaks between runs.

### Reps

- Default: **3 reps** per (case × config)
- `simple_fix` category: **5 reps** (faster runs, more statistical signal)
- Override with `--reps N`

### Cost accounting (uniform heuristic)

No per-provider token APIs are available across configs. We use a uniform heuristic:

```
est_tokens = (len(task_prompt_utf8) + len(agent_stdout_utf8)) // 4
```

This is a proxy — it measures I/O volume, not actual compute cost. It is uniform across configs (same formula, same inputs), so relative comparisons are fair, but absolute numbers are not meaningful. When kimi reports actual token counts in output, they are parsed and recorded as `tokens_reported` (null by default — kimi prints none in headless mode).

**Limitation:** `est_tokens` does not capture reasoning-chain length, internal retries, or tool-call overhead. A config that produces concise correct answers scores better; a verbose config scores worse. This is intentional for cost comparison but not a true API cost measure.

### Blinded scoring

After all runs complete, `run_eval.py` writes `blinding-key.json` — a random permutation mapping `Config1/2/3 → A/B/C`. The scorecard (`report.py`) uses blinded labels throughout, so the human reading the scorecard does not know which letter is the routing harness until the key is revealed.

### Pre-registered sets and decision rules

The tune/holdout split is **fixed in `cases.yaml`** before any runs. The decision rules below use **holdout only** — tuning on the tune set and evaluating on holdout prevents teaching-to-the-test.

#### Adoption rule

Config C is adopted only if ALL of:
1. Holdout success rate ≥ Config A's holdout success rate
2. Holdout cost-per-successful-task < Config A's
   - Cost per successful task = total `est_tokens` for the config / successful runs
   - If a config has zero successes, cost-per-success is undefined and success rate decides

#### Simple-case guardrail

Config C must not be slower than Config A on any `simple_fix` case median wall-clock. This prevents adopting a config that improves hard cases but regresses on trivial ones.

### Hidden tests

Each case has a `hidden_test` — a check whose existence is **not visible in the task_prompt**. This guards against solutions that pass the visible acceptance check but fail on unseen edge cases (regression proxy). The hidden test is run after the acceptance test on the agent's modified fixture.

## How to run

### Prerequisites

- Python 3.10.5 (stdlib only, no third-party packages)
- Git Bash on Windows 11
- `kimi.exe` at `C:/Users/BorisVaisman/.kimi-code/bin/kimi.exe` (v0.31.0)
- Skills directories `evals/skills/A`, `evals/skills/B`, `evals/skills/C` (A is empty; B and C you author)

### 1. Self-test (no kimi invocations, no cost)

Verifies all fixture generators run, all check scripts execute, and expected-initial-failures fail while expected-initial-passes pass.

```bash
cd evals
python run_eval.py --self-test
```

### 2. Pilot (small subset, real kimi runs)

Run a few cases across all configs to validate the pipeline end-to-end:

```bash
python run_eval.py --cases sf1_off_by_one,ex1_call_chain,mf1_cache_layer --reps 1
```

### 3. Full benchmark

```bash
# Full run (all 20 cases, default reps)
python run_eval.py

# Holdout only
python run_eval.py --set holdout

# Specific configs
python run_eval.py --configs A,C

# Resume after interruption
python run_eval.py --resume
```

### 4. Generate scorecard

```bash
python report.py
```

Writes `scorecard.md` with blinded labels, per-case medians, per-category aggregates, adoption verdict, guardrail check, first-pass success, regression proxy, and raw run table.

## Run directories

Fixture run dirs live **outside OneDrive** to avoid sync contention:

```
C:/Dev/bootstrap-state/kimi-router/evals/runs/<case_id>/<config>/<rep>/
```

Each run dir contains:
- The regenerated fixture project
- `agent_stdout.txt` / `agent_stderr.txt` (captured kimi output)

`results.jsonl`, `blinding-key.json`, and `scorecard.md` live in `evals/` (the working directory).
