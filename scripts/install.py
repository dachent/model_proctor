"""Install the kimi-router harness durably (idempotent).

- Copies delegate wrapper files to C:\\Tools\\kimi-router\\
- ACL-hardens the installed agents.json (Administrators: full, current user: read)
  so a compromised worker cannot rewrite the trusted command config.
  Re-run this script (elevated or after taking ownership) to update the roster.
- Installs the multi-model-routing skill to %USERPROFILE%\\.kimi-code\\skills\\
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = Path(r"C:\Tools\kimi-router")

FILES = ["delegate.py", "agents.json", "agents.example.json", "README.md"]


def main():
    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(ROOT / "delegate" / name, TOOL_DIR / name)
    print(f"copied {len(FILES)} files to {TOOL_DIR}")

    agents = TOOL_DIR / "agents.json"
    domain = os.environ.get("USERDOMAIN", "")
    user = os.getlogin()
    principal = f"{domain}\\{user}" if domain else user
    cmd = [
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe"),
        str(agents), "/inheritance:r",
        "/grant:r", "Administrators:(OI)(CI)F",
        "/grant:r", f"{principal}:(OI)(CI)R",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"WARNING: icacls failed: {r.stdout} {r.stderr}")
    else:
        print(f"agents.json ACL hardened (Admins:F, {principal}:R)")

    for skill in ("static-cascade",):
        skill_dir = Path(os.environ["USERPROFILE"]) / ".kimi-code" / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "skill" / skill / "SKILL.md", skill_dir / "SKILL.md")
        print(f"skill installed to {skill_dir}")

    print("done")


if __name__ == "__main__":
    sys.exit(main())
