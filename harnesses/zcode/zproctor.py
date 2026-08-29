#!/usr/bin/env python3
"""Minimal deterministic acceptance gate for ZCode sessions.

Mirrors the trust-boundary invariants of the dachent/model_proctor runner, in
the subset achievable without a dispatch control plane:

  * state, journal and receipts live OUTSIDE the agent-writable workspace
  * the verifier is executed by THIS tool, never reported by a model
  * receipts are bound to an exact tree identity and stale on any mutation
  * the event journal is append-only with a hash chain
  * accept refuses unless a fresh passing receipt matches the current tree

Not mirrored: worker dispatch, per-dispatch isolated homes, sealed verifier
payload restore-on-tamper, cost metering. Use the real runner for those.

Python 3.10+, standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# The shared decision core. Lane table, fingerprint normalization, stagnation
# thresholds, scope matching and the verification-affecting set live there so
# every harness decides the same way. See policy/HARNESS_CONTRACT.md.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
import decisions  # noqa: E402

STATE_ROOT = Path(os.environ.get("ZPROCTOR_STATE_ROOT", r"C:/Dev/scratch/zcode-proctor"))
IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".runner-state",
               ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build"}


def _run(argv, cwd, timeout=1800):
    try:
        p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, p.stdout[-65536:], p.stderr[-65536:]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def tree_id(ws):
    """Deterministic identity of the working tree, including dirty state."""
    rc, head, _ = _run(["git", "rev-parse", "HEAD"], ws, 30)
    if rc == 0:
        rc2, porc, _ = _run(["git", "status", "--porcelain=v1"], ws, 60)
        if rc2 == 0:
            h = hashlib.sha256(porc.encode("utf-8", "replace")).hexdigest()
            return "git:%s+%s" % (head.strip(), h[:16])
    acc = hashlib.sha256()
    for root, dirs, files in os.walk(ws):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
        for name in sorted(files):
            fp = Path(root) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            acc.update(str(fp.relative_to(ws)).replace("\\", "/").encode())
            acc.update(("%s:%s" % (st.st_size, st.st_mtime_ns)).encode())
    return "stat:%s" % acc.hexdigest()[:32]


def task_dir(task_id, ws):
    key = hashlib.sha256(str(ws.resolve()).lower().encode()).hexdigest()[:12]
    return STATE_ROOT / ("%s-%s" % (ws.name, key)) / task_id


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def walk_rel(ws):
    """Relative posix paths of every file in the tree, ignore-dirs excluded."""
    for root, dirs, files in os.walk(ws):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
        for name in sorted(files):
            fp = Path(root) / name
            try:
                rel = str(fp.relative_to(ws)).replace("\\", "/")
            except ValueError:
                continue
            yield rel, fp


def manifest_of(ws):
    """Per-file stat manifest. Cheap, and enough to diff added/modified/deleted."""
    out = {}
    for rel, fp in walk_rel(ws):
        try:
            st = fp.stat()
        except OSError:
            continue
        out[rel] = [st.st_size, st.st_mtime_ns]
    return out


def diff_manifest(old, new):
    o, n = set(old), set(new)
    added = sorted(n - o)
    deleted = sorted(o - n)
    modified = sorted(r for r in (o & n) if old[r] != new[r])
    return added, modified, deleted


def seal_paths(td, ws, paths):
    """Copy verifier inputs OUT of the agent-writable tree and hash them."""
    sealed_dir = td / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    sealed = {}
    for rel in paths:
        src = ws / rel
        if not src.is_file():
            continue
        digest = sha256_file(src)
        dest = sealed_dir / rel.replace("/", "__")
        try:
            dest.write_bytes(src.read_bytes())
        except OSError:
            continue
        sealed[rel] = {"sha256": digest, "copy": dest.name}
    return sealed


def restore_tampered(td, ws, sealed):
    """Restore any sealed file whose content changed. Returns what was restored."""
    restored = []
    sealed_dir = td / "sealed"
    for rel, meta in (sealed or {}).items():
        src = ws / rel
        cur = sha256_file(src) if src.is_file() else None
        if cur == meta.get("sha256"):
            continue
        backup = sealed_dir / meta.get("copy", "")
        if not backup.is_file():
            restored.append({"path": rel, "restored": False,
                             "reason": "sealed copy missing"})
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(backup.read_bytes())
            restored.append({"path": rel, "restored": True,
                             "was": "missing" if cur is None else cur[:12]})
        except OSError as exc:
            restored.append({"path": rel, "restored": False, "reason": str(exc)})
    return restored


def norm_ws(p):
    """Comparable workspace key. Lowercased, forward slashes, no trailing slash."""
    return str(p).replace("\\", "/").rstrip("/").lower()


def _active_entries():
    try:
        data = json.loads((STATE_ROOT / "active.json").read_text(encoding="utf-8"))
        return [e for e in data.get("entries", []) if isinstance(e, dict)]
    except Exception:
        return []


def active_for_ws(ws, ws_arg=None):
    """The registered active task for this workspace, if any."""
    try:
        resolved = str(ws.resolve())
    except OSError:
        resolved = str(ws)
    keys = {norm_ws(ws), norm_ws(resolved)}
    if ws_arg:
        keys.add(norm_ws(ws_arg))
    hits = [e for e in _active_entries()
            if {norm_ws(e.get("workspace", "")),
                norm_ws(e.get("workspace_resolved", "")),
                norm_ws(e.get("workspace_arg", ""))} & keys]
    return hits[-1] if hits else None


def register_active(task, ws, td, max_dispatches, max_stagnant, ws_arg=None):
    """Record this task as the active one FOR ITS WORKSPACE.

    Keyed per workspace, not globally: two tasks in different trees must not
    collide. Both the as-given and the resolved path are stored because Windows
    8.3 short names ("BORISV~1") survive in some callers and are expanded in
    others, and the dispatch gate has to match either form.
    """
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    af = STATE_ROOT / "active.json"
    try:
        data = json.loads(af.read_text(encoding="utf-8"))
        entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
    except Exception:
        entries = []
    try:
        resolved = str(ws.resolve())
    except OSError:
        resolved = str(ws)
    keys = {norm_ws(ws), norm_ws(resolved)}
    if ws_arg:
        keys.add(norm_ws(ws_arg))
    entries = [e for e in entries
               if not ({norm_ws(e.get("workspace", "")),
                        norm_ws(e.get("workspace_resolved", "")),
                        norm_ws(e.get("workspace_arg", ""))} & keys)]
    entries.append({"task": task, "workspace": str(ws),
                    "workspace_arg": str(ws_arg or ws),
                    "workspace_resolved": resolved, "task_dir": str(td),
                    "max_dispatches": max_dispatches,
                    "max_stagnant": max_stagnant,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    af.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")
    return af


SIGNED_FIELDS = ("schema", "seq", "type", "ts", "payload", "prev_hash")


def _event_hash(ev):
    """Recomputable identity of an event. Must stay in sync with append_event."""
    signed = {k: ev.get(k) for k in SIGNED_FIELDS}
    return hashlib.sha256(json.dumps(signed, sort_keys=True).encode()).hexdigest()


def append_event(td, etype, payload):
    jf = td / "events.jsonl"
    prev_hash, seq = "genesis", 0
    if jf.exists():
        for line in jf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn line: never advance state on it
            seq, prev_hash = ev.get("seq", seq), ev.get("event_hash", prev_hash)
    ev = {"schema": 1, "seq": seq + 1, "type": etype,
          "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "payload": payload,
          "prev_hash": prev_hash}
    ev["event_hash"] = _event_hash(ev)
    with jf.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return ev


def project(td):
    """Rebuild current state from the journal. The journal is authoritative."""
    st = {"task_id": td.name, "events": 0, "chain_ok": True, "receipt": None,
          "accepted": False, "aborted": False, "verify_attempts": 0, "fingerprints": [],
          "verifier": None, "init_tree": None}
    jf = td / "events.jsonl"
    if not jf.exists():
        return st
    prev = "genesis"
    for line in jf.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            st["chain_ok"] = False
            continue
        # Two independent checks. Linkage catches insertion, deletion and
        # reordering; recomputation catches in-place content edits, which
        # linkage alone cannot see because the stored hash travels with the
        # forged event.
        if ev.get("prev_hash") != prev:
            st["chain_ok"] = False
        if ev.get("event_hash") != _event_hash(ev):
            st["chain_ok"] = False
        prev = ev.get("event_hash", prev)
        st["events"] += 1
        etype, pl = ev.get("type"), ev.get("payload", {})
        if etype == "TASK_INITIALIZED":
            st["init_tree"] = pl.get("tree")
            st["verifier"] = pl.get("verifier")
            st["init_payload"] = pl
        elif etype in ("VERIFY_PASSED", "VERIFY_FAILED"):
            st["verify_attempts"] += 1
            st["receipt"] = {"passed": etype == "VERIFY_PASSED",
                             "tree": pl.get("tree"), "seq": ev.get("seq"),
                             "ts": ev.get("ts")}
            if etype == "VERIFY_PASSED":
                # A pass clears the stagnation run. Without this the projection
                # keeps counting failures across a green verify while the JS gate
                # (which does clear) counts from the pass - the control plane then
                # orders an escalation its own gate refuses. Deadlock, observed.
                st["fingerprints"] = []
                st["aborted"] = False
            elif etype == "VERIFY_FAILED" and pl.get("fingerprint"):
                st["fingerprints"].append(pl["fingerprint"])
        elif etype == "TASK_ACCEPTED":
            st["accepted"] = True
        elif etype == "TASK_ABORTED":
            st["aborted"] = True
    return st


def load_roster():
    """lane -> agent name, plus the ordered ladder. Names only, never models.

    Model and argv binding stays in the machine-local, ACL-hardened harness
    config; putting model ids in a tracked file would reverse that decision and
    create a third source of truth.
    """
    path = Path(__file__).with_name("roster.json")
    if not path.is_file():
        path = Path(__file__).with_name("roster.example.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("ladder", [l for l in decisions.LANES])
    return data


def emit(obj, code=0):
    print(json.dumps(obj, indent=2))
    sys.exit(code)


def main():
    ap = argparse.ArgumentParser(prog="zproctor")
    ap.add_argument("cmd", choices=["lane", "init", "verify", "accept", "status",
                                "events", "record"])
    ap.add_argument("--task", required=True)
    ap.add_argument("--workspace", default=".")
    # lane inputs: observable task features, not a judgement of difficulty
    ap.add_argument("--bounded", action="store_true",
                    help="scope is bounded to known files")
    ap.add_argument("--known-location", action="store_true",
                    help="the code to change has already been located")
    ap.add_argument("--objective-acceptance", action="store_true",
                    help="a command decides pass/fail")
    ap.add_argument("--marathon", action="store_true",
                    help="open-ended, no bounded signature")
    ap.add_argument("--multi-module", action="store_true",
                    help="the change spans multiple modules")
    ap.add_argument("--unfamiliar-repo", action="store_true",
                    help="the repository is unfamiliar to the worker")
    ap.add_argument("--max-dispatches", type=int, default=3)
    ap.add_argument("--max-stagnant", type=int, default=6,
                    help="consecutive identical failures before the task is "
                         "terminally aborted (hard cap, like the runner's)")
    ap.add_argument("--relane", action="store_true",
                    help="deliberately re-select a frozen lane; needs --reason")
    ap.add_argument("--reason", default=None,
                    help="why the lane is being overridden; recorded in the journal")
    ap.add_argument("--scope", nargs="*", default=None,
                    help="paths/globs the worker may change; empty = unrestricted")
    ap.add_argument("--seal", nargs="*", default=None,
                    help="extra verifier inputs to seal beyond those auto-detected")
    ap.add_argument("--session", default=None,
                    help="record: limit metering to one ZCode session id")
    ap.add_argument("--db", default=None,
                    help="record: ZCode session db (default ~/.zcode/cli/db/db.sqlite)")
    ap.add_argument("--pricing", default=None,
                    help="record: pricing table json (default alongside this script)")
    ap.add_argument("--verifier", nargs=argparse.REMAINDER,
                    help="argv array, e.g. --verifier python -m pytest -q")
    a = ap.parse_args()

    ws = Path(a.workspace).resolve()
    if not ws.is_dir():
        emit({"ok": False, "error": "workspace_not_a_directory", "workspace": str(ws)}, 2)
    # Trust boundary: state must live outside the agent-writable tree. If it does
    # not, appending to the journal mutates the tree and no receipt can ever be
    # fresh. Refusal is final - relocate the state root, never bypass.
    try:
        STATE_ROOT.resolve().relative_to(ws)
    except ValueError:
        pass
    else:
        emit({"ok": False, "error": "state_root_inside_workspace",
              "state_root": str(STATE_ROOT.resolve()), "workspace": str(ws),
              "hint": "set ZPROCTOR_STATE_ROOT to a path outside the workspace"}, 2)
    td = task_dir(a.task, ws)
    td.mkdir(parents=True, exist_ok=True)
    st = project(td)

    if a.cmd == "lane":
        # The lane is decided ONCE per task. Without this refusal the whole
        # mechanism is defeatable: re-run `lane` with friendlier features and the
        # gate authorizes whatever you wanted. Observed doing exactly that.
        existing = None
        if (td / "lane.json").is_file():
            try:
                existing = json.loads((td / "lane.json").read_text(encoding="utf-8"))
            except Exception:
                existing = None
        if existing and not a.relane:
            emit({"ok": False, "error": "lane_already_selected",
                  "task": a.task, "lane": existing.get("lane"),
                  "agent": existing.get("agent"),
                  "features": existing.get("features"),
                  "hint": "the lane is frozen for the life of the task; to override "
                          "deliberately use --relane --reason '<why>', which is "
                          "recorded in the journal"}, 2)

        # Freezing per task is not enough on its own: a new task id for the same
        # workspace would buy a fresh lane with friendlier features. Observed
        # exactly that. One live task per workspace until it is accepted or
        # aborted.
        if not a.relane:
            other = active_for_ws(ws)
            if other and other.get("task") != a.task:
                otd = Path(other["task_dir"])
                ost = project(otd) if otd.is_dir() else {}
                if not (ost.get("accepted") or ost.get("aborted")):
                    emit({"ok": False, "error": "workspace_has_live_task",
                          "live_task": other.get("task"),
                          "live_task_dir": other.get("task_dir"),
                          "hint": "accept or abort %s before laning another task in "
                                  "this workspace, or pass --relane --reason '<why>' "
                                  "to supersede it deliberately"
                                  % other.get("task")}, 2)

        # The lane comes from the shared table (core/decisions.py); this harness
        # only BINDS the resulting role to a concrete worker via its roster.
        # ZCode has no cheap worker, so its roster maps cheap -> "self", meaning
        # the orchestrator does the task and no dispatch is authorized.
        features = {"bounded": a.bounded, "known_location": a.known_location,
                    "objective_acceptance": a.objective_acceptance,
                    "marathon": a.marathon, "open_ended": a.marathon,
                    "multi_module": a.multi_module,
                    "unfamiliar_repo": a.unfamiliar_repo}
        lane, reasons = decisions.lane_for(features)
        roster = load_roster()
        agent = roster["lanes"].get(lane)
        if agent == "self":
            agent = None
        rec = {"task": a.task, "lane": lane, "agent": agent,
               "reasons": reasons, "ladder": roster["ladder"],
               # Everything the dispatch shim needs, written here so the shim
               # holds no policy of its own.
               "roster_lanes": roster["lanes"],
               "escalation_threshold": decisions.STAGNATION_THRESHOLD,
               "max_dispatches": a.max_dispatches,
               "features": features,
               "contract": decisions.CONTRACT_VERSION,
               "workspace": str(ws)}
        if existing:
            if not a.reason:
                emit({"ok": False, "error": "relane_reason_required",
                      "hint": "--relane requires --reason '<why>'"}, 2)
            rec["relaned_from"] = {"lane": existing.get("lane"),
                                   "agent": existing.get("agent"),
                                   "features": existing.get("features")}
            rec["reason"] = a.reason
        rec["max_stagnant"] = a.max_stagnant
        (td / "lane.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        register_active(a.task, ws, td, a.max_dispatches, a.max_stagnant,
                        ws_arg=a.workspace)
        (td / "dispatches.log").write_text("", encoding="utf-8")
        append_event(td, "LANE_SELECTED", rec)
        emit({"ok": True, "cmd": "lane", **rec,
              "note": "self = the orchestrator does it; no dispatch is authorized"})

    if a.cmd == "init":
        if not a.verifier:
            emit({"ok": False, "error": "verifier_required",
                  "hint": "--verifier python -m pytest -q"}, 2)
        # Seal the verifier surface: anything named in the verifier argv that
        # exists in the tree, plus every verification-affecting file present.
        to_seal = set(a.seal or [])
        for tok in a.verifier:
            cand = tok.replace("\\", "/").lstrip("./")
            if cand and (ws / cand).is_file():
                to_seal.add(cand)
        for rel, _fp in walk_rel(ws):
            if decisions.is_verification_affecting(rel):
                to_seal.add(rel)
        sealed = seal_paths(td, ws, sorted(to_seal))

        manifest = manifest_of(ws)
        (td / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        ev = append_event(td, "TASK_INITIALIZED",
                          {"tree": tree_id(ws), "workspace": str(ws),
                           "epoch_ms": int(time.time() * 1000),
                           "verifier": a.verifier,
                           "scope": a.scope or [],
                           "sealed": sealed,
                           "file_count": len(manifest)})
        emit({"ok": True, "cmd": "init", "task": a.task, "state_dir": str(td),
              "tree": ev["payload"]["tree"], "verifier": a.verifier,
              "scope": a.scope or [], "sealed": sorted(sealed),
              "files_tracked": len(manifest)})

    if a.cmd == "verify":
        verifier = a.verifier or st.get("verifier")
        if not verifier:
            emit({"ok": False, "error": "not_initialized", "hint": "run init first"}, 2)
        init_pl = st.get("init_payload") or {}
        sealed = init_pl.get("sealed") or {}
        scope = init_pl.get("scope") or []
        try:
            old_manifest = json.loads((td / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            old_manifest = {}

        # Restore any tampered verifier input BEFORE running it, and flag it.
        restored = restore_tampered(td, ws, sealed)
        if restored:
            append_event(td, "VERIFIER_RESTORED",
                         {"restored": restored, "tree": tree_id(ws)})

        added, modified, deleted = diff_manifest(old_manifest, manifest_of(ws))

        # A verification-affecting file appearing after init changes what the
        # verifier means. Refuse rather than run a different exam.
        new_affecting = [r for r in added if decisions.is_verification_affecting(r)]
        if new_affecting:
            append_event(td, "VERIFY_FAILED",
                         {"tree": tree_id(ws), "rc": None,
                          "reason": "verification_surface_changed",
                          "new_files": new_affecting,
                          "fingerprint": "verification_surface_changed"})
            emit({"ok": False, "cmd": "verify", "passed": False,
                  "error": "verification_surface_changed",
                  "new_files": new_affecting,
                  "hint": "these appeared after init and can change what the "
                          "verifier means; remove them or re-init deliberately"}, 1)

        violations = decisions.scope_violations(scope, added, modified, deleted)
        if violations:
            append_event(td, "VERIFY_FAILED",
                         {"tree": tree_id(ws), "rc": None,
                          "reason": "scope_violation", "paths": violations,
                          "scope": scope, "fingerprint": "scope_violation"})
            emit({"ok": False, "cmd": "verify", "passed": False,
                  "error": "scope_violation", "out_of_scope": violations,
                  "declared_scope": scope,
                  "hint": "the worker changed files outside the declared scope; "
                          "revert them or re-init with a wider scope"}, 1)

        before = tree_id(ws)
        rc, out, err = _run(list(verifier), ws)
        after = tree_id(ws)
        if before != after:
            append_event(td, "VERIFY_FAILED",
                         {"tree": after, "rc": rc,
                          "reason": "tree_mutated_during_verify",
                          "fingerprint": "tree_mutated"})
            emit({"ok": False, "cmd": "verify", "passed": False,
                  "error": "tree_mutated_during_verify",
                  "tree_before": before, "tree_after": after}, 1)
        fp = None if rc == 0 else decisions.fingerprint(rc, out, err)
        append_event(td, "VERIFY_PASSED" if rc == 0 else "VERIFY_FAILED",
                     {"tree": after, "rc": rc, "fingerprint": fp,
                      "stdout_tail": out[-4000:], "stderr_tail": err[-4000:]})
        st2 = project(td)
        lane_rec = {}
        try:
            lane_rec = json.loads((td / "lane.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        max_stagnant = int(lane_rec.get("max_stagnant")
                           or decisions.DEFAULT_MAX_STAGNANT)

        # Stagnation and the next action are computed by the shared core, so the
        # control plane and the dispatch gate cannot disagree about them.
        run = decisions.stagnation_run(st2["fingerprints"])
        nxt = decisions.next_action(rc, st2["fingerprints"], max_stagnant)
        stagnant = nxt in ("lateral_switch", "abort")
        aborted = nxt == "abort"
        if aborted:
            append_event(td, "TASK_ABORTED",
                         {"tree": after, "reason": "max_stagnant_exceeded",
                          "run": run, "max_stagnant": max_stagnant,
                          "fingerprint": fp})
        emit({"ok": rc == 0, "cmd": "verify", "passed": rc == 0, "rc": rc,
              "tree": after, "fingerprint": fp, "stagnation": stagnant, "next": nxt,
              "aborted": aborted, "stagnant_run": run,
              "max_stagnant": max_stagnant,
              "verifier_tampered": restored,
              "files_changed": {"added": added, "modified": modified,
                                "deleted": deleted},
              "stdout_tail": out[-2000:], "stderr_tail": err[-2000:]},
             0 if rc == 0 else 1)

    if a.cmd == "accept":
        r = st.get("receipt")
        cur = tree_id(ws)
        if not r:
            emit({"ok": False, "error": "no_receipt", "hint": "run verify"}, 1)
        if not r["passed"]:
            emit({"ok": False, "error": "last_verify_failed"}, 1)
        if r["tree"] != cur:
            emit({"ok": False, "error": "receipt_stale",
                  "receipt_tree": r["tree"], "current_tree": cur,
                  "hint": "tree changed after verify - re-verify"}, 1)
        if not st["chain_ok"]:
            emit({"ok": False, "error": "journal_chain_broken",
                  "hint": "event journal was modified out of band"}, 1)
        if st["accepted"]:
            emit({"ok": True, "cmd": "accept", "task": a.task, "tree": cur,
                  "note": "already_accepted_for_this_tree"})
        append_event(td, "TASK_ACCEPTED", {"tree": cur})
        emit({"ok": True, "cmd": "accept", "task": a.task, "tree": cur})


    if a.cmd == "record":
        # Metered accounting from ZCode's own session db. Token counts are
        # redacted in the JSONL log but recorded in full here, per model and per
        # agent - which is what makes the leader-vs-worker split measurable.
        import sqlite3
        db = Path(a.db) if a.db else Path(os.path.expanduser(
            "~/.zcode/cli/db/db.sqlite"))
        if not db.is_file():
            emit({"ok": False, "error": "session_db_not_found", "path": str(db)}, 2)
        pricing_path = Path(a.pricing) if a.pricing else (
            Path(__file__).with_name("zproctor_pricing.json"))
        try:
            pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
        except Exception:
            pricing = {}

        since = int((st.get("init_payload") or {}).get("epoch_ms") or 0)
        if not since:
            emit({"ok": False, "error": "no_init_epoch",
                  "hint": "re-init the task; older tasks predate metering"}, 2)

        q = ("select model_id, provider_id, agent, input_tokens, output_tokens, "
             "cache_read_input_tokens, completed_at from model_usage "
             "where completed_at >= ? and status = 'completed'")
        params = [since]
        if a.session:
            q += " and session_id = ?"
            params.append(a.session)
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
            rows = list(conn.execute(q, params))
        except Exception as exc:
            emit({"ok": False, "error": "session_db_unreadable", "detail": str(exc)}, 2)

        by_model, by_agent = {}, {}
        priced = unpriced = 0.0
        missing = set()
        for model_id, provider_id, agent, inp, outp, cached, _ts in rows:
            inp, outp, cached = int(inp or 0), int(outp or 0), int(cached or 0)
            fresh = max(0, inp - cached)
            rate = (pricing.get(provider_id or "", {}) or {}).get(model_id or "")
            cost = None
            if rate:
                cost = (fresh / 1e6) * rate["input"] +                        (cached / 1e6) * rate["cached"] +                        (outp / 1e6) * rate["output"]
                priced += cost
            else:
                missing.add("%s/%s" % (provider_id, model_id))
            key = "%s/%s" % (provider_id, model_id)
            m = by_model.setdefault(key, {"requests": 0, "input_fresh": 0,
                                          "cache_read": 0, "output": 0, "usd": 0.0})
            m["requests"] += 1
            m["input_fresh"] += fresh
            m["cache_read"] += cached
            m["output"] += outp
            m["usd"] += cost or 0.0
            ag = by_agent.setdefault(agent or "unknown",
                                     {"requests": 0, "usd": 0.0, "output": 0})
            ag["requests"] += 1
            ag["usd"] += cost or 0.0
            ag["output"] += outp

        for d in list(by_model.values()) + list(by_agent.values()):
            d["usd"] = round(d["usd"], 6)
        accepted = bool(st.get("accepted"))
        summary = {"task": a.task, "since_ms": since, "requests": len(rows),
                   "usd_total": round(priced, 6),
                   "accepted": accepted,
                   "usd_per_accepted_task": round(priced, 6) if accepted else None,
                   "by_model": by_model, "by_agent": by_agent,
                   "unpriced_models": sorted(missing),
                   "pricing_source": str(pricing_path)}
        append_event(td, "TASK_METERED", summary)
        with (td / "ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary) + "\n")
        emit({"ok": True, "cmd": "record", **summary})

    if a.cmd == "status":
        emit({"ok": True, "cmd": "status", "state_dir": str(td),
              "current_tree": tree_id(ws), "projection": st})

    if a.cmd == "events":
        jf = td / "events.jsonl"
        lines = jf.read_text(encoding="utf-8").splitlines() if jf.exists() else []
        emit({"ok": True, "cmd": "events", "chain_ok": st["chain_ok"],
              "events": [json.loads(x) for x in lines if x.strip()]})


if __name__ == "__main__":
    main()
