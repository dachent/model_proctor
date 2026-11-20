#!/usr/bin/env bash
# Scope gate: this repo is Kimi Code only and carries no org-specific strings.
# Prints every offending line and exits 1; exits 0 when clean. Run from the repo root.
#
# The patterns are assembled from parts so this file does not match itself, and the
# gate's own files are excluded as well.
set -u

harness="co""dex|zc""ode|her""mes"
org="WK""FINOPS|fs""network|Fert""ility|Boris""Vaisman|fs""n[_-]|\bfs""n\b|wk_""finops|1_FS_""Financials|weekly_""emr"

self=(':!scripts/scope_gate.sh' ':!.github/workflows/ci.yml')

# Harness rule exclusions, each deliberate:
#  - docs/baseline/*: records that the other harnesses were removed.
#  - the codex-advisor model seat and the codex-cli pricing key name a model used
#    FROM Kimi, not harness code; editing an eval arm would change the eval (T6b decision 1).
#  - dated historical records keep the words they were written with (T6b decision 2).
harness_excl=(':!docs/baseline' ':!evals/pricing.yaml'
              ':!evals/skills/C/multi-model-routing/SKILL.md'
              ':!harnesses/kimi-code/skill/multi-model-routing/SKILL.md'
              ':!policy/delegation-policy.md'
              ':!docs/superpowers/plans' ':!policy/FINDINGS-2026-09-09.md')

# Org rule exclusions: only the redaction test, which must name the string it forbids.
org_excl=(':!scripts/tests/test_wire_fixture.py')

fail=0
if git grep -n -i -E "\b($harness)\b" -- . "${self[@]}" "${harness_excl[@]}"; then
  echo "scope gate: other-harness reference above (this repo is Kimi Code only)"; fail=1
fi
if git grep -n -i -E "$org" -- . "${self[@]}" "${org_excl[@]}"; then
  echo "scope gate: org-specific string above (use example-org / example-repo / PROJ-n)"; fail=1
fi
exit $fail
