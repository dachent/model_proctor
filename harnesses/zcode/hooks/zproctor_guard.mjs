#!/usr/bin/env node
/**
 * ZCode PreToolUse guard - keeps models out of the acceptance-evidence store.
 *
 * Node rather than Python on purpose: ZCode ships a node runtime and its own
 * official plugin hooks (mimosa) invoke `node`, so there is no separate
 * interpreter to cold-start. A Python hook measured 4.7-6.4s cold on this
 * machine, which is what made ZCode crawl.
 *
 * Contract, read out of resources/glm/zcode.cjs (the CLI bundle the desktop
 * spawns - NOT app.asar, which carries a different internal permission type):
 *
 *   HookJSONOutput = { additionalContext?, additional_context?, continue?,
 *                      decision?: "approve" | "block",
 *                      hookSpecificOutput?, reason?, stopReason?,
 *                      suppressOutput? }
 *   PreToolUse variant of hookSpecificOutput =
 *     { hookEventName: "PreToolUse",
 *       permissionDecision?: "allow" | "ask" | "deny",
 *       permissionDecisionReason?: string, updatedInput?, additionalContext? }
 *
 * parseHookStdout THROWS if stdout is JSON that fails this schema, and the
 * normalizer THROWS if hookSpecificOutput.hookEventName does not match the
 * event being run. Either throw surfaces as hook.run.failed and the tool is
 * NOT blocked - so the shape has to be exact.
 *
 * Silence (or any non-JSON output) means allow. Every failure path here is
 * silent by design: a broken guard must never wedge a session.
 */

import { appendFileSync } from "node:fs";

const PROTECTED = (process.env.ZPROCTOR_STATE_ROOT || "C:/Dev/scratch/zcode-proctor");
const GATED = new Set(["Write", "Edit", "Bash", "MultiEdit", "NotebookEdit"]);
const DEADLINE_MS = Number(process.env.ZPROCTOR_GUARD_DEADLINE_MS || 1500);
const MAX_INPUT = 1_000_000;

let settled = false;

/**
 * Best-effort invocation trace. Without it an "allow" is silent and
 * indistinguishable from the hook never running at all. Never throws, never
 * blocks the decision.
 */
function trace(note) {
  if (process.env.ZPROCTOR_TRACE === "0") return;
  try {
    appendFileSync(
      `${PROTECTED}/guard-trace.log`,
      `${new Date().toISOString()}\t${note}\n`,
      "utf8"
    );
  } catch {
    /* tracing must never affect the outcome */
  }
}

function finish(output) {
  if (settled) return;
  settled = true;
  trace(output ? "DENY" : "ALLOW");
  if (!output) {
    process.exit(0);
  }
  // Wait for the pipe to drain before exiting. process.exit() immediately after
  // write() can truncate on a Windows pipe, and truncated JSON fails ZCode's
  // schema validation, which throws instead of denying.
  try {
    process.stdout.write(JSON.stringify(output), () => process.exit(0));
    setTimeout(() => process.exit(0), 1000).unref?.();
  } catch {
    process.exit(0);
  }
}

// Hard deadline: if stdin never closes, allow rather than hang.
const timer = setTimeout(() => finish(null), DEADLINE_MS);
timer.unref?.();

function norm(s) {
  return String(s).replace(/\\/g, "/").replace(/\/+/g, "/").toLowerCase();
}

/** Every string anywhere in the payload - tools nest their args differently. */
function* strings(node, depth = 0) {
  if (depth > 8 || node == null) return;
  if (typeof node === "string") {
    yield node;
  } else if (Array.isArray(node)) {
    for (const v of node) yield* strings(v, depth + 1);
  } else if (typeof node === "object") {
    for (const v of Object.values(node)) yield* strings(v, depth + 1);
  }
}

function evaluate(raw) {
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return finish(null); // unparseable: allow, never wedge
  }

  const tool = String(payload.tool_name ?? payload.toolName ?? "");
  if (!GATED.has(tool)) return finish(null);

  const target = norm(PROTECTED);
  const input = payload.tool_input ?? payload.toolInput ?? payload;
  let hit = false;
  for (const s of strings(input)) {
    if (norm(s).includes(target)) { hit = true; break; }
  }
  if (!hit) return finish(null);

  // Echo the event name back verbatim - a mismatch throws on ZCode's side.
  const event = String(payload.hook_event_name ?? payload.hookEventName ?? "PreToolUse");

  finish({
    hookSpecificOutput: {
      hookEventName: event,
      permissionDecision: "deny",
      permissionDecisionReason:
        `Denied: ${PROTECTED} is the deterministic acceptance-evidence store. ` +
        `Models do not author their own receipts, event journal, or verifier ` +
        `state. Change the tree, then re-run: ` +
        `python C:/Dev/bin/zproctor.py verify --task <id> --workspace <ws>`,
    },
  });
}

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  if (buf.length > MAX_INPUT) evaluate(buf);
});
process.stdin.on("end", () => evaluate(buf));
process.stdin.on("error", () => finish(null));
