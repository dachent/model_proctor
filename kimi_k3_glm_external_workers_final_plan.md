# Final Plan: Kimi K3 + GLM-5.2 + Lean External CLI Workers

**Status:** Implementation-ready  
**Target:** Linux first  
**Design goal:** Maximum practical model diversity with minimal orchestration code, a small attack surface, and deterministic failure handling.

---

## 1. Decision

Use a two-tier delegation architecture:

```text
Kimi Code CLI
└── Kimi K3 primary orchestrator
    ├── Native Kimi Agent / AgentSwarm
    │   └── GLM-5.2 secondary model
    └── External delegation command
        └── delegate wrapper
            ├── DeepSeek coding worker
            ├── DeepSeek review worker
            ├── Grok research/review worker
            └── Additional explicitly configured CLI workers
```

Do **not** make the subprocess wrapper impersonate Kimi's secondary-model API.

The native secondary slot expects a model-provider interaction loop. A one-shot CLI wrapper returns a completed subprocess result. Converting that wrapper into a compatible model endpoint would require an HTTP service, streaming, tool-call translation, cancellation, state management, and protocol-specific error handling. That would defeat the purpose of a lean, auditable wrapper.

### Final role allocation

| Role | Model/mechanism | Responsibility |
|---|---|---|
| Lead orchestrator | Kimi K3 | Planning, decomposition, architecture, delegation, conflict resolution, integration, final acceptance |
| Native senior workers | GLM-5.2 via Kimi secondary model | Repository exploration, difficult implementation, debugging, test strategy, independent review |
| External bounded workers | DeepSeek/Grok/others via `delegate` | Narrow implementation, test generation, repetitive refactoring, alternative analysis, independent critique |
| Final verifier | Kimi K3 | Inspect changes, reconcile reports, run integrated checks, approve or reject result |

---

## 2. Why this architecture survives adversarial review

### Advantages

- Preserves Kimi's native subagent semantics for GLM-5.2.
- Adds arbitrary third-party models without modifying Kimi Code CLI.
- Keeps the external integration stateless and replaceable.
- Uses allowlisted commands rather than model-supplied executables.
- Avoids ACP, MCP, HTTP, queues, databases, containers, and session brokers.
- Makes every external invocation independently auditable.
- Allows later addition of a thin MCP adapter without changing the wrapper.

### Explicit limitations

- External CLI workers are not native Kimi subagents.
- They receive only the task and workspace supplied to them.
- They do not share Kimi's conversation state unless K3 includes the relevant context in the delegated task.
- They do not resume sessions.
- They may modify the workspace directly if their configured CLI has write access.
- Parallel writers can conflict unless K3 assigns separate files or separate Git worktrees.
- The wrapper cannot prevent a child CLI from echoing task text or secrets into its own output.
- A worker's final text is untrusted input; K3 must verify its claims and inspect its changes.

---

## 3. Kimi configuration

### 3.1 Primary and secondary models

Configure Kimi K3 as the primary model and GLM-5.2 as the secondary model in Kimi Code CLI.

Illustrative structure only; retain the provider and model identifiers that already work in the installed version:

```toml
# ~/.kimi-code/config.toml

default_model = "kimi-k3"

[secondary_model]
model = "glm-5.2"
default_effort = "high"

[subagent]
timeout_ms = 7200000

[background]
max_running_tasks = 6
```

Enable secondary-model routing in a supported mode:

```bash
export KIMI_CODE_EXPERIMENTAL_SECONDARY_MODEL=1
kimi web
```

For experimental print mode, use the experimental flag required by the installed release. The current Kimi documentation states that custom-agent `model_preference` and explicit primary/secondary selection are not supported by the ordinary TUI.

### 3.2 Native GLM custom agents

Create profiles under:

```text
~/.kimi-code/agents/
```

Recommended profiles:

- `glm-explorer`: read-only codebase mapping and dependency analysis.
- `glm-implementer`: difficult, bounded implementation.
- `glm-debugger`: reproduction and root-cause analysis.
- `glm-reviewer`: read-only independent review.
- `glm-tester`: test design and verification.

Each profile should include:

```yaml
model_preference: secondary
```

Give write tools only to agents that genuinely need them. Read-only reviewers should not receive write/edit tools.

---

## 4. External delegation boundary

Expose one fixed executable to K3:

```text
~/.local/bin/delegate
```

Interface:

```text
delegate --agent <name> --workspace <path> \
         [--task <text> | --task-file <path>] \
         [--timeout <seconds>]
```

Exactly one task source is required.

### Preferred task-delivery order

1. `stdin` to the child CLI.
2. A private temporary task file, only where the child requires a filename.
3. A command argument only when unavoidable.

Command-line arguments are visible through process inspection on many systems. Therefore, `argument` delivery must be supported for compatibility but should not be the default for sensitive or proprietary prompts.

---

## 5. Wrapper specification

Create these files:

```text
delegate.py
agents.example.json
README.md
tests/test_delegate.py
```

Use Python 3 and only the standard library.

### 5.1 Trusted configuration

The wrapper loads `agents.json` from a fixed default location. An administrator may override the path only through a specifically documented trusted mechanism. Task input must never select an arbitrary configuration file.

Configuration defines:

- allowed workspace roots;
- maximum task size;
- maximum timeout;
- bounded stdout/stderr tail sizes;
- maximum grace period before force-kill;
- named agent definitions;
- absolute executable path and fixed arguments;
- prompt-delivery mode;
- default timeout;
- inherited environment-variable allowlist;
- non-secret fixed environment values.

Never accept an executable, provider, model name, command fragment, environment-variable name, or arbitrary fixed argument from delegated task text.

### 5.2 Proposed configuration schema

```json
{
  "allowed_workspace_roots": [
    "/home/boris/projects"
  ],
  "max_task_bytes": 262144,
  "max_timeout_seconds": 7200,
  "default_kill_grace_seconds": 5,
  "max_stdout_bytes": 65536,
  "max_stderr_bytes": 65536,
  "agents": {
    "deepseek-coder": {
      "command": [
        "/absolute/path/to/deepseek-cli",
        "--non-interactive"
      ],
      "prompt_delivery": "stdin",
      "default_timeout": 1800,
      "environment_allowlist": [
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "DEEPSEEK_API_KEY"
      ],
      "environment": {}
    },
    "grok-reviewer": {
      "command": [
        "/absolute/path/to/grok-cli",
        "--non-interactive",
        "--read-only"
      ],
      "prompt_delivery": "stdin",
      "default_timeout": 1200,
      "environment_allowlist": [
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "XAI_API_KEY"
      ],
      "environment": {}
    }
  }
}
```

The actual fixed arguments must match each installed CLI's verified non-interactive invocation. Do not invent flags. If a CLI cannot run reliably without interactive prompts, do not configure it as a worker until that is solved at the CLI level.

### 5.3 Input validation

Before launching a child:

1. Parse arguments with `argparse`.
2. Require an exact configured agent name.
3. Require exactly one of `--task` and `--task-file`.
4. Read task files as UTF-8; reject decoding errors.
5. Require a nonempty task within `max_task_bytes`, measured after UTF-8 encoding.
6. Parse timeout as a finite positive number within the configured maximum.
7. Resolve the workspace with `Path.resolve(strict=True)`.
8. Require an existing directory.
9. Resolve all allowed roots.
10. Require the workspace to equal or descend from an allowed root.
11. Reject symlink escapes after resolution.
12. Validate configuration types and required fields before use.
13. Require an absolute executable path and verify it refers to an executable regular file.
14. Reject NUL bytes in task, path, and configured arguments.

Do not include task text or environment values in validation errors.

### 5.4 Environment construction

Do not pass unrestricted `os.environ.copy()`.

Construct the child environment from:

1. Environment variables named in the agent's allowlist and present in the wrapper environment.
2. Non-secret fixed values from the trusted configuration.

Rules:

- Never print environment values.
- Never serialize the constructed environment to logs.
- Never put API keys directly in `agents.json`.
- Fail clearly when a required credential is absent, without naming its value.
- Consider a separate `required_environment` list so missing credentials are detected before launch.

Recommended addition:

```json
"required_environment": ["DEEPSEEK_API_KEY"]
```

### 5.5 Process launch

Use:

- `subprocess.Popen`;
- argument arrays;
- `shell=False`;
- resolved workspace as `cwd`;
- separate stdout/stderr pipes;
- binary mode for exact logging;
- a new POSIX session/process group using `start_new_session=True`;
- platform-specific process management isolated in small functions.

The wrapper must not:

- invoke a shell;
- parse or remove ANSI sequences;
- answer interactive prompts;
- retry failed workers;
- maintain sessions;
- interpret model output;
- schedule concurrent children internally.

### 5.6 Output capture without memory exhaustion

Do not call `communicate()` in a way that retains unlimited output in memory.

Use two reader threads:

```text
stdout pipe -> stdout.log + bounded stdout tail buffer
stderr pipe -> stderr.log + bounded stderr tail buffer
```

Requirements:

- Write raw bytes to complete log files.
- Maintain only the final configured number of bytes in memory.
- Decode bounded tails with UTF-8 and `errors="replace"` after completion.
- Report whether each stream was truncated.
- Ensure reader-thread exceptions become wrapper internal failures.
- Join reader threads after the process exits or is killed.

Tail retention is preferred because CLI summaries and tracebacks normally appear at the end. Full output remains available in the run directory.

### 5.7 Private run directory

Create one run directory per invocation using `tempfile.mkdtemp()` beneath a configured or system temporary root.

Immediately enforce restrictive permissions:

```text
0700 run directory
0600 log files
```

Files:

```text
stdout.log
stderr.log
```

Do not write:

- task text;
- command arrays;
- configuration;
- environment names or values;
- credentials;
- wrapper debug dumps.

Child stdout/stderr must be logged verbatim. The child may itself emit task excerpts, source code, credentials, or other sensitive information. State this explicitly in the README.

### 5.8 Timeout and interruption behavior

On POSIX:

1. Start the child in a new session/process group.
2. On timeout, send `SIGTERM` to the process group.
3. Wait for the configured grace period.
4. If any process remains, send `SIGKILL` to the process group.
5. Reap the child.
6. Finish draining and joining output readers.
7. Return `status="timeout"`.

On wrapper interruption:

1. Record the signal condition.
2. Terminate the full child process group using the same graceful-then-forceful sequence.
3. Emit one JSON result when feasible.
4. Exit with the documented interruption code.

Keep signal handlers minimal. Perform substantive cleanup in normal control flow rather than directly inside the handler.

### 5.9 JSON response

Print exactly one UTF-8 JSON object followed by one newline to stdout. Print nothing else.

```json
{
  "status": "completed|failed|timeout|invalid|internal_error|interrupted",
  "agent": "deepseek-coder",
  "child_exit_code": 0,
  "duration_seconds": 12.345,
  "stdout": "bounded final output",
  "stderr": "bounded final error output",
  "stdout_truncated": false,
  "stderr_truncated": false,
  "run_dir": "/absolute/private/run/path",
  "error": null
}
```

Semantics:

| Status | Meaning |
|---|---|
| `completed` | Child exited with code 0 |
| `failed` | Child started and exited nonzero |
| `timeout` | Timeout expired and process group was terminated |
| `invalid` | Input or trusted configuration was invalid; no child launched |
| `internal_error` | Wrapper failed independently of ordinary child failure |
| `interrupted` | Wrapper received a termination/interruption signal |

Use `null` for `child_exit_code` when no child exit code is available.

The wrapper must preserve the child's actual exit code in `child_exit_code`. Do not overload this field with wrapper status.

### 5.10 Wrapper process exit codes

Use wrapper process exit codes for wrapper-level control flow:

```text
0    valid JSON emitted for completed or ordinary child failure
64   invalid input or configuration
70   internal wrapper failure
124  timeout
130  interrupted by SIGINT
143  terminated by SIGTERM, where feasible
```

An ordinary child failure produces `status="failed"`, preserves the child's nonzero code in JSON, and exits the wrapper with code `0`. This prevents arbitrary child codes from colliding with wrapper-control codes and ensures callers treat the JSON as authoritative.

K3 must parse JSON even when the wrapper exits nonzero, because timeout and invalid-input results are still deliberately structured.

### 5.11 Secret handling

Guarantee only what the wrapper controls:

- The wrapper never logs task text itself.
- The wrapper never logs commands, environment values, or configuration.
- Wrapper diagnostics never contain secrets or task text.
- Child output is captured verbatim and may expose content emitted by the child.

Do not promise that logs can never contain task text. A child CLI may echo its prompt. Making the stronger promise would require output interpretation or redaction, which conflicts with the design and is unreliable.

### 5.12 Log lifecycle

A temporary run directory can accumulate sensitive logs indefinitely unless lifecycle is defined.

For version 1:

- Return the run directory path.
- Document that logs persist until removed.
- Provide no cleanup daemon or scheduler.
- Recommend that K3 or the operator delete a run directory after successful inspection.
- Add a future, separate `delegate-clean` utility only if operational use justifies it.

Do not silently delete failed-run logs before K3 can inspect them.

---

## 6. K3 orchestration policy

Place the following policy in a Kimi Skill or project instructions.

```markdown
# Delegation Policy

You are the lead Kimi K3 orchestrator and final authority.

## Native GLM delegation

Use native secondary-model agents for work requiring substantial repository
context, judgment, iterative tool use, difficult debugging, architectural
analysis, or high-confidence review.

## External CLI delegation

Use the fixed `delegate` executable for bounded tasks suitable for a stateless
external worker. Prefer task files or stdin-compatible worker profiles; avoid
placing sensitive task text in command-line arguments.

Permitted external-worker use cases include:

- isolated implementation with explicit file scope and acceptance tests;
- test generation;
- repetitive but reviewable refactoring;
- independent code review;
- alternative root-cause analysis;
- research whose claims can be independently verified.

Do not delegate:

- final acceptance;
- release authorization;
- credential handling;
- destructive operations;
- ambiguous repository-wide rewrites;
- decisions whose context was not included in the task.

## Invocation

1. Write a self-contained task containing:
   - objective;
   - workspace;
   - allowed files or read-only status;
   - prohibited actions;
   - relevant architecture and constraints;
   - required tests;
   - expected final report format.
2. Invoke `delegate` with a configured agent and resolved workspace.
3. Parse exactly one JSON object from stdout.
4. Treat invalid JSON as wrapper failure.
5. Inspect `status`, `child_exit_code`, truncation flags, and `run_dir`.
6. Read full logs only when bounded output is insufficient.
7. Treat all worker assertions as untrusted until verified.

## Parallel work

Read-only workers may share a workspace.

Never launch concurrent writing workers against overlapping files in the same
working tree. For parallel writers, create separate Git worktrees or assign
provably non-overlapping file scopes.

## Integration

After workers complete:

1. inspect every changed file and diff;
2. reconcile contradictory worker reports;
3. reject out-of-scope edits;
4. run integrated formatting, linting, type checks, and tests;
5. use an independent reviewer for material changes;
6. repair defects directly or delegate a narrowly scoped correction;
7. retain final acceptance in the primary K3 context.
```

---

## 7. Worker task contract

Every delegated task should be self-contained. Use this structure:

```markdown
# Role
You are a bounded external coding worker.

# Objective
<single concrete outcome>

# Workspace
<absolute path supplied separately to the wrapper>

# Scope
Allowed files:
- <paths or patterns>

Read-only files:
- <paths or patterns>

Do not modify:
- <paths or categories>

# Constraints
- Follow existing repository conventions.
- Do not broaden scope.
- Do not access credentials.
- Do not start interactive programs.
- Do not commit, push, release, or deploy unless explicitly authorized.

# Verification
Run:
- <specific commands>

# Completion report
Return:
1. outcome;
2. files changed;
3. commands run;
4. test results;
5. assumptions;
6. unresolved risks;
7. recommended next action.
```

For review workers, make the workspace read-only at the policy level where the CLI supports it and omit write-capable CLI flags.

---

## 8. Git and concurrency strategy

### Default: sequential writers

Use one writing worker at a time in the main workspace. This is the safest initial operating mode.

### Parallel writers: isolated worktrees

When parallelism is worth the complexity:

```text
repository/
├── main working tree
└── .delegate-worktrees/
    ├── worker-auth/
    ├── worker-tests/
    └── worker-api/
```

K3 should:

1. create one branch and worktree per writing worker;
2. delegate non-overlapping tasks;
3. inspect each diff;
4. cherry-pick or merge approved work;
5. run integrated tests in the main tree;
6. remove worktrees after completion.

The wrapper must not create branches, worktrees, commits, or merges. Those are orchestration responsibilities.

---

## 9. Tests

Use `unittest` and temporary directories. Child fixtures should be tiny Python scripts invoked with the current Python interpreter so tests require no third-party CLIs.

Required test coverage:

### Validation

- valid configured agent;
- unknown agent;
- missing task;
- both task sources supplied;
- empty task;
- oversized task;
- malformed timeout;
- timeout below/above bounds;
- nonexistent workspace;
- workspace is a file;
- workspace outside allowed roots;
- symlink escape outside allowed roots;
- malformed JSON configuration;
- invalid configuration types;
- relative executable rejected;
- missing executable;
- non-executable command;
- missing required environment variable.

### Execution

- successful stdin delivery;
- successful argument delivery;
- task from command argument;
- task from task file;
- task and path containing spaces;
- child working directory is correct;
- fixed arguments remain distinct array elements;
- `shell=False` behavior is preserved;
- child exits zero;
- child exits nonzero;
- child exit code appears unchanged in JSON.

### Output

- valid JSON for every handled outcome;
- exactly one JSON object on stdout;
- no wrapper diagnostics outside JSON;
- independent stdout/stderr capture;
- complete log files;
- bounded in-memory tails;
- truncation flags;
- UTF-8 replacement for invalid child bytes;
- large output does not deadlock or exhaust memory.

### Environment

- only allowlisted variables are inherited;
- fixed non-secret variables are added;
- disallowed variables are absent;
- environment values never appear in wrapper diagnostics.

### Lifecycle

- timeout status and wrapper exit code;
- child process terminated on timeout;
- descendant process terminated on timeout;
- graceful termination followed by force-kill;
- interruption cleanup where practical;
- private run-directory permissions;
- private log-file permissions.

For process-tree tests, spawn a child that creates a grandchild, record the grandchild PID in a test file, trigger timeout, and verify both are gone. Account for short process-reaping delays to avoid flaky assertions.

---

## 10. Implementation sequence

### Phase 1: Wrapper core

1. Define configuration dataclasses or typed dictionaries.
2. Parse and validate trusted configuration.
3. Parse and validate invocation input.
4. Resolve workspace containment.
5. Build minimal environment.
6. Create private run directory and logs.
7. Launch one process group.
8. Stream output with two reader threads and bounded tails.
9. Implement timeout and process-group termination.
10. Emit one JSON result.
11. Add wrapper exit-code mapping.

### Phase 2: Tests

1. Build Python fixture workers.
2. Cover validation and output contracts.
3. Add timeout and descendant-kill tests.
4. Add environment-isolation tests.
5. Add permission checks.
6. Run tests under the minimum supported Python version and current Python.

### Phase 3: CLI profiles

1. Verify exact non-interactive command for each installed CLI.
2. Create one read-only profile first.
3. Create one bounded coding profile second.
4. Confirm credentials are inherited only through the allowlist.
5. Confirm each CLI exits rather than waiting for input.
6. Confirm timeout kills its complete process tree.

### Phase 4: Kimi integration

1. Install `delegate` at a fixed absolute path.
2. Add a Kimi Skill containing the delegation policy.
3. Run Kimi in a mode that supports native secondary routing.
4. Test one GLM native subagent.
5. Test one external read-only worker.
6. Test one external writing worker in a disposable repository.
7. Test full K3 integration and verification.

### Phase 5: Controlled parallelism

1. Begin with sequential writers.
2. Add read-only parallel workers.
3. Add Git worktrees for parallel writing only after sequential operation is reliable.
4. Cap concurrency according to API limits, CPU/memory, and provider rate limits.

---

## 11. Acceptance criteria

The system is ready for regular use only when all of the following are true:

- K3 remains the final decision-maker.
- Native GLM subagents route correctly in the selected Kimi mode.
- `delegate` accepts only configured workers.
- Workspaces cannot escape configured roots through symlinks.
- No shell command construction occurs.
- Child environments contain only permitted variables.
- Tasks default to stdin delivery where supported.
- Large output is streamed to disk and bounded in memory.
- Timeout terminates the entire descendant process tree.
- Every handled outcome produces exactly one valid JSON object.
- Child failures remain distinguishable from wrapper failures.
- Full logs are private and discoverable through `run_dir`.
- Concurrent writers cannot touch overlapping files in the same worktree.
- K3 independently inspects changes and runs integrated verification.
- No external worker can authorize release, deployment, or final acceptance.

---

## 12. Rejected alternatives

### Secondary model routed through the subprocess wrapper

Rejected because Kimi expects a model-provider interaction protocol, not a one-shot CLI result. Implementing the adapter correctly would introduce HTTP, streaming, session/tool-call state, protocol translation, and substantially more failure modes.

### MCP in version 1

Rejected as unnecessary. Kimi can invoke the wrapper through its Bash tool. MCP may later improve typed arguments, discoverability, and permission UX, but it should be a thin adapter over the unchanged wrapper.

### ACP/session broker

Rejected because the goal is stateless bounded delegation, not remote interactive session management.

### Internal concurrency in `delegate`

Rejected because concurrency and write-conflict resolution belong to K3 and Git worktrees, not the process launcher.

### Automatic retries

Rejected because retries can duplicate edits, conceal deterministic failures, and increase cost. K3 should decide whether a retry is justified after inspecting the structured result.

### Automatic log redaction

Rejected because reliable redaction requires interpreting arbitrary child output and can create false confidence. Protect logs with permissions and lifecycle policy instead.

---

## 13. Recommended initial agent set

Start small:

```text
glm-explorer       native, read-only, secondary

glm-implementer    native, write-capable, secondary

glm-reviewer       native, read-only, secondary

deepseek-coder     external, bounded implementation

deepseek-tester    external, tests and repetitive verification

grok-reviewer      external, independent read-only critique
```

Do not create a dozen nearly identical worker profiles initially. Model diversity is useful; role-name proliferation is mostly decorative bureaucracy.

---

## 14. Final operating model

For a material engineering task:

1. K3 inspects the repository and creates the plan.
2. K3 uses GLM native agents for high-context exploration and difficult judgment.
3. K3 sends narrow, self-contained work to external DeepSeek/Grok workers through `delegate`.
4. Read-only workers may run concurrently.
5. Writing workers run sequentially or in isolated Git worktrees.
6. K3 parses structured wrapper results and reads full logs only as needed.
7. K3 distrusts worker summaries, inspects diffs, and runs integrated checks.
8. GLM or an external reviewer independently reviews material changes.
9. K3 resolves findings and retains final acceptance.

This is the preferred design: **K3 primary, GLM-5.2 native secondary, and a lean subprocess wrapper for heterogeneous external workers.**

---

## 15. Primary references

- Kimi Code CLI, Agents and Sub-Agents: https://www.kimi.com/code/docs/en/kimi-code-cli/customization/agents.html
- Kimi Code CLI, Built-in Tools: https://www.kimi.com/code/docs/en/kimi-code-cli/reference/tools.html
- Kimi Code CLI, Environment Variables: https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/env-vars.html
- Kimi Code CLI, Configuration Files: https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files.html
- Kimi Code CLI, Agent Skills: https://www.kimi.com/code/docs/en/kimi-code-cli/customization/skills.html
- Kimi Code CLI, MCP: https://www.kimi.com/code/docs/en/kimi-code-cli/customization/mcp.html
- Python documentation, `subprocess`: https://docs.python.org/3/library/subprocess.html
