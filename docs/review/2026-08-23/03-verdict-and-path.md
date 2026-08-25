# Verdict and path: session audit, containment, and development decision

Date: 2026-08-23
Repo: dachent/kimi_router (public; formerly dachent/robot_lockstep_ballast, renamed 2026-08-25)
Signed-off-by: agent session (evidence at issues #15, #16, #17–#24)

---

## Is the target well-defined?

Originally no; after the audit chain (#13–#16), **now yes.**

The first framing ("better than Kimi K3 alone, cheaper") had no falsification rule. The SPEC v3.1 sharpened the objective; #16's meta-audit completed it by defining:

- **Fair baseline:** optimized, fresh-session, cache-correct native K3-max (not the pathological $203 day).
- **Adoption gate:** cost-per-task upper confidence bound < ⅓ of baseline, at non-inferior quality.
- **STOP-prior:** preregistered — the legitimate outcome is "nothing beats baseline → deploy native K3 + context discipline and close the project successfully."

## Is the development on the right path?

**Culture: consistently right.** Pre-registration, blinding, adversarial review, honest `[CODE]/[POLICY]/[RESIDUAL]` labels, and a benchmark that rejected the project's own dynamic router on real metering (+16%/+37%, guardrail fail) demonstrate process discipline rare for solo work.

**Aim: mis-centered until the audits.** The project optimized the ~3.6–5.2% cost slice (workers) while the leader session burned ~96% of spend. After the audits, the goal reoriented: measure fixed arms first, accept STOP as legitimate, build routing only if headroom survives.

**Verdict:** the development *process* is sound; the *target* was wrong until the audit chain corrected it. The current path (#13 + #16's 11-step sequence) is the correct one.

---

## Orchestration logic (current vs expected)

### As built today (static cascade; per `skill/static-cascade/SKILL.md`, `cascade.py`)

- **Planner (per substantial goal):** K3 writes a schema'd task list (executor, verifier cmd, scope, criticality, max attempts).
- **Orchestrator/QC:** GLM-5.2 leader session runs the loop, judges QC fresh-context reviews.
- **Default executor:** DeepSeek V4 Flash (~10× cheaper than K3).
- **Escalation rungs:** Flash → (evidence-driven) → K3 fresh-context → advisor (Codex/Claude) → final K3 retry → stop.
- **Caps (code-enforced):** 5–6 executor invocations/task, 2–3 QC, ≤2 planner/goal, ceiling ~0.6× estimate.
- **Key gates:** verify runs deterministically by controller (worker-reported results never count); `commit-green`/`rollback` are the only git mutations; escalation is one rung at a time; deterministic evidence outranks every model.

### Expected after this plan (measurement-first target)

- Same skeleton, wrapped in a scoreboard: sealed evaluator outside agent-writable boundary; request-ID logging; discriminating corpus; fair-baseline measurement before any routing claims.
- Lanes open only on measured headroom: Pi challenger (conditional after K3-transport conformance), rescue = fresh-session after objective gate, task-level routing only if `q + f·ρ ≤ T` passes, step-level routing last.
- **STOP is preregistered as the likely outcome.**

### Routing-optimality position

The current lineup is a **falsifiable hypothesis, not a proven optimum.**

- Config D (Pro-as-default) measured +36.9% cost vs K3 alone and 3/6 hidden success vs 4/6 baseline.
- Input-dominated cache economics punish premium models in the highest-volume seat (the orchestrator read loop).
- The live routing decision space is small (≤3 legal rungs, test outcomes as real router) — smart-router overhead pays nothing here.
- Mega-analysis: my SOTA survey confirmed no mainstream framework ships deterministic escalation with immutable verifier roles; this niche is unoccupied (contra AUDIT-003's blanket "every layer occupied").

**Open items folded into protocol:** (a) GLM-5.2 vs Qwen3.8-Max vs Flash-leader as orchestrator (never head-to-head tested); (b) DS-Pro as conditional escalation rung (its edge is cheapest heavy-output reasoning); (c) MiniMax M3 & GPT-OSS-120B as executor candidates.

---

## Lineup review (live Fireworks pricing 2026-08-23 + AA Index v4.1.1)

| Model | $/M input | $/M cached | $/M output | AA Index |
|---|---|---|---|---|
| DeepSeek V4 Flash | 0.22 | 0.007 | 0.66 | 52 |
| GLM-5.2 | 1.40 | 0.14 | 4.40 | 53 |
| DeepSeek V4 Pro | 1.74 | 0.145 | 3.48 | 53 |
| Kimi K2.7 Code | 0.95 | 0.19 | 4.00 | 43 |
| Qwen 3.8 Max | 2.00 | 0.25 | 6.00 | 58 |
| Kimi K3 | 3.00 | 0.30 | 15.00 | 60 |

**Key facts:**

- Pro is ~24% pricier than GLM on input, tied on cache, ~21% cheaper on output — which axis dominates depends on role in/out mix.
- Flash is one index point below Pro at 1/8 price; near-unassailable as bulk executor.
- GLM-5.2 and Pro share AA index 53; Pro has zero independent agentic evaluation; its only defensible slot is conditional escalation for heavy-CoT QC.
- **No public head-to-head orchestrator/judge benchmark exists for any of these models.** (JudgeBench frozen since 2024.)

**Rulings:**

1. Pro demoted from assumed-value to conditional escalation rung.
2. GLM-5.2 keeps orchestrator seat (purpose-built 1M-context long-horizon, cheap cache) but Qwen3.8-Max (+5 index, `preserve_thinking`) is the credible challenger.
3. Flash remains default executor; also a measurable dark-horse LEADER candidate.
4. Executor-candidate pool widened (MiniMax M3, GPT-OSS-120B).
5. SPEC §0's 0%-cache premise contradicted; pricing data stale; break-evens must be recomputed with live prices.

---

## Reconciliation vs audit chain (#13–#16)

| Point | #13 (governing) | #14 (minimalist) | #15 (defect) | #16 (meta) | This doc |
|---|---|---|---|---|---|
| Bespoke cascade loses production authority | Adopted | Adopted | Adopted | Adopted | Adopted |
| Leader ~96% of spend; workers ~3.6% slice | Adopted | Adopted | Adopted | Adopted | Verified + adopted |
| STOP ("native K3 wins") = legitimate outcome | Adopted | — | — | Adopted | Preregistered |
| P0 containment first | Adopted | Adopted | Adopted | Adopted | Executed (6f96807) |
| Fix criticals first? | — | — | Required | — | Deferred to issues #17/#18 |
| "Every layer occupied"? | — | Weak claim | Asserted | — | **Disputed**; unoccupied niche for deterministic gated cascade |
| Stale pricing/§0 cache premise? | — | — | Noted | — | Verified; filed #23 |
| Privacy exposure | — | — | agents.json | 6 files | Verified 6 files; tip-sanitized |

---

## MVP and left-to-MVP gap

**MVP = fair-baseline measurement → stop-or-continue decision.**

1. Refresh corpus so quality can discriminate.
2. Add request-ID logging + two-tier accounting.
3. Run `session_rebuild` metering for optimized native K3-max + GLM-flip savings.
4. Produce one falsifiable scorecard.

If nothing clears the `<⅓` bar — the prediction from economics — you deploy native K3 and the project successfully closes.

**Gaps:** P0 hygiene (done); preregistration v2 with pinned prices; sealed evaluator + append-only ledger; protocol/cache canaries; baseline measurement; decision record.

---

## Privacy exposure appendix

### Summary
Six tracked files at public tip contained personal/employer identifiers (no credentials):
- `delegate/agents.json`
- `evals/README.md`
- `evals/run_eval.py`
- `evals/skills/C/multi-model-routing/SKILL.md`
- `evals/skills/D/multi-model-routing/SKILL.md`
- `AGENTS.md`

First committed: `6095695` (2026-08-13) — public for 10 days.

### Remediation performed
Tip sanitization (portable placeholders) + untracking of agents.json committed locally (6f96807).

### History-rewrite candidates
- `git filter-repo` to purge identifiers across history.
- Followed by force-push + GitHub Support cache purge.
- Risk: forks/clones persist; employer-notification decision separate.

**Decision items (owner):**

1. Push the sanitizing commit to remote? (local-only now)
2. Pursue history rewrite + force-push for full removal?
3. Notify employer of public employer-name exposure?
4. Notify third parties (if any), e.g., "V Capital" named an allowed root?

This doc does not execute any of the above.
