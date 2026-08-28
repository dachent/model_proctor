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
- Installs the static-cascade and model-proctor skills to
  %USERPROFILE%\\.kimi-code\\skills\\
- Verifies afterwards that every path the installed skill instructs a leader to
  run actually exists.

Idempotent in OUTCOME, not a no-op: every file is rewritten each run. Re-running
as the same non-elevated user is supported — the hardened agents.json is skipped
when unchanged, and write access is restored before overwriting when it differs.
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = Path(r"C:\Tools\model-proctor")
ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "icacls.exe")

DELEGATE_FILES = ["delegate.py", "agents.example.json", "README.md"]
# (subdir, filename) pairs copied flat into TOOL_DIR.
RUNNER_FILES = [("runner", "runner.py"), ("runner", "pilot.py"),
                ("evals", "pricing.yaml")]
SKILLS = ("static-cascade", "model-proctor")
# Everything the installed SKILL.md tells a leader to invoke or pass.
REQUIRED_AFTER_INSTALL = ["runner.py", "delegate.py", "pricing.yaml"]


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def main():
    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    for name in DELEGATE_FILES:
        shutil.copy2(ROOT / "delegate" / name, TOOL_DIR / name)
    for subdir, name in RUNNER_FILES:
        shutil.copy2(ROOT / subdir / name, TOOL_DIR / name)
    print(f"copied delegate + runner files to {TOOL_DIR}")

    agents_dst = TOOL_DIR / "agents.json"
    agents_src = ROOT / "delegate" / "agents.json"
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
        shutil.copy2(ROOT / "skill" / skill / "SKILL.md", skill_dir / "SKILL.md")
        print(f"skill installed to {skill_dir}")

    missing = [n for n in REQUIRED_AFTER_INSTALL if not (TOOL_DIR / n).is_file()]
    if missing:
        print(f"ERROR: install incomplete, missing from {TOOL_DIR}: {missing}")
        return 1

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
