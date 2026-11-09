#!/usr/bin/env python3
"""seat_override.py — run one seat on a model the roster does not carry.

The skill's `[use <model> substantial]` argument asks for the substantial seat
to run a different model than the one the machine-local roster binds it to.
That binding lives in `agents.json`, which is ACL-hardened to read-only for the
user (a compromised worker must not rewrite the trusted command config), so
retargeting it in place is a decision-seat step — rotation runbook Phase 4 —
and never something a leader does for one task.

This tool takes the no-elevation path instead: it copies the live roster to a
scratch file, adds ONE additional seat for the chosen model (synthesized from
the hardened `model_dispatch_template`, i.e. delegate's own model-mode shape),
and emits an agent-map naming it for the lane. `runner.py dispatch
--agent-map <map>` then selects it, and `DELEGATE_CONFIG=<copy>` — inherited by
the delegate the runner spawns — makes the copy the config that dispatch
validates and launches from. The live roster is never written.

Why a new seat name instead of editing the existing one: the runner journal
records the seat it dispatched (`agent`), and the task record is evidence. An
override that reused `substantial-worker` would leave a record claiming the
live seat ran when it did not. The derived name carries the model slug, so the
journal says what actually ran.

The live roster and the live harness catalog are both read at invocation and
never cached (#23: a synced copy of either is what desynced on 2026-08-28).
Nothing here dispatches or spends tokens.

The synthesized seat also carries whatever credential its provider declares. The
delegate hands the child a sanitized environment (a Windows base set plus the
seat's `environment_allowlist`), so a provider that reads its key from
`api_key_env` cannot authenticate unless that variable is on the allowlist — the
2026-11-09 `config.invalid: ... api_key_env ... not set` failure. The variable's
NAME comes from the provider's own block in kimi's config, per model; a provider
holding a literal key gets nothing added. The value is never read, written, or
printed here — the caller exports it into the dispatch process's environment.

Usage:
  python seat_override.py                                  # the default: no override
  python seat_override.py --model hy4                      # substantial -> hy4
  python seat_override.py --model openrouter/tencent/hy4 --out-dir <dir>

Exit codes: 0 emitted (including "no override needed"), 2 refused.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import delegate  # noqa: E402
import catalog  # noqa: E402

# The only seat with an owner-directed override (2026-10-30). Adding another is
# a doc + table change, not a flag: each seat id belongs to a lane, and a
# mis-mapped override would silently move work to a different worker.
SEATS = {"substantial": ("glm", "substantial-worker")}
SEAT_ALIASES = {"substantial-worker": "substantial"}

# Documented shorthands (SKILL.md "Arguments"). A fully qualified id needs no
# entry here; an unknown shorthand is refused rather than guessed, because the
# provider is half of what an id means.
SHORTHANDS = {
    "glm-5p3": "fireworks/glm-5p3",
    "hy4": "openrouter/tencent/hy4",
}
DEFAULT_MODEL = "fireworks/glm-5p3"
# How this workstation resolves a provider credential into the dispatch's
# process environment. Names a sanctioned READ command only — never a value,
# and never a place to store one.
CREDENTIAL_HINTS = {"OPENROUTER_API_KEY": "kvget openrouter-api-key"}


def _emit(payload, code=0):
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    sys.stdout.flush()
    return code


def _refuse(error, **detail):
    return _emit({"ok": False, "error": error, **detail}, 2)


def _out_dir(explicit):
    if explicit:
        return Path(explicit).expanduser()
    # Mirrors runner.scratch_root() (#115): one structured root, env-overridable.
    root = os.environ.get("MODEL_PROCTOR_SCRATCH") or r"C:\Dev\scratch\model-proctor"
    return Path(root) / "seats"


def _slug(model_id):
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def _seat_model(agent):
    """The `-m` value a roster seat dispatches, or None for a non-model seat."""
    cmd = [str(t) for t in agent.get("command", [])]
    for i, tok in enumerate(cmd[:-1]):
        if tok == "-m":
            return cmd[i + 1]
    return None


def _priced(model_id):
    load_pricing = catalog._load_pricing_fn()
    path = catalog._pricing_path()
    if not load_pricing or not path:
        return None
    try:
        return load_pricing(str(path)).get(model_id)
    except OSError:
        return None


def _block_field(text, header_re, field):
    """First `field = "value"` inside the block opened by `header_re`."""
    m = re.search(header_re + r"\n(.*?)(?=\n\[|\Z)", text, re.S | re.M)
    if not m:
        return None
    f = re.search(r"^\s*" + field + r'\s*=\s*"([^"]*)"', m.group(1), re.M)
    return f.group(1) if f else None


def _provider_credential(model_id):
    """(env_var, note) for the credential the model's provider reads.

    Kimi's config declares, per provider, how it authenticates: `api_key_env =
    "VAR"` means the key is taken from the child process environment, a literal
    `api_key` means it is already inside the config the child is seeded with.
    Only the first kind needs passing through, and which var that is belongs to
    the provider's own declaration — never to a list kept here.

    Header scan, not a TOML parse: stdlib on 3.10 has none, the same constraint
    `delegate.live_harness_models` works under. An unreadable config returns a
    note; a provider with no api_key_env (or none declared) returns (None, None)
    and the seat is left exactly as the hardened template defines it.
    """
    path = delegate._harness_config_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"harness config unreadable: {path} ({e})"
    provider = _block_field(
        text, r'^\[models\."' + re.escape(model_id) + r'"\]', "provider")
    if not provider:
        return None, f"no provider declared for {model_id} in {path}"
    env_var = _block_field(
        text, r"^\[providers\." + re.escape(provider) + r"\]", "api_key_env")
    return env_var, None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="seat_override")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"model id or a documented shorthand; default "
                         f"{DEFAULT_MODEL} = no override")
    ap.add_argument("--seat", default="substantial",
                    help="seat to override (only 'substantial' is wired)")
    ap.add_argument("--out-dir", default=None,
                    help="where to write the derived config and agent map "
                         "(default: <scratch root>/seats, outside any workspace)")
    args = ap.parse_args(argv)

    seat = SEAT_ALIASES.get(args.seat, args.seat)
    if seat not in SEATS:
        return _refuse("seat_not_supported", seat=args.seat,
                       wired=sorted(SEATS),
                       detail="only the substantial seat carries an "
                              "owner-directed override")
    lane, seat_id = SEATS[seat]

    model = SHORTHANDS.get(args.model, args.model)
    if "/" not in model:
        return _refuse("model_shorthand_unknown", model=args.model,
                       shorthands=SHORTHANDS,
                       detail="pass a fully qualified id (provider/model) or a "
                              "documented shorthand; `catalog.py` lists what "
                              "the live harness config resolves")

    ids, note = delegate.live_harness_models()
    if note:
        return _refuse("catalog_unavailable", detail=note)
    if model not in ids:
        return _refuse("model_not_in_harness_config", model=model,
                       detail="not a [models.*] key in the live harness config, "
                              "so no dispatch could resolve it")

    try:
        source_cfg = delegate.load_config()
    except delegate.ConfigError as e:
        return _refuse("config_error", detail=str(e))
    source_path = delegate._resolve_config_path()
    agents = source_cfg.get("agents", {})
    if seat_id not in agents:
        return _refuse("seat_not_in_roster", seat=seat_id,
                       rostered=sorted(agents),
                       detail="the live roster does not carry this seat; "
                              "regenerate it per delegate/README.md")

    current = _seat_model(agents[seat_id])
    if current == model:
        return _emit({
            "ok": True,
            "override": False,
            "seat": seat_id,
            "lane": lane,
            "model": model,
            "source_config": str(source_path),
            "detail": "the live seat already runs this model — dispatch the "
                      "gated path with no seat flags",
        })

    try:
        entry = delegate.resolve_model_agent(source_cfg, model, write=False)
    except delegate.ConfigError as e:
        return _refuse("config_error", detail=str(e))

    # The ticket that failed: the template's allowlist carries no credential, and
    # the child gets a sanitized environment, so a provider that reads its key
    # from the environment cannot authenticate. The provider's own declaration
    # says which variable that is; a provider holding a literal key needs none.
    cred_env, cred_note = _provider_credential(model)
    if cred_env:
        allow = entry.setdefault("environment_allowlist", [])
        if cred_env not in allow:
            allow.append(cred_env)
        required = entry.setdefault("required_environment", [])
        if cred_env not in required:
            required.append(cred_env)

    alias = f"{seat_id}-override-{_slug(model)}"
    derived = json.loads(json.dumps(source_cfg))
    derived["agents"][alias] = entry

    out_dir = _out_dir(args.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _refuse("out_dir_unwritable", out_dir=str(out_dir), detail=str(e))
    config_path = out_dir / f"{alias}.json"
    map_path = out_dir / f"{alias}.map.json"
    try:
        config_path.write_text(json.dumps(derived, indent=2) + "\n",
                               encoding="utf-8", newline="\n")
        map_path.write_text(json.dumps({lane: alias}, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
    except OSError as e:
        return _refuse("out_dir_unwritable", out_dir=str(out_dir), detail=str(e))

    # One validator: the derived config must pass the same check the delegate
    # runs at dispatch, so a bad copy fails here instead of mid-task.
    try:
        delegate._validate_config(derived, config_path)
    except delegate.ConfigError as e:
        return _refuse("derived_config_invalid", path=str(config_path),
                       detail=str(e))

    price = _priced(model)
    credential = None
    if cred_env:
        credential = {
            "env": cred_env,
            "declared_by": "the model's provider block in kimi's config",
            "in_current_environment": bool(os.environ.get(cred_env)),
            "hint": CREDENTIAL_HINTS.get(
                cred_env,
                "resolve it through the sanctioned credential path and export "
                "it for the dispatch only"),
        }
    return _emit({
        "ok": True,
        "override": True,
        "seat": seat_id,
        "lane": lane,
        "model": model,
        "previous_model": current,
        "journal_agent": alias,
        "source_config": str(source_path),
        "delegate_config": str(config_path),
        "agent_map": str(map_path),
        "env": {"DELEGATE_CONFIG": str(config_path)},
        "credential": credential,
        "credential_note": cred_note,
        "dispatch_args": ["--agent-map", str(map_path)],
        "metering": ("priced" if price else
                     "unknown — no pricing row, so the record meters as "
                     "usage_unknown, never $0"),
    })


if __name__ == "__main__":
    sys.exit(main())
