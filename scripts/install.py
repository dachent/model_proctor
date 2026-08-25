"""Install the kimi-router harness durably (idempotent).

- Copies the delegate wrapper files to C:\\Tools\\kimi-router\\
- Copies runner/runner.py to C:\\Tools\\kimi-router\\ (MVP-001 control plane)
- ACL-hardens the installed agents.json (Administrators: full, current user: read)
  so a compromised worker cannot rewrite the trusted command config.
  Re-run this script (elevated or after taking ownership) to update the roster.
  agents.json is machine-local and deliberately untracked; if absent it is
  skipped — regenerate it from agents.example.json per delegate/README.md.
- Installs the static-cascade and task-router skills to
  %USERPROFILE%\\.kimi-code\\skills\\
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = Path(r"C:\Tools\kimi-router")

DELEGATE_FILES = ["delegate.py", "agents.example.json", "README.md"]
RUNNER_FILES = [("runner", "runner.py")]
SKILLS = ("static-cascade", "task-router")


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
        shutil.copy2(agents_src, agents_dst)
        domain = os.environ.get("USERDOMAIN", "")
        user = os.getlogin()
        principal = f"{domain}\\{user}" if domain else user
        cmd = [
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe"),
            str(agents_dst), "/inheritance:r",
            "/grant:r", "Administrators:(OI)(CI)F",
            "/grant:r", f"{principal}:(OI)(CI)R",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"WARNING: icacls failed: {r.stdout} {r.stderr}")
        else:
            print(f"agents.json ACL hardened (Admins:F, {principal}:R)")
    else:
        print("agents.json not present (untracked, machine-local) — skipped; "
              "regenerate from agents.example.json per delegate/README.md")

    for skill in SKILLS:
        skill_dir = Path(os.environ["USERPROFILE"]) / ".kimi-code" / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "skill" / skill / "SKILL.md", skill_dir / "SKILL.md")
        print(f"skill installed to {skill_dir}")

    print("done")


if __name__ == "__main__":
    sys.exit(main())
