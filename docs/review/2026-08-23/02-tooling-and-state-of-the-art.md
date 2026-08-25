# Tooling and state of the art (condensed; full parked as #24)

Date: 2026-08-23
Repo: dachent/kimi_router (formerly dachent/robot_lockstep_ballast, renamed 2026-08-25)

---

## Routing services (learned, per-request)

| Service | Mechanism | Status |
|---|---|---|
| Azure Foundry Model Router | Quality/Balanced/Cost modes; per-request classifier | Active/GA |
| AWS Bedrock intelligent routing | Quality-delta-threshold cascade | GA |
| OpenRouter Auto | Task-type classifier + market-signal (trailing spend) | Active |
| NotDiamond | Routing for coding agents; closed | Active |
| FireRouter (Fireworks) | Learned difficulty router; research preview | Alpha |

## Cascade/escalation research lineage

- **FrugalGPT** (2023): cascade plus learned scorer, up to 98% savings.
- **AutoMix**: self-verification + POMDP router; >50% compute reduction.
- **Hybrid LLM** (ICLR 2024): tunable quality target; 40% fewer large-model calls.
- **RouteLLM**: preference-trained routers; >2× cost reduction.
- **Triage** (arXiv:2604.07494, Apr 2026): code-health signals route coding tasks; falsifiable conditions.
- **UCCI / Conformal Cascade**: calibrated/conformal guarantees on cascade accuracy.
- **TwinRouterBench** (arXiv:2605.18859, May 2026): static/dynamic twin eval on SWE-bench Verified; tune/holdout + deterministic no-LLM-judge scoring. **Closest methodological match.**

## Orchestration frameworks (lead/subagent)

- **Claude Code subagents**: per-role model override, hooks, denylists, `opusplan`.
- **OpenAI Agents SDK / Swarm**: handoffs, guardrails, per-agent model config.
- **LangGraph**: low-level graph; deterministic paths achievable.
- **Microsoft Agent Framework (MAF)**: explicit workflows + harness agent; closest to deterministic-gated execution.
- **Magentic-One** (arXiv:2411.04468): ledger-driven orchestrator; no deterministic verification gates.

## Coding-agent tiering

- **Cursor Router**: Auto Cost / Balance / Intelligence.
- **Cline Plan/Act**: read-only plan mode; per-mode model.
- **Aider architect**: two-model pipeline (architect + editor).

**Key finding:** No shipped system combines: deterministic bounded-escalation state machine + immutable verifier roles + git-state gates + evidence hardening + local Windows-native operation. That niche is **unoccupied**.

## Leveragable artifacts

- **TwinRouterBench** — adopt its protocol (tune/holdout, execution-verified tiers, no-LLM-judge scoring).
- **Triage** — reuse code-health routing signals.
- **RouteLLM** — OSS learned-router baseline.
- **Claude Code subagents docs** — role/tool separation spec.
- **Dynamic Model Routing survey (arXiv:2603.04445)** — taxonomy anchor.

---

Full landscaped report (40+ sources) parked as GitHub issue #24 ([DOC-001] SOTA landscape) due to session capacity.
