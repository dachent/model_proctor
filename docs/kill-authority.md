# Kill Authority (TOOL-036, issue #107)

One component terminates a delegate-owned worker tree: the delegate that owns
its Job Object and process handles, via `killauthority.KillAuthority`.
External actors are read-only: their terminal action is a termination request
plus a report, never a kill.

## Attribution vocabulary (`kill_authority` key)

| Value | Meaning |
|---|---|
| `none` | Payload root exited on its own; no kill occurred. |
| `delegate:timeout` | Delegate authority terminated the tree at the dispatch deadline. |
| `delegate:interrupted` | Delegate authority terminated the tree on SIGINT/SIGTERM/KeyboardInterrupt. |
| `delegate:runner_requested` | Delegate authority terminated the tree after the runner wrote the terminate-request file at the wrapper deadline. Envelope status is `interrupted`, exit 130. |
| `unresolved_reported` | Runner requested; the delegate never confirmed within TERMINATION_REQUEST_WAIT_S. The runner did NOT kill anything; the wedged delegate and its tree remain the delegate's custody (operator action via `status`/orphan reporting). |
| `codex:cleanup_kill` | Codex adapter killed its own direct child after a 1s grace (no Job Object custody). |
| `eval:taskkill_tree_force` | Evals harness taskkill /T /F'd the kimi.exe tree it spawned itself. |

Attribution records what the authority DID. For `allow_breakaway` agents it
is not a guarantee that detached descendants died (the BREAKAWAY_OK design
comment in delegate.py).

## Kill-path inventory (kept in sync with killauthority.KILL_SITES)

| Site | File | Class | Mechanism | Record |
|---|---|---|---|---|
| KillAuthority.terminate | harnesses/kimi-code/delegate/killauthority.py | authority | taskkill /T → grace → /T /F → job close → /T /F | envelope kill_authority |
| KillAuthority.release | same | authority (custody) | job close reaps stragglers on normal exit | kill_authority "none" |
| Detached (WMI) dispatch kill | harnesses/kimi-code/delegate/delegate.py `_run_detached_dispatch` | authority | same KillAuthority sequence from the bootstrap pid (job=None) | envelope kill_authority |
| Runner wrapper deadline | harnesses/kimi-code/runner/runner.py | external_readonly | writes request file, bounded wait, journals outcome | journal dispatch_termination_requested + dispatch_finished.kill_authority |
| Kernel on delegate death | (OS) | authority (custody) | KILL_ON_JOB_CLOSE fires when the delegate's job handle closes for any reason | implicit; documented here |
| Codex cleanup | harnesses/codex/delegate/delegate.py:96 | own_child_cleanup | proc.kill() on own direct child after 1s grace | envelope kill_authority |
| Evals timeout | evals/run_eval.py:95-98 | own_child_cleanup | taskkill /T /F on own kimi.exe | result row kill_authority |
| Cascade backstop | harnesses/kimi-code/cascade/cascade.py:476-483 | frozen_artifact | stdlib subprocess.run timeout kill of direct child | frozen; envelope passthrough |
| plain_arm / pilot / zproctor spawns | evals/plain_arm.py:63-67, runner/pilot.py:69-73, zcode/zproctor.py:41-53 | stdlib_implicit | stdlib timeout kills of own direct children | not attributed (no envelope surface) |
| core/* | core/decisions.py, core/task_schema.py | policy_pure | none — pure functions | n/a |

## Residuals

- A delegate wedged INSIDE its kill sequence (worst case ≈ grace + 55s of
  swallowed taskkill timeouts) may outlast the runner's request wait; the
  journal then says unresolved even though the authority later executed. The
  record never claims the runner killed it. Not hermetically testable.
- The delegate consumes a terminate-request file on first poll with no
  staleness check; defense is the runner's per-dispatch uuid path +
  unlink-before-spawn.
- _journal_open masks dispatch_open behind any newer record sharing
  dispatch_id (heartbeats do this today) — follow-up issue, out of TOOL-036
  scope.
