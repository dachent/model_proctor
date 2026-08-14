# Magnitude-Style Open-Model Coding Harness
## Final Reverse-Engineered Routing Plan and Implementation Scaffold

**Status:** Adversarially reviewed reconstruction  
**Target:** Kimi Code CLI-based harness  
**Primary objective:** Reproduce Magnitude’s likely routing advantages using open models, explicit roles, isolated contexts, automatic delegation, independent review, and bounded escalation  
**Design constraint:** Keep the transport and wrapper layer lean. Do not introduce ACP, MCP, HTTP services, persistent agent sessions, or a general orchestration framework unless later evidence proves they are necessary.

---

## 1. Executive conclusion

Magnitude’s defensible routing advantage is not a magical learned model selector.

It is best reconstructed as a **hierarchical, stage-aware orchestration policy**:

1. A persistent leader maintains user intent and project state.
2. The leader identifies the current software-development stage.
3. It decomposes work into bounded tasks.
4. It selects a specialized worker role for each task.
5. Each role maps to a preferred model profile.
6. Workers run in isolated contexts.
7. The leader validates outputs and controls transitions.
8. Independent review is mandatory for material changes.
9. Repeated failure triggers diagnosis, replanning, or advisory escalation.
10. Provider capability, availability, context, and cost constrain model resolution.

The practical reproduction should use:

- **Kimi K3** as persistent leader and integrator.
- **GLM-5.2** as the native secondary reasoning model.
- **DeepSeek** through a lean `delegate` subprocess wrapper for scouting and inexpensive bulk analysis.
- **Kimi coding workers** for bounded implementation.
- **Fresh-context critics** for independent review.
- **GPT-5.6, Opus 5, and Fable 5** as advisory-only escalation models. They must not directly edit the workspace.
- No ACP initially. Hide transport behind one stable `delegate` interface so ACP can replace subprocess execution later without changing routing policy.

---

## 2. Evidence-based reconstruction

### Confirmed from public Magnitude material

Magnitude publicly exposes or describes:

- A persistent leader agent.
- Specialized worker roles.
- Isolated worker context windows.
- Delegation for exploration, planning, implementation, debugging, and review.
- A software workflow of context gathering, design, planning, implementation, and review.
- Fresh-context independent review.
- Lower-cost workers for token-heavy tasks.
- Reasoning-effort controls.
- Provider-model metadata including context size, output limits, availability, pricing, vision, tools, structured output, and reasoning options.
- Model-family normalization across inconsistent provider naming.
- Loop detection, malformed-tool-call handling, and overthinking controls.
- Parallel workers for independent tasks.

### Strong inference

The leader itself likely performs most routing decisions through prompted reasoning rather than a separate trained classifier.

The main route is therefore:

```text
session state
→ identify current phase and uncertainty
→ select worker role
→ resolve role to eligible model
→ run isolated worker
→ validate result
→ continue, retry, review, or escalate
```

### Not established

Do not claim the following without new evidence:

- A learned routing model.
- Reinforcement learning or contextual-bandit routing.
- Online learning from user acceptance.
- Per-repository automatic model benchmarking.
- A universal numerical difficulty score.
- Exact internal thresholds.
- Exact fallback chains.
- Exact production prompts.
- Direct cost minimization on every turn.

---

## 3. Adversarial review swarm

The reconstruction was challenged from five independent perspectives.

### Reviewer A: Source-fidelity critic

**Attack:** The earlier reconstruction may confuse public marketing claims, prompt instructions, and actual executable routing code.

**Finding:** Valid criticism.

**Correction:**

- Treat role definitions and workflow stages as confirmed.
- Treat exact model-role bindings as configurable defaults, not immutable facts.
- Treat pricing-aware selection as possible but unproven.
- Separate policy from provider resolution.
- Label every non-source-backed mechanism as an implementation recommendation.

### Reviewer B: Routing-theory critic

**Attack:** A stage-based role router may be too coarse. A task can require exploration, architecture, coding, and debugging simultaneously.

**Finding:** Valid.

**Correction:**

- Route **subtasks**, not whole user requests.
- Allow each task to move through multiple phases.
- Reclassify after every material result or failure.
- Use a task graph rather than one global phase variable.
- Permit mixed parallel phases when dependencies allow.

### Reviewer C: Reliability critic

**Attack:** Letting the leader freely delegate can create runaway cost, duplicate work, conflicting edits, and endless review loops.

**Finding:** Valid.

**Correction:**

- Add explicit concurrency, retry, token, time, and escalation budgets.
- Separate read-only and write-capable workers.
- Give each writable task exclusive file ownership.
- Require a result schema.
- Limit review-fix cycles.
- Detect stagnation and repeated evidence.
- Keep all workspace mutation under leader-controlled policy.

### Reviewer D: Model-specialization critic

**Attack:** Static model-role mappings can become stale as models change. “DeepSeek scouts, Kimi codes” may stop being optimal.

**Finding:** Valid.

**Correction:**

- Bind roles to **capability profiles**, not hard-coded model names.
- Keep preferred models in configuration.
- Add a small recurring benchmark suite.
- Promote or demote models only from measured repository-specific evidence.
- Preserve deterministic fallbacks.

### Reviewer E: Benchmark-skeptic critic

**Attack:** Claimed lower cost and SOTA performance may be benchmark-specific or caused by greater token usage, hidden retries, or favorable task selection.

**Finding:** Valid.

**Correction:**

Track:

- task success;
- regression rate;
- human intervention;
- elapsed time;
- total tokens;
- provider cost;
- tool calls;
- retries;
- review defects;
- post-review defects;
- model and role attribution.

Compare the scaffold against a single strong baseline on the same tasks.

---

## 4. Final architecture

```text
User
  │
  ▼
Kimi K3 Leader
  ├─ maintains user intent and task graph
  ├─ classifies subtasks by phase, uncertainty, risk, and capability
  ├─ delegates work
  ├─ integrates results
  ├─ controls all write permissions
  └─ decides retries, reviews, and escalation
       │
       ├─ Native GLM-5.2 secondary
       │    ├─ architecture
       │    ├─ difficult planning
       │    ├─ ambiguous reasoning
       │    └─ adjudication
       │
       ├─ Lean `delegate` wrapper
       │    ├─ DeepSeek scout
       │    ├─ DeepSeek analyst
       │    ├─ Kimi engineer CLI
       │    ├─ fresh-context critic
       │    └─ optional specialist CLIs
       │
       └─ Advisory-only escalation
            ├─ GPT-5.6 via Codex CLI
            ├─ Opus 5 via Claude Code CLI
            └─ Fable 5 via CCC
```

Advisers receive snapshots, diffs, logs, or design documents. They return analysis only. They never modify the repository.

---

## 5. Core routing dimensions

Route each subtask using these dimensions.

### 5.1 Workflow phase

```text
EXPLORE
DESIGN
PLAN
IMPLEMENT
VERIFY
DEBUG
REVIEW
INTEGRATE
```

### 5.2 Uncertainty

```text
LOW
MEDIUM
HIGH
```

Examples:

- Low: exact file, exact change, known test.
- Medium: bounded area but several plausible implementations.
- High: unclear requirements, architecture choice, unknown failure cause.

### 5.3 Risk

```text
LOW
MEDIUM
HIGH
CRITICAL
```

Increase risk for:

- authentication;
- authorization;
- payments;
- data deletion;
- migrations;
- concurrency;
- cryptography;
- public APIs;
- infrastructure;
- security boundaries;
- broad refactors.

### 5.4 Capability needs

```text
repository_search
long_context
code_generation
deep_reasoning
vision
web_research
structured_output
tool_use
high_creativity
```

### 5.5 Execution permission

```text
READ_ONLY
WRITE_BOUNDED
ADVISORY_ONLY
```

---

## 6. Role definitions

### Leader

**Preferred model:** Kimi K3  
**Permission:** Full orchestration; workspace changes only under explicit policy  
**Responsibilities:**

- preserve user intent;
- maintain task graph;
- create bounded tasks;
- classify work;
- choose roles;
- enforce budgets;
- integrate results;
- resolve conflicts;
- approve writes;
- present final output.

The leader should avoid spending large amounts of context on mechanical exploration or implementation.

### Scout

**Preferred model:** DeepSeek fast model  
**Permission:** Read-only  
**Use for:**

- repository mapping;
- symbol and dependency tracing;
- locating relevant tests;
- documentation research;
- log triage;
- gathering facts.

**Output:** Evidence-backed report with file paths, symbols, commands, and uncertainties.

### Architect

**Preferred model:** GLM-5.2 with high reasoning  
**Permission:** Read-only  
**Use for:**

- cross-cutting design;
- API boundaries;
- migration strategy;
- architectural tradeoffs;
- high-ambiguity tasks.

**Output:** Design with alternatives, decision, consequences, invariants, and risks.

### Planner

**Preferred model:** GLM-5.2 or Kimi K3  
**Permission:** Read-only  
**Use for:**

- converting an accepted design into atomic implementation tasks;
- dependency ordering;
- assigning file ownership;
- defining tests and acceptance criteria.

### Engineer

**Preferred model:** Kimi coding model  
**Permission:** Write-bounded  
**Use for:**

- implementing approved, bounded tasks;
- adding tests;
- mechanical refactors;
- fixing confirmed defects.

Each engineer receives:

- one task;
- explicit file scope;
- acceptance criteria;
- relevant design references;
- commands to run;
- prohibited files or operations.

### Scientist

**Preferred model:** GLM-5.2 with high reasoning  
**Permission:** Read-only by default  
**Use for:**

- ambiguous bugs;
- nondeterministic behavior;
- repeated failures;
- performance regressions;
- hypothesis testing.

**Output:** Ranked hypotheses, evidence, experiments, and recommended next action.

### Critic

**Preferred model:** Kimi or GLM in a fresh context  
**Permission:** Read-only  
**Use for:**

- independent diff review;
- requirement compliance;
- correctness;
- edge cases;
- regressions;
- security;
- test sufficiency.

The critic must not receive the implementer’s hidden reasoning. It may receive the user requirement, design, plan, diff, and repository access.

### Artisan

**Preferred model:** GLM with higher temperature  
**Permission:** Read-only or write-bounded  
**Use for:**

- UI concepts;
- visual alternatives;
- naming;
- UX copy;
- creative design.

### Adviser

**Preferred models:** GPT-5.6, Opus 5, Fable 5  
**Permission:** Advisory-only  
**Use only when:**

- two internal reviewers disagree;
- a critical-risk design remains uncertain;
- repeated internal attempts fail;
- a major architectural decision has high irreversible cost;
- a final apex review is explicitly justified.

---

## 7. Routing policy

### 7.1 Primary decision function

```python
def choose_role(task, state):
    if task.requires_user_interaction:
        return "leader"

    if task.phase == "EXPLORE":
        return "scout"

    if task.phase == "DESIGN":
        return "architect"

    if task.phase == "PLAN":
        return "planner"

    if task.phase == "IMPLEMENT":
        if task.specification_complete and task.file_scope_bounded:
            return "engineer"
        return "architect"

    if task.phase == "VERIFY":
        return "engineer" if task.verification_is_mechanical else "scientist"

    if task.phase == "DEBUG":
        return "scientist"

    if task.phase == "REVIEW":
        return "critic"

    if task.phase == "INTEGRATE":
        return "leader"

    return "leader"
```

### 7.2 Escalation modifiers

```python
def modify_route(task, state, route):
    if task.risk in {"HIGH", "CRITICAL"}:
        task.requires_independent_review = True

    if task.uncertainty == "HIGH" and route == "engineer":
        route = "architect"

    if state.same_failure_count >= 2:
        route = "scientist"

    if state.same_failure_count >= 3:
        task.requires_advisory_review = True

    if state.review_disagreement:
        route = "leader"
        task.requires_adjudication = True

    return route
```

### 7.3 Model resolution

Resolve a role to a model only after filtering for capability and availability.

```python
def resolve_model(role, task, registry):
    profile = ROLE_PROFILES[role]

    eligible = [
        model for model in registry
        if model.available
        and model.supports_tools >= profile.requires_tools
        and model.supports_structured_output >= profile.requires_structured_output
        and model.context_window >= task.minimum_context
        and (not task.needs_vision or model.vision)
        and model.reasoning_level >= profile.minimum_reasoning
    ]

    preferred = [
        model for model in eligible
        if model.id in profile.preferred_models
    ]

    pool = preferred or eligible
    return deterministic_score(pool, task, profile)
```

Use deterministic scoring first. Do not add an LLM model-selector until measurements demonstrate that deterministic policy is insufficient.

Suggested score:

```text
score =
  quality_weight × benchmark_score_for_role
+ reliability_weight × recent_success_rate
+ latency_weight × normalized_speed
+ cost_weight × inverse_expected_cost
- failure_weight × recent_failure_rate
```

For high-risk tasks, quality and reliability dominate cost. For scouting, speed and cost receive greater weight.

---

## 8. Task graph and phase transitions

Do not assign one phase to the entire user request. Maintain phases per task.

```json
{
  "task_id": "T-104",
  "parent_id": "T-100",
  "title": "Implement token refresh rotation",
  "phase": "IMPLEMENT",
  "status": "READY",
  "uncertainty": "LOW",
  "risk": "HIGH",
  "permission": "WRITE_BOUNDED",
  "owner_role": "engineer",
  "allowed_paths": [
    "src/auth/refresh.ts",
    "tests/auth/refresh.test.ts"
  ],
  "depends_on": ["T-101", "T-102"],
  "acceptance_criteria": [
    "Old refresh token becomes invalid after rotation",
    "Concurrent reuse is rejected",
    "Existing login tests remain green"
  ],
  "attempt": 1,
  "max_attempts": 2
}
```

Allowed transitions:

```text
EXPLORE → DESIGN
EXPLORE → PLAN
EXPLORE → DEBUG
DESIGN → PLAN
PLAN → IMPLEMENT
IMPLEMENT → VERIFY
VERIFY → REVIEW
DEBUG → IMPLEMENT
REVIEW → IMPLEMENT
REVIEW → DESIGN
REVIEW → COMPLETE
```

The leader may return to an earlier stage when a foundational issue appears.

---

## 9. Delegation contract

Every delegated task must contain:

```text
ROLE
OBJECTIVE
BACKGROUND
REQUIRED INPUTS
WORKSPACE
PERMISSION
ALLOWED PATHS
PROHIBITED ACTIONS
ACCEPTANCE CRITERIA
COMMANDS TO RUN
OUTPUT SCHEMA
TIMEOUT
```

Example:

```markdown
ROLE: Engineer

OBJECTIVE:
Implement refresh-token rotation exactly as specified in `$M/designs/auth-rotation.md`.

WORKSPACE:
`/workspace/project`

PERMISSION:
WRITE_BOUNDED

ALLOWED PATHS:
- `src/auth/refresh.ts`
- `tests/auth/refresh.test.ts`

PROHIBITED:
- dependency changes
- schema changes
- edits outside allowed paths
- force operations
- network access

ACCEPTANCE:
- old token invalid after rotation
- concurrent reuse rejected
- all targeted tests pass

VERIFY:
`npm test -- tests/auth/refresh.test.ts`

RETURN:
A JSON result matching the worker-result schema.
```

---

## 10. Worker result schema

All workers return one JSON object.

```json
{
  "status": "success",
  "summary": "Implemented refresh-token rotation and added concurrency tests.",
  "evidence": [
    {
      "type": "file",
      "path": "src/auth/refresh.ts",
      "detail": "Added atomic token-family replacement."
    },
    {
      "type": "command",
      "command": "npm test -- tests/auth/refresh.test.ts",
      "exit_code": 0
    }
  ],
  "changed_files": [
    "src/auth/refresh.ts",
    "tests/auth/refresh.test.ts"
  ],
  "findings": [],
  "uncertainties": [],
  "recommended_next": "review",
  "usage": {
    "elapsed_ms": 0,
    "input_tokens": null,
    "output_tokens": null,
    "estimated_cost_usd": null
  }
}
```

Allowed statuses:

```text
success
partial
blocked
failed
timeout
policy_violation
```

A worker must never describe success without evidence.

---

## 11. Lean delegation wrapper

Use one stable CLI:

```text
delegate --agent <name> --workspace <path> \
  [--task <text> | --task-file <path>] \
  [--timeout <seconds>]
```

### Required implementation properties

- Python 3 standard library only.
- Agent commands configured in `agents.json`.
- Never accept user-supplied executable names.
- Validate agent name, workspace, task, and timeout.
- Resolve workspace and require an existing directory.
- Use `subprocess.Popen`.
- Use argument arrays.
- Use `shell=False`.
- Set `cwd` to the workspace.
- Allow only configured environment variables.
- Capture stdout and stderr.
- Bound output size.
- Create one temporary run directory.
- Terminate the full process group on timeout or interruption.
- Return one JSON result.
- Stateless execution.
- No retries in the wrapper.
- No routing logic in the wrapper.
- No concurrency controller in the wrapper.
- No ACP, MCP, HTTP server, plugin system, or session database.

The wrapper is transport. The leader is policy.

### Example `agents.json`

```json
{
  "deepseek-scout": {
    "command": ["deepseek-cli", "--mode", "read-only"],
    "default_timeout": 600,
    "prompt_delivery": "stdin",
    "environment_allowlist": ["HOME", "PATH", "M"]
  },
  "kimi-engineer": {
    "command": ["kimi-code", "--non-interactive"],
    "default_timeout": 1200,
    "prompt_delivery": "stdin",
    "environment_allowlist": ["HOME", "PATH", "M"]
  },
  "critic": {
    "command": ["kimi-code", "--non-interactive", "--read-only"],
    "default_timeout": 900,
    "prompt_delivery": "stdin",
    "environment_allowlist": ["HOME", "PATH", "M"]
  },
  "gpt-adviser": {
    "command": ["codex", "exec", "--sandbox", "read-only"],
    "default_timeout": 900,
    "prompt_delivery": "stdin",
    "environment_allowlist": ["HOME", "PATH", "M"]
  },
  "opus-adviser": {
    "command": ["claude", "--print", "--permission-mode", "plan"],
    "default_timeout": 900,
    "prompt_delivery": "stdin",
    "environment_allowlist": ["HOME", "PATH", "M"]
  }
}
```

Exact CLI flags must be adapted to installed versions. Keep semantic permissions unchanged.

---

## 12. Write isolation

Parallel writable workers must not edit overlapping files.

Before dispatch:

```python
def can_run_in_parallel(a, b):
    if a.permission != "WRITE_BOUNDED":
        return True
    if b.permission != "WRITE_BOUNDED":
        return True
    return set(a.allowed_paths).isdisjoint(b.allowed_paths)
```

Preferred options:

1. Separate worktrees per writable worker.
2. Separate branches with leader-controlled integration.
3. Sequential writes when isolation is not available.

Do not let multiple workers share one mutable working tree merely because parallelism looks impressive in a terminal demo.

---

## 13. Review policy

Material code changes require an independent fresh-context review.

### Review inputs

- original user requirement;
- accepted design;
- implementation plan;
- diff;
- relevant tests;
- repository access;
- known constraints.

### Review categories

```text
requirement_compliance
correctness
edge_cases
regressions
security
concurrency
data_integrity
error_handling
test_quality
maintainability
scope_discipline
```

### Review output

```json
{
  "verdict": "changes_required",
  "findings": [
    {
      "severity": "high",
      "category": "concurrency",
      "location": "src/auth/refresh.ts:84",
      "claim": "Two concurrent requests can both validate before invalidation.",
      "evidence": "Validation and invalidation occur in separate transactions.",
      "recommended_fix": "Use one atomic compare-and-replace operation.",
      "confidence": 0.93
    }
  ]
}
```

The leader independently validates findings. A critic does not automatically control the implementation.

### Review loop limits

```text
Maximum ordinary review-fix cycles: 2
Third cycle: Scientist diagnosis or Architect re-evaluation
Fourth unresolved cycle: advisory escalation or user decision
```

---

## 14. Failure and loop detection

Track these signals per task and worker:

- repeated identical tool call;
- repeated file reads without new findings;
- repeated command failure;
- unchanged hypothesis after failure;
- no workspace diff after implementation attempts;
- oscillation between two fixes;
- repeated critic finding;
- excessive output without evidence;
- malformed structured output;
- timeout;
- scope violation;
- rising cost without measurable progress.

### Stagnation score

```python
stagnation = (
    2 * repeated_command_failures
    + repeated_tool_calls
    + repeated_file_reads
    + 2 * unchanged_hypotheses
    + 3 * scope_violations
    + 2 * malformed_results
)
```

Suggested actions:

```text
score 0–2: continue
score 3–4: leader redirects with explicit next test
score 5–6: stop worker; route to Scientist
score 7+: return to Design or escalate
```

These thresholds are initial defaults, not claims about Magnitude’s internals.

---

## 15. Budget controls

Configure per run:

```yaml
budgets:
  max_parallel_workers: 4
  max_write_workers: 2
  max_attempts_per_task: 2
  max_review_cycles: 2
  max_advisory_calls: 2
  max_total_elapsed_minutes: 90
  max_total_cost_usd: 25
  max_worker_output_bytes: 200000
```

Recommended priority order:

```text
correctness
security
requirement fidelity
reliability
elapsed time
cost
```

For low-risk scouting:

```text
sufficient accuracy
speed
cost
```

For critical code:

```text
correctness
independent evidence
quality
cost
```

---

## 16. Advisory escalation

Advisers are not ordinary workers.

### Trigger only when

- high-risk architecture remains disputed;
- two independent reviewers disagree materially;
- three internal attempts fail;
- the leader detects a likely blind spot;
- the user explicitly requests apex review.

### Advisory packet

```text
QUESTION
USER REQUIREMENT
RELEVANT DESIGN
DIFF OR FAILURE LOGS
KNOWN HYPOTHESES
WHAT HAS ALREADY BEEN TRIED
REQUESTED DECISION FORMAT
```

### Required adviser output

```json
{
  "recommendation": "...",
  "reasoning_summary": "...",
  "key_risks": [],
  "disagreements_with_current_plan": [],
  "confidence": 0.0,
  "required_evidence": []
}
```

The leader adjudicates. Advisers never write files.

---

## 17. Context management

Use a scratch directory such as `$M`.

```text
$M/
  task-state.json
  reports/
  designs/
  plans/
  reviews/
  advisory/
  results/
  metrics/
```

Workers should receive file references, not large repeated prompts.

Persist:

- user requirements;
- accepted decisions;
- task graph;
- worker reports;
- designs;
- plans;
- review findings;
- unresolved risks;
- model usage metrics.

Do not persist irrelevant hidden reasoning.

---

## 18. Model registry

```yaml
models:
  kimi-k3:
    provider: native
    roles: [leader, planner, integrator]
    reasoning: high
    tools: true
    structured_output: true

  glm-5.2:
    provider: native-secondary
    roles: [architect, scientist, adjudicator]
    reasoning: high
    tools: true
    structured_output: true

  deepseek-fast:
    provider: delegate
    roles: [scout, analyst]
    reasoning: medium
    tools: true
    structured_output: true

  kimi-code:
    provider: delegate
    roles: [engineer, critic]
    reasoning: medium
    tools: true
    structured_output: true

  gpt-5.6:
    provider: delegate
    roles: [adviser]
    permission: advisory_only

  opus-5:
    provider: delegate
    roles: [adviser]
    permission: advisory_only

  fable-5:
    provider: delegate
    roles: [adviser]
    permission: advisory_only
```

Do not route Kimi’s native secondary slot through the wrapper. Use GLM-5.2 directly as the native secondary. Use the wrapper for external CLIs.

---

## 19. Configuration scaffold

```yaml
routing:
  leader: kimi-k3

  roles:
    scout:
      preferred: [deepseek-fast]
      permission: read_only
      max_parallel: 3

    architect:
      preferred: [glm-5.2, kimi-k3]
      permission: read_only
      reasoning: high

    planner:
      preferred: [glm-5.2, kimi-k3]
      permission: read_only

    engineer:
      preferred: [kimi-code]
      permission: write_bounded
      requires_plan: true
      requires_allowed_paths: true

    scientist:
      preferred: [glm-5.2]
      permission: read_only
      reasoning: high

    critic:
      preferred: [kimi-code, glm-5.2]
      permission: read_only
      fresh_context: true

    adviser:
      preferred: [gpt-5.6, opus-5, fable-5]
      permission: advisory_only
      max_calls_per_run: 2

policy:
  mandatory_review:
    - risk >= HIGH
    - changed_files > 2
    - public_api_change
    - migration
    - authentication
    - authorization
    - concurrency
    - payment
    - data_deletion

  escalation:
    scientist_after_same_failure: 2
    adviser_after_same_failure: 3
    architect_after_foundational_review_finding: true

  parallelism:
    max_workers: 4
    max_write_workers: 2
    disallow_overlapping_write_paths: true

  budgets:
    max_attempts_per_task: 2
    max_review_cycles: 2
    max_total_cost_usd: 25
    max_total_elapsed_minutes: 90
```

---

## 20. Leader control loop

```python
async def run(request):
    state = initialize_state(request)
    task_graph = await leader_decompose(request, state)

    while not task_graph.complete:
        ready = task_graph.ready_tasks()

        batches = schedule_non_conflicting(ready)

        for batch in batches:
            results = await execute_batch(batch)

            for task, result in results:
                validate_schema(result)
                validate_scope(task, result)
                record_metrics(task, result)
                update_task(task, result)

                if result.status == "success":
                    advance_phase(task)
                elif result.status in {"partial", "blocked"}:
                    leader_resolve_blocker(task, result)
                else:
                    increment_failure(task)
                    apply_failure_policy(task)

        for task in task_graph.tasks_requiring_review():
            review = await run_fresh_critic(task)
            findings = leader_validate_findings(review)

            if findings:
                route_findings(task, findings)
            else:
                mark_review_complete(task)

        enforce_budgets(state)
        detect_stagnation(state)
        compact_context(state)

    final_checks(task_graph)
    return leader_present(task_graph)
```

---

## 21. Minimum viable implementation order

### Phase 1: Lean delegation

Implement:

- `delegate`;
- `agents.json`;
- process-group termination;
- bounded outputs;
- JSON result envelope;
- read-only and write-bounded agent definitions.

Do not add orchestration infrastructure.

### Phase 2: Role prompts

Create:

```text
prompts/leader.md
prompts/scout.md
prompts/architect.md
prompts/planner.md
prompts/engineer.md
prompts/scientist.md
prompts/critic.md
prompts/adviser.md
```

### Phase 3: Task state

Add:

- `task-state.json`;
- task IDs;
- dependencies;
- phases;
- status;
- attempts;
- file ownership;
- acceptance criteria.

### Phase 4: Routing

Implement deterministic role selection from:

- phase;
- uncertainty;
- risk;
- capability;
- permission;
- failure count.

### Phase 5: Review and failure control

Add:

- fresh critic;
- review schema;
- leader adjudication;
- stagnation score;
- retry limits;
- Scientist escalation;
- advisory-only apex review.

### Phase 6: Measurement

Capture:

- task outcome;
- cost;
- tokens;
- time;
- retries;
- review defects;
- post-review defects;
- model attribution.

Only after this should adaptive model scoring be added.

---

## 22. Benchmark plan

Use 20–50 representative repository tasks.

Include:

- simple bug fix;
- bounded feature;
- cross-file refactor;
- unfamiliar codebase exploration;
- failing test diagnosis;
- concurrency bug;
- migration design;
- security-sensitive change;
- frontend task;
- documentation task.

Compare:

```text
A. Kimi K3 alone
B. Kimi K3 + GLM secondary
C. Full routed scaffold
D. Magnitude, when available
E. One strong frontier baseline
```

Measure:

```text
task success
tests passed
hidden regression tests
human corrections
review defects
post-review defects
elapsed time
total tokens
total cost
tool calls
retries
```

Do not optimize router weights from the same tasks used for final evaluation.

---

## 23. Acceptance criteria

The scaffold is successful when:

- the leader preserves user requirements across workers;
- scouting consumes most bulk-context work;
- engineers receive bounded implementation plans;
- parallel writers never overlap;
- every material change receives independent review;
- repeated failures change strategy rather than repeat;
- advisers remain read-only;
- all worker outputs are structured and evidenced;
- task history and model attribution are inspectable;
- measured quality is at least equal to Kimi K3 alone;
- cost per successful task falls without increasing regressions.

---

## 24. Final judgment

The strongest reconstruction of Magnitude is:

```text
hierarchical leader
+ stage-aware task decomposition
+ role-based model profiles
+ isolated worker contexts
+ bounded parallelism
+ independent review
+ failure-aware escalation
+ provider capability filtering
+ model-specific inference controls
```

The highest-value parts are not sophisticated model-selection mathematics. They are:

1. routing at the subtask level;
2. preserving a persistent leader context;
3. keeping bulk exploration away from the leader;
4. delaying implementation until scope is concrete;
5. reviewing in a fresh context;
6. escalating repeated failure to a different cognitive role;
7. enforcing permissions, file ownership, budgets, and evidence.

Build those first. Add adaptive model selection only after the harness produces trustworthy measurements.

---

## 25. Public source anchors

- Magnitude repository: `https://github.com/magnitudedev/magnitude`
- Leader prompt: `packages/roles/src/prompts/leader.txt`
- Provider model schema: `packages/ai/src/provider/model.ts`
- Provider model-ID classifier tests: `packages/providers/src/classifier/__tests__/atomizer.test.ts`
- Magnitude website: `https://magnitude.dev`
- Y Combinator company profile: `https://www.ycombinator.com/companies/magnitude`

These sources support the role-based orchestration, isolated workers, workflow stages, provider capability model, reasoning controls, and model-family normalization. Exact private production routing rules remain undisclosed.
