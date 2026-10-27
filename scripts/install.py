"""Install the model-proctor harness durably (idempotent).

- Copies the delegate wrapper files to C:\\Tools\\model-proctor\\
- Copies runner/runner.py, runner/pilot.py and evals/pricing.yaml to
  C:\\Tools\\model-proctor\\ (MVP-001 control plane + the metering inputs the
  installed SKILL.md's `record --pricing` step needs; without pricing.yaml
  that step resolves a RELATIVE path and fails for any leader not sitting in
  the repo root, which is the whole point of installing to C:\\Tools)
- ACL-hardens the installed agents.json (Administrators: full, current user: read)
  so a compromised worker cannot rewrite the trusted command config.
  agents.json is machine-local and deliberately untracked; if absent it is
  skipped — regenerate it from agents.example.json per delegate/README.md.
- Installs the model-proctor skill to %USERPROFILE%\\.kimi-code\\skills\\
- Verifies afterwards that every path the installed skill instructs a leader to
  run actually exists.

Idempotent in OUTCOME, not a no-op: every file is rewritten each run. Re-running
as the same non-elevated user is supported — the hardened agents.json is skipped
when unchanged, and write access is restored before overwriting when it differs.

Because rewriting is the destructive step, it is guarded. Every run records what
it installed in C:\\Tools\\model-proctor\\install-manifest.json (target filename
-> sha256). At the START of the next run, a target that differs from BOTH the repo
source AND its manifest hash is content this installer never wrote and no commit
accounts for: a local patch. The run prints it, refuses to overwrite it, and exits
3. There is one way past — `--force` — because the failure this prevents is
silent, and silent is the whole problem: an installed runner.py had been extended
locally with commands that existed in no commit, and the next install would have
replaced the file without a word.
"""
import argparse
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIMI = ROOT / "harnesses" / "kimi-code"     # Kimi harness sources
TOOL_DIR = Path(r"C:\Tools\model-proctor")
ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "icacls.exe")

DELEGATE_FILES = ["delegate.py", "killauthority.py", "catalog.py",
                  "agents.example.json", "README.md",
                  "heartbeat.py", "stall_guard.py"]
# (base, subdir, filename) copied flat into TOOL_DIR. The base is explicit
# because runner/ lives under the harness while core/ and evals/ stay at the
# repo root. task_schema.py is the shared M0 schema (#83): the installed
# runner resolves it as a sibling (see runner._task_schema). catalog.py is
# the live-harness-catalog lister (#96). timeout_stack.py is the shared
# sizing authority (#108): the installed runner resolves it as a sibling
# (see runner._timeout_stack).
RUNNER_FILES = [(KIMI, "runner", "runner.py"), (KIMI, "runner", "pilot.py"),
                (ROOT, "core", "task_schema.py"),
                (ROOT, "core", "timeout_stack.py"),
                (ROOT, "evals", "pricing.yaml")]
SKILLS = ("model-proctor",)
# Everything the installed SKILL.md tells a leader to invoke or pass.
REQUIRED_AFTER_INSTALL = ["runner.py", "delegate.py", "killauthority.py", "catalog.py",
                          "task_schema.py", "timeout_stack.py", "pricing.yaml",
                          "heartbeat.py", "stall_guard.py"]
MANIFEST_NAME = "install-manifest.json"
# Distinct from 1 (incomplete install) so a caller can tell a refusal from a
# failure without parsing stdout.
REFUSED_LOCAL_PATCH = 3


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _targets():
    """(target filename, repo source) for every file this installer overwrites.

    agents.json is deliberately absent. It is machine-local and edited on the
    machine (a seat added, a model id retargeted), so differing from the repo's
    untracked copy is its normal state, not a lost patch. Its copy and ACL path
    are untouched and stay outside this guard.
    """
    targets = [(name, KIMI / "delegate" / name) for name in DELEGATE_FILES]
    targets += [(name, base / subdir / name) for base, subdir, name in RUNNER_FILES]
    return targets


def read_manifest(tool_dir):
    """The {filename: sha256} recorded by the last install; {} when absent.

    A missing or corrupt manifest is not fatal: an unrecorded hash simply cannot
    match, so the guard can only get more conservative, never less.
    """
    try:
        data = json.loads((Path(tool_dir) / MANIFEST_NAME)
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_manifest(tool_dir, names):
    """Record what this run installed. Sorted and undated, so two runs that
    install identical bytes produce an identical file — the re-install stays
    provably idempotent."""
    manifest = {name: _sha256(Path(tool_dir) / name) for name in names}
    (Path(tool_dir) / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def local_patches(tool_dir, targets, manifest):
    """Installed files that are neither the repo source nor the last install.

    Three states are legitimate and must NOT refuse:
      - target absent           -> fresh install; nothing to lose
      - target == repo source   -> content already in the repo; nothing to lose
      - target == manifest hash -> written by a previous install and the repo has
                                   since moved on, so this is a normal re-install
    Anything else is a difference this installer did not produce and no commit
    accounts for.
    """
    found = []
    for name, src in targets:
        dst = Path(tool_dir) / name
        if not dst.is_file():
            continue
        digest = _sha256(dst)
        if digest == _sha256(src) or digest == manifest.get(name):
            continue
        found.append((name, dst, src))
    return found


def _diff_preview(dst, src, limit=20):
    """Bounded unified diff of installed vs repo source, so the refusal shows
    what is at stake instead of only naming a file."""
    def read(path):
        return path.read_text(encoding="utf-8", errors="replace").splitlines()

    diff = list(difflib.unified_diff(read(src), read(dst),
                                     fromfile=f"repo/{src.name}",
                                     tofile=f"installed/{dst.name}",
                                     lineterm="", n=1))
    return diff[:limit], max(0, len(diff) - limit)


def _icacls(*args):
    return subprocess.run([ICACLS, *args], capture_output=True, text=True)


# Broad principals that must not retain access to the roster, given as SIDs.
# Names are localised -- a German Windows says "Authentifizierte Benutzer" --
# and SIDs are not. Same reason ICACLS itself is resolved by absolute path.
_BROAD_SIDS = {
    "*S-1-1-0": "Everyone",
    "*S-1-5-11": "Authenticated Users",
    "*S-1-5-32-545": "Users",
}


def harden_acl(path, principal):
    """Lock a file to Administrators:F + principal:R. Yields report lines.

    `/inheritance:r` removes INHERITED ACEs only. An installed roster that
    already carries EXPLICIT permissive entries -- which is the state of every
    previously-installed agents.json -- keeps them, and `/grant:r` replaces
    only the named principal's entry. Observed live: after the corrected
    #47 call, `NT AUTHORITY\\Authenticated Users:(M)` was still present and
    icacls reported "Successfully processed 1 files". The roster stayed
    writable by any authenticated account while the installer printed
    "ACL hardened". So remove the broad principals by name first.

    Then verify, because a hardening step that cannot confirm its own result
    is what produced both this bug and #47.
    """
    path = str(path)
    # /remove:g on an absent principal is a no-op, so this is safe to run
    # unconditionally and needs no pre-check.
    _icacls(path, "/remove:g", *_BROAD_SIDS)
    r = _icacls(path, "/inheritance:r",
                "/grant:r", "Administrators:F",
                "/grant:r", f"{principal}:R")
    if r.returncode != 0:
        yield f"WARNING: icacls failed: {r.stdout} {r.stderr}"
        return

    check = _icacls(path)
    acl = (check.stdout or "").lower()
    # Match the ACE form "PRINCIPAL:(PERMS)", not the bare name. icacls echoes
    # the file path first, and a path under C:\Users would otherwise match
    # "Users" and report a leak that is not there.
    leaked = [name for name in _BROAD_SIDS.values()
              if f"{name.lower()}:(" in acl]
    if leaked:
        yield (f"WARNING: agents.json still grants {leaked} — NOT hardened. "
               f"ACL:\n{acl}")
    else:
        yield f"agents.json ACL hardened (Admins:F, {principal}:R)"


def main(argv=()):
    """Install. `argv` holds the flag list only (see `__main__`); the default
    keeps `main()` callers — the test suites — from inheriting unittest's own
    argv."""
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install the model-proctor harness into C:\\Tools\\model-proctor.")
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite installed files that differ from both the repo source and "
             f"{MANIFEST_NAME}, destroying uncommitted local patches")
    args = parser.parse_args(list(argv))

    targets = _targets()
    manifest = read_manifest(TOOL_DIR)
    patches = local_patches(TOOL_DIR, targets, manifest)
    if patches and not args.force:
        print("REFUSED: these installed files differ from both the repo source and "
              f"{MANIFEST_NAME}, so they hold content that exists in no commit. "
              "Overwriting them would delete it:")
        for _name, dst, src in patches:
            print(f"\n  {dst}")
            diff, omitted = _diff_preview(dst, src)
            for line in diff:
                print(f"    {line}")
            if omitted:
                print(f"    ... {omitted} more diff lines")
        if not manifest:
            print(f"\nNo {MANIFEST_NAME} exists yet, so nothing installed before "
                  "this run can be recognised as the installer's own output.")
        print("\nCommit or file the patch first, then re-run. If the diff above is "
              "only a newer repo — or an install made before this guard existed — "
              "overwriting loses nothing: install.py --force")
        return REFUSED_LOCAL_PATCH

    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    for name in DELEGATE_FILES:
        shutil.copy2(KIMI / "delegate" / name, TOOL_DIR / name)
    for base, subdir, name in RUNNER_FILES:
        shutil.copy2(base / subdir / name, TOOL_DIR / name)
    # Written from what is actually on disk after the copies, so the next run
    # can tell "a previous install wrote this" from "somebody patched it".
    write_manifest(TOOL_DIR, [name for name, _src in targets])
    print(f"copied delegate + runner files to {TOOL_DIR} "
          f"(recorded in {MANIFEST_NAME})")

    agents_dst = TOOL_DIR / "agents.json"
    agents_src = KIMI / "delegate" / "agents.json"
    if agents_src.is_file():
        domain = os.environ.get("USERDOMAIN", "")
        user = os.getlogin()
        principal = f"{domain}\\{user}" if domain else user
        # The hardening below leaves the current user with R, so a second run's
        # copy2 would raise PermissionError against its own previous run. Skip
        # when the roster is unchanged; restore write first when it is not.
        if agents_dst.is_file() and _sha256(agents_src) == _sha256(agents_dst):
            print("agents.json unchanged — copy skipped, re-hardening ACL")
        else:
            if agents_dst.is_file():
                _icacls(str(agents_dst), "/grant", f"{principal}:(M)")
            shutil.copy2(agents_src, agents_dst)
        for line in harden_acl(agents_dst, principal):
            print(line)
    else:
        print("agents.json not present (untracked, machine-local) — skipped; "
              "regenerate from agents.example.json per delegate/README.md")

    for skill in SKILLS:
        skill_dir = Path(os.environ["USERPROFILE"]) / ".kimi-code" / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(KIMI / "skill" / skill / "SKILL.md", skill_dir / "SKILL.md")
        print(f"skill installed to {skill_dir}")

    missing = [n for n in REQUIRED_AFTER_INSTALL if not (TOOL_DIR / n).is_file()]
    if missing:
        print(f"ERROR: install incomplete, missing from {TOOL_DIR}: {missing}")
        return 1

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
