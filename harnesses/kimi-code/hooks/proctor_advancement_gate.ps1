<#
.SYNOPSIS
    Kimi Code PreToolUse hook (matcher "Agent"): refuse a subagent spawn while a
    stop-severity escalation is unresolved, or while this fire's Job-0 output is
    UNKNOWN or stale.

.DESCRIPTION
    This is the HOST-LEVEL enforcement of the unified loop's advancement
    suppression (invariant I13). The prompt's Job 3 check is a readable reason;
    it is not enforcement - instruction is not enforcement. The disproof of the
    loop design (review/disprove-loop.md, rank 5) is the reason this file
    exists.

    Reads the Kimi hook event JSON from stdin. Contract (same as the other
    hooks on this host, e.g. qe-write-guard.ps1): exit 0 allows, exit 2 blocks
    and the stderr text becomes the block reason shown to the model.

    Decision path - deliberately thin, so the decision lives in one place:
      1. Escape hatches (KIMI_HOOKS_OFF=1, MODEL_PROCTOR_ADVANCEMENT_GATE=off).
      2. If the payload names a tool and it is not Agent, allow.
      3. Enumerate "<loop root>/*/installed.json"; keep the state dirs whose
         session_id matches the payload's session_id. No match means this
         session is not proctored by a bound loop: allow.
      4. For each matching state dir, ask
             python loopctl.py advancement --state-dir <dir>
         and read {"allowed": bool, "reason": str}. `loopctl advancement` is the
         single source of truth; advancement_allowed() in loopctl.py is what the
         loop suite tests directly.
      5. Not allowed -> exit 2 with the reason.

    FAIL-CLOSED AT THE GATE (a deliberate, documented deviation from the
    fail-open convention of the other hooks): when a loop IS bound to this
    session and the decision cannot be read, this hook BLOCKS. A fail-open
    spawn gate is exactly the "instruction, not enforcement" failure it was
    built to remove. With no bound loop state the hook is inert and allows.

.REGISTRATION (NOT performed by this file)
    Registering a hook edits ~/.kimi-code/config.toml, which is in the G5
    protected set (see qe-write-guard.ps1). That is a decision-seat step with a
    non-Kimi review, not something this hook or a worker may do. The intended
    registration, once the seat approves it:

        [[hooks]]
        event = "PreToolUse"
        matcher = "Agent"
        command = 'pwsh -NoProfile -NonInteractive -File "C:/Tools/model-proctor/loop/proctor_advancement_gate.ps1"'

    (The repo copy lives at harnesses/kimi-code/hooks/proctor_advancement_gate.ps1
    and is installed to C:\Tools\model-proctor\loop\ by the flat install.)

.ENVIRONMENT
    MODEL_PROCTOR_LOOP_ROOT        loop state root (default
                                   C:/Dev/bootstrap-state/model-proctor/loop)
    MODEL_PROCTOR_LOOPCTL          path to loopctl.py (default: next to this
                                   hook, then C:/Tools/model-proctor/loop)
    MODEL_PROCTOR_PYTHON           interpreter (default "python")
    MODEL_PROCTOR_ADVANCEMENT_GATE "off" disables this hook

.NOTES
    ASCII-only source. Runs on PowerShell 5.1 and 7. Never executes the tool it
    inspects; never writes anything.
#>
#Requires -Version 5.1
[CmdletBinding()]
param()

$script:LoopRootDefault = 'C:/Dev/bootstrap-state/model-proctor/loop'

function Block-Spawn {
    param([string]$Why)
    [Console]::Error.WriteLine("[proctor-advancement-gate] BLOCKED: $Why")
    [Console]::Error.WriteLine('Advancement suppression (invariant I13): no new worker is spawned while a stop-severity finding is unresolved, or while this fire''s Job 0 has not landed (UNKNOWN/stale is never clear). Clear the finding with `python loopctl.py clear --state-dir <dir> --id <key> --reason "<why>"`, or fix the Job-0 run, then retry. Set MODEL_PROCTOR_ADVANCEMENT_GATE=off only with an explicit owner decision.')
    exit 2
}

function Read-JsonFile {
    param([string]$Path)
    try {
        if (-not (Test-Path -LiteralPath $Path)) { return $null }
        return (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json)
    } catch { return $null }
}

try {
    $ErrorActionPreference = 'Stop'

    if ($env:KIMI_HOOKS_OFF -eq '1') { exit 0 }
    if ($env:MODEL_PROCTOR_ADVANCEMENT_GATE -eq 'off') { exit 0 }

    $rawPayload = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($rawPayload)) { exit 0 }
    try {
        $payload = $rawPayload | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine('[proctor-advancement-gate] warning: unparseable hook payload; allowing (fail-open: no loop decision was consulted).')
        exit 0
    }

    # ---- the tool arm: this hook exists for subagent spawns ----------------
    $toolName = $null
    foreach ($prop in @('tool_name', 'toolName', 'tool')) { if ($null -ne $payload.$prop) { $toolName = [string]$payload.$prop; break } }
    if (-not [string]::IsNullOrWhiteSpace($toolName) -and $toolName -ne 'Agent') { exit 0 }

    $root = $env:MODEL_PROCTOR_LOOP_ROOT
    if ([string]::IsNullOrWhiteSpace($root)) { $root = $script:LoopRootDefault }
    $root = ($root -replace '\\', '/').TrimEnd('/')
    if (-not (Test-Path -LiteralPath $root)) { exit 0 }

    $sessionId = [string]$payload.session_id
    if ([string]::IsNullOrWhiteSpace($sessionId)) { $sessionId = [string]$payload.sessionId }

    $candidates = @()
    foreach ($dir in (Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue)) {
        $installed = Read-JsonFile (Join-Path $dir.FullName 'installed.json')
        if ($null -eq $installed) { continue }
        if (-not [string]::IsNullOrWhiteSpace($sessionId)) {
            if ([string]$installed.session_id -ne $sessionId) { continue }
        }
        $candidates += [pscustomobject]@{ Dir = $dir.FullName; Installed = $installed }
    }
    if ($candidates.Count -eq 0) { exit 0 }   # this session has no bound loop

    # ---- resolve loopctl.py and the interpreter ---------------------------
    $loopctl = $env:MODEL_PROCTOR_LOOPCTL
    if ([string]::IsNullOrWhiteSpace($loopctl)) {
        $beside = Join-Path $PSScriptRoot 'loopctl.py'
        $installedPath = 'C:/Tools/model-proctor/loop/loopctl.py'
        if (Test-Path -LiteralPath $beside) { $loopctl = $beside }
        elseif (Test-Path -LiteralPath $installedPath) { $loopctl = $installedPath }
        else { $loopctl = Join-Path (Join-Path $PSScriptRoot '..') 'loop/loopctl.py' }
    }
    $python = $env:MODEL_PROCTOR_PYTHON
    if ([string]::IsNullOrWhiteSpace($python)) { $python = 'python' }

    foreach ($candidate in $candidates) {
        $stdout = $null
        $ok = $true
        try {
            $stdout = & $python $loopctl advancement --state-dir $candidate.Dir 2>$null
            if ($LASTEXITCODE -ne 0) { $ok = $false }
        } catch { $ok = $false }

        if (-not $ok) {
            Block-Spawn ("the loop bound to state dir '" + $candidate.Dir + "' could not produce an advancement decision (" + $python + " " + $loopctl + "). A bound loop whose gate is unreadable does not get a spawn: fail closed at the gate.")
        }

        $decision = $null
        try { $decision = ($stdout | Out-String) | ConvertFrom-Json } catch { $decision = $null }
        if ($null -eq $decision -or $null -eq $decision.allowed) {
            Block-Spawn ("the advancement decision from '" + $candidate.Dir + "' was unreadable (expected JSON with an 'allowed' field). Fail closed at the gate.")
        }
        if ($decision.allowed -ne $true) {
            Block-Spawn ("advancement is suppressed in '" + $candidate.Dir + "': " + [string]$decision.reason)
        }
    }

    exit 0
} catch {
    # A defect inside the hook itself is not a loop decision.
    [Console]::Error.WriteLine('[proctor-advancement-gate] warning: internal error; allowing (no loop decision was consulted).')
    exit 0
}
exit 0
