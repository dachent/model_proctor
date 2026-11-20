"""TEMPORARY (Plan 1 T1): run every suite from ci.yml with -v and record counts.

Writes baseline.json {suite: {ran, skipped, failed}}, skip_reasons.json and
versions.json into the directory given as argv[1]. Deleted after the baseline commit.
"""
import json
import os
import platform
import re
import subprocess
import sys

SUITES = [
    "harnesses/kimi-code/delegate/tests",
    "harnesses/codex/delegate/tests",
    "harnesses/codex/skill/model-proctor/tests",
    "harnesses/kimi-code/runner/tests",
    "harnesses/kimi-code/replay/tests",
    "harnesses/kimi-code/wirewatch/tests",
    "harnesses/kimi-code/loop/tests",
    "core/tests",
    "scripts/tests",
    "evals/tests",
    "harnesses/zcode/tests",
]

out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)
env = dict(os.environ, PYTHONIOENCODING="utf-8")
counts, reasons = {}, {}
for s in SUITES:
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", s, "-v"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    text = p.stdout + p.stderr
    ran = re.search(r"^Ran (\d+) tests? in", text, re.M)
    skipped = re.search(r"skipped=(\d+)", text)
    failed = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", text))
    counts[s] = {"ran": int(ran.group(1)) if ran else 0,
                 "skipped": int(skipped.group(1)) if skipped else 0,
                 "failed": failed if ran else max(failed, 1)}
    reasons[s] = sorted(set(re.findall(r"\.\.\. skipped (.*)$", text, re.M)))
    print(f"{s}: {counts[s]} rc={p.returncode}", flush=True)
json.dump(counts, open(os.path.join(out_dir, "baseline.json"), "w"), indent=2, sort_keys=True)
json.dump(reasons, open(os.path.join(out_dir, "skip_reasons.json"), "w"), indent=2, sort_keys=True)
json.dump({"python": sys.version, "platform": platform.platform(), "sha": os.environ.get("GITHUB_SHA")},
          open(os.path.join(out_dir, "versions.json"), "w"), indent=2)
