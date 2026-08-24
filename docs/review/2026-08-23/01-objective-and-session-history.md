# Objective and session history: kimi_router (2026-07-30 → 2026-08-23)

> Built on deterministic extraction per `scripts/extract_log.py`. Every quotation carries citation tuple (file, record, context). See `C:\Users\BorisVaisman\.kimi-code\sessions\wd_kimi_router_48279e049987\agents\main\wire.jsonl` for primary wire-log source.

---

## Phase 0 — Question & reverse-engineering (2026-07-30, morning)

**Objective as stated:** *"I want to setup multi-model routing, like what https://magnitude.dev/ does, with the goal of improving performance to be better than Kimi K3 alone … reducing average cost per token… Is this goal achievable?"* (wire.jsonl turn 1)

**Activity:** Two subagents produced `magnitude_reverse_engineered_routing_scaffold.md` ("Adversarially reviewed reconstruction"; conclusion: "Magnitude's defensible routing advantage is not a magical learned model selector"). Two operationalization specs followed; surviving decision: **"do not make the wrapper impersonate the secondary-model API."**

**Learned:** Magnitude's edge is hierarchical, stage-aware orchestration (leader → bounded subtasks → role specialization → fresh-context review), not a learned per-request router.

---

## Phase B — Build & canary day (2026-07-30 15:40–19:30)

**Canaries:** k27-scout, ds-flash, glm: `READY`. Claude-advisor ready. Codex-advisor failed on Windows command-syntax bug. Grok-worker auth failed (401) after repeated retries; later succeeded but was dropped from final roster.

**Adversarial review of delegate.py** (review-result-codex.json, review-result-claude.json) produced Job-Object kill, stdin thread, workspace containment findings.

**Benchmark executed:** 36+ blinded runs (A/B/C/D configs over 8 cases × reps) → `C:\Dev\bootstrap-state\kimi-router\evals\runs`.

---

## Phase C — Verdict on dynamic routing (2026-07-30 → 2026-08-16)

**SHOWCASE prediction:** "C and D beat A on wall-clock ≥ 2× and est-tokens ≥ 3×."

**Reality (results-metered.jsonl):**
- Primary metric proxy broken (`tokens_reported: null` throughout).
- All 18 showcase runs passed acceptance and hidden tests at 1.00 → fixtures never discriminated on quality.
- Hidden-test recount (audit): joint success A 4/6, C 2/6, D 3/6 (quality hurt by routing).
- Real cost vs K3 baseline: C +16.2%, D +36.9%.

**Guardrail:** simple-case wall-clock regression on `sf1_off_by_one` (35.91s vs 34.54s) → FAIL.

**Lesson (TOOL-004):** *"The v1 benchmark rejected dynamic routing on cost (+16%/+37% real metering) but could not say anything about quality."*

---

## Phase D — Literature check → static cascade v3 (2026-08-11–13)

`research-swarm-output.txt` verified FrugalGPT/RouteLLM/RouterBench/EcoAssistant claims.

Two advisor reviews of SPEC v3 produced v3.1. Key inversion: **Flash is default executor**, Pro escalation-only, K3 planner-per-goal, GLM-5.2 orchestrator/QC, Codex/Claude read-only advisors.

---

## Phase E — Install, integration, meta-analysis (2026-08-13 morning)

- Static-cascade skill installed.
- Resume-test canary: first full cascade cycle (t1 dispatched to flash, verify exit 0 PASS).
- Vision test: k27-scout correctly described generated PNG.

**Meta-session:** digest of 15 Codex rollout files (~86MB) from Aug 11–12 quant_engine takeover produced `DIGEST.md` — a ~22-hour session dominated by "PROCEED-loop", goal-mode re-injection, zero remediation code, handoff written twice after rejection. This directly shaped #269/#270 chain and TOOL-001/TOOL-008.

---

## Phase F — Production dogfood (2026-08-13 day → Aug 14)

Harness used on private financial-controls repo:
- `attrib-*` pipeline authored issue #269 (merged as PR #279).
- `hwm-*` runs implemented numerical TWR/HWM; QC round 2 proved wiring impossible → #281.
- CI incident (windows-2025 runner provisioning). Recovery after owner's budget update and rerun green.
- One composite change went through **6+ fresh-context QC rounds**; controller's QC cap exists because "a human leader will not stop itself."
- Leader session cost ~$196 of $203 day (96%); delegated slice ~$9.44 vs $17.85 counterfactual.

**Fidelity phantom-handoff (TOOL-008):** leader marked "capture handed to owner" but no browser opened, no login — *tracked state with no underlying reality*. Produced 5-rule HITL protocol.

---

## Phase G — Consolidation (Aug 15–17)

- Quantbase HAR re-extraction fixing truncated pricehistory.
- Panel/decomposition parity probes.
- Final turn (Aug 17 07:46): dead-end branches deleted; everything on GitHub or in OneDrive tree.

---

## What worked

- Pre-registration + blinded scoring (methods survived negative result).
- Adversarial review culture (delegate.py, SPEC v3, leader-context v2).
- Deterministic extraction + spot-verification pattern (F1/F3 lessons).
- Controller-enforced caps preventing runaway QC loops.

## What failed

- Dynamic per-turn routing (cost +16%/+37%, guardrail fail, quality hurt).
- Token-count instrumentation (null throughout; true cost invisible until retro metering).
- Leader-context discipline (506M cached-read tokens → 96% of spend).
- Phantom handoffs (tracked state only; F5).
- Grok auth, codex Windows syntax, MCP GitHub token missing (transport fragility).

---

## Recurring failure modes

1. **Transport/auth fragility on Windows** (auth failures, command-syntax bugs, log 404s).
2. **Broken instrumentation** (null tokens, non-discriminating fixtures).
3. **Leader-context cost dominance** (96% of spend).
4. **Permission-loop dead-ends** (PROCEED-loop).
5. **Identity/nondeterminism in governed pipelines** (stage-vs-publish drift).
6. **Phantom handoffs**.

---

## Numbers re-derived or marked "claimed"

- **Verified from artifacts:** +16.2% / +36.9% (results-metered.jsonl showcase medians).
- **Claimed, not re-derived:** $196/$203 and 506M (source logs exist at `.kimi-code/sessions/...` but not mechanically extracted this session; marked `[CLAIMED-UNVERIFIED]`).
- **Audit recalculated:** hidden joint success counts (A4/C2/D3-of-6) not present in scorecard; audit generated from raw records.

---

## Sources

- Primary wire-log: `C:\Users\BorisVaisman\.kimi-code\sessions\wd_kimi_router_48279e049987\agents\main\wire.jsonl` (27 MB, 307 turns).
- Tmp artifacts: `.orchestrator/tmp/*` (83 items, Jul 30–Aug 17).
- Eval fixtures & runs: `C:\Dev\bootstrap-state\kimi-router\evals\runs`.
- Audits: GitHub issues #13–#16, #17–#24.
