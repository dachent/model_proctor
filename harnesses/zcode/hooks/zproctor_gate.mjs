#!/usr/bin/env node
/**
 * ZCode PreToolUse gate on the `Agent` tool - deterministic dispatch control.
 *
 * model_proctor's runner owns dispatch: it computes the lane from observable
 * features, caps the number of dispatches, and only permits a lateral switch
 * when a recorded failure fingerprint justifies it. ZCode owns subagent
 * dispatch itself, so the equivalent control has to sit in front of the `Agent`
 * tool call. This is that control.
 *
 * Enforced here, not advised:
 *   - no dispatch at all until `zproctor lane` has run for the active task
 *   - lane "self" authorizes no worker dispatch
 *   - only the lane's assigned worker may be dispatched
 *   - escalating to the next tier requires a recorded stagnation fingerprint
 *   - dispatches are capped at the lane's max_dispatches
 *
 * Ungated agents (general-purpose, Explore, anything not a proctor worker) pass
 * through untouched. Silence means allow; every failure path is silent so a
 * broken gate can never wedge a session.
 */
import { appendFileSync, mkdirSync, readFileSync, realpathSync } from "node:fs";

const STATE = process.env.ZPROCTOR_STATE_ROOT || "C:/Dev/scratch/zcode-proctor";
const DEADLINE_MS = Number(process.env.ZPROCTOR_GATE_DEADLINE_MS || 1500);

// No policy lives here. The ladder, the dispatch budget, the abort cap and the
// escalation threshold all arrive in lane.json, written by zproctor.py from the
// shared core. This shim performs comparisons only. Two implementations of one
// rule is how the control plane and its own gate drifted into a deadlock once.

let settled = false;

/**
 * Record that this shim allowed a call it could not adjudicate. Acceptance reads
 * this and refuses, so fail-open on the fast path does not become fail-open at
 * the irreversible boundary. Best effort: never let bookkeeping change the answer.
 */
function failOpen(why) {
  try {
    // The state root may not exist yet on the first gap of a session. Without
    // this the record is silently lost - and a silently lost fail-open is
    // exactly the hole this is meant to close.
    mkdirSync(STATE, { recursive: true });
    appendFileSync(`${STATE}/gate-failed-open.log`,
      `${Date.now()}	${why}
`, "utf8");
  } catch { /* bookkeeping must not affect the outcome */ }
}

function trace(note) {
  if (process.env.ZPROCTOR_TRACE === "0") return;
  try {
    appendFileSync(`${STATE}/gate-trace.log`,
      `${new Date().toISOString()}\t${note}\n`, "utf8");
  } catch { /* tracing must never affect the outcome */ }
}

function finish(reason, agent) {
  if (settled) return;
  settled = true;
  trace(reason ? `DENY\t${agent ?? "?"}\t${reason.slice(0, 70)}` : `ALLOW\t${agent ?? "?"}`);
  if (!reason) process.exit(0);
  const out = {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
  };
  try {
    process.stdout.write(JSON.stringify(out), () => process.exit(0));
    setTimeout(() => process.exit(0), 1000).unref?.();
  } catch {
    process.exit(0);
  }
}

setTimeout(() => { failOpen("deadline"); finish(null); }, DEADLINE_MS).unref?.();

function readJson(p) {
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

/** Comparable workspace key: forward slashes, no trailing slash, lowercased. */
function normWs(p) {
  return String(p || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/**
 * Active task FOR THIS WORKSPACE. Keyed per workspace so concurrent tasks in
 * different trees cannot collide. Both the as-given and resolved paths are
 * compared because Windows 8.3 short names survive in some callers.
 */
function activeFor(cwd) {
  const data = readJson(`${STATE}/active.json`);
  if (!data?.entries) return null;
  const keys = new Set([normWs(cwd)]);
  // Canonicalize away Windows 8.3 short names ("BORISV~1"). Python's resolve()
  // expands them and Node's path.resolve() does not, so without this the two
  // sides describe the same directory with different strings and never match.
  try {
    keys.add(normWs(realpathSync.native(cwd)));
  } catch { /* path may not exist; the raw form is still worth trying */ }
  const hits = data.entries.filter((e) =>
    keys.has(normWs(e.workspace)) ||
    keys.has(normWs(e.workspace_resolved)) ||
    keys.has(normWs(e.workspace_arg)));
  if (hits.length === 0) return null;
  return hits[hits.length - 1]; // most recently registered wins
}

/** Read the journal once: trailing identical-fingerprint run, and abort state. */
function journalState(taskDir) {
  const out = { run: 0, last: null, aborted: false };
  let fps = [];
  try {
    for (const line of readFileSync(`${taskDir}/events.jsonl`, "utf8").split("\n")) {
      if (!line.trim()) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; }
      if (ev.type === "TASK_ABORTED") out.aborted = true;
      if (ev.type === "VERIFY_PASSED") { fps = []; out.aborted = false; }
      if (ev.type === "VERIFY_FAILED" && ev.payload?.fingerprint) {
        fps.push(ev.payload.fingerprint);
      }
    }
  } catch {
    return out;
  }
  if (fps.length) {
    out.last = fps[fps.length - 1];
    for (let i = fps.length - 1; i >= 0 && fps[i] === out.last; i -= 1) out.run += 1;
  }
  return out;
}

function dispatchCount(taskDir) {
  try {
    return readFileSync(`${taskDir}/dispatches.log`, "utf8")
      .split("\n").filter((l) => l.trim()).length;
  } catch {
    return 0;
  }
}

function evaluate(raw) {
  let p;
  try { p = JSON.parse(raw); } catch { failOpen("unparseable payload"); return finish(null); }

  if (String(p.tool_name ?? p.toolName ?? "") !== "Agent") return finish(null);

  const input = p.tool_input ?? p.toolInput ?? {};
  const agent = String(input.subagent_type ?? "");
  if (!agent) return finish(null, "ungated");

  const cwd = p.cwd ?? p.workspace ?? "";
  const cur = activeFor(cwd);
  if (!cur?.task_dir) {
    return finish(
      `Dispatch refused: no lane has been selected for this workspace ` +
      `(${cwd || "unknown"}). The proctor assigns the lane from observable task ` +
      `features, not from a guess at difficulty. Run: ` +
      `python C:/Dev/bin/zproctor.py lane --task <id> --workspace . ` +
      `[--bounded --known-location --objective-acceptance | --marathon]`, agent);
  }

  const lane = readJson(`${cur.task_dir}/lane.json`);
  // Agents this proctor owns come from the roster in the lane record; anything
  // else (general-purpose, Explore) is none of our business.
  const owned = new Set(Object.values(lane?.roster_lanes ?? {}).filter((a) => a && a !== "self"));
  if (lane && !owned.has(agent)) return finish(null, `${agent} (ungated)`);
  if (!lane) {
    return finish(`Dispatch refused: lane record missing for task ${cur.task}. ` +
      `Re-run zproctor lane.`, agent);
  }

  if (!lane.agent || lane.agent === "self") {
    return finish(
      `Dispatch refused: task ${cur.task} is in lane "${lane.lane}", which this ` +
      `harness's roster binds to "self" - the orchestrator does this one itself ` +
      `and no worker dispatch is authorized. A dispatch pays a full context ` +
      `rebuild with no cache hit.`, agent);
  }

  // Hard cap: past max_stagnant identical failures the task is terminally
  // aborted. No dispatch is legal until state changes - not even a tier switch.
  const js = journalState(cur.task_dir);
  const maxStagnant = lane.max_stagnant ?? cur.max_stagnant;
  if (js.aborted || js.run >= maxStagnant) {
    return finish(
      `Dispatch refused: task ${cur.task} is terminally aborted - ` +
      `${js.run} consecutive identical failures against a cap of ${maxStagnant}. ` +
      `Repeating the same failure with a different model is not a repair. ` +
      `Change the approach and open a new task.`, agent);
  }

  const used = dispatchCount(cur.task_dir);
  if (used >= lane.max_dispatches) {
    return finish(
      `Dispatch refused: budget exhausted for task ${cur.task} ` +
      `(${used}/${lane.max_dispatches}). A refusal is final until state changes ` +
      `legally - re-verify or open a new task.`, agent);
  }

  if (agent !== lane.agent) {
    const ladder = (lane.ladder ?? []).map((role) => lane.roster_lanes?.[role]);
    const from = ladder.indexOf(lane.agent);
    const to = ladder.indexOf(agent);
    const isNextTier = from >= 0 && to === from + 1;
    if (!isNextTier) {
      return finish(
        `Dispatch refused: lane for task ${cur.task} is "${lane.lane}" ` +
        `(${lane.agent}); ${agent} is not the next tier.`, agent);
    }
    if (js.run < lane.escalation_threshold) {
      return finish(
        `Dispatch refused: escalation to ${agent} requires recorded stagnation - ` +
        `three identical normalized failure fingerprints. The journal for task ` +
        `${cur.task} does not show it. Run the verifier and let the evidence ` +
        `decide: python C:/Dev/bin/zproctor.py verify --task ${cur.task} ` +
        `--workspace .`, agent);
    }
  }

  try {
    appendFileSync(`${cur.task_dir}/dispatches.log`,
      `${new Date().toISOString()}\t${agent}\t${cur.task}\n`, "utf8");
  } catch { /* accounting failure must not block a legal dispatch */ }
  finish(null, agent);
}

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => { buf += c; });
process.stdin.on("end", () => evaluate(buf));
process.stdin.on("error", () => { failOpen("stdin error"); finish(null); });
