{{HEADER}} unified session controller loop - one cron, five jobs, fixed order

This prompt IS the loop. It is re-injected on every fire, so it is also the only
memory the loop has that survives compaction. Keep it exact; do not edit it by
hand (render + bind only).

SESSION
  session_id   {{SESSION_ID}}
  cadence      every {{CADENCE}} minutes  (cron "{{CRON}}")
  fire budget  {{FIRE_BUDGET}}s total; jobs budgeted at {{TOTAL_JOB_BUDGET}}s
  state dir    {{STATE_DIR}}
  wires        {{WIRE_GLOBS}}
               under {{SESSIONS_ROOT}}
  journal      {{JOURNAL}}
  output cap   {{MAX_LINES}} lines per fire, verdict line first

BOUNDED, NOT NON-BLOCKING
  The owner types into this same session. Steering therefore queues behind at
  most ONE fire: worst case the owner waits {{FIRE_BUDGET}}s. A fire that runs
  long is not "free"; it is preemption. Return inside the budget.

JOB REGISTRY - run in this exact order, every fire, no reordering

{{JOBS}}

JOB 0 - ESCALATE (runs first; can suppress JOB 3)
  Run: python <loop>/loopctl.py job0 --state-dir "{{STATE_DIR}}" --session {{SESSION_ID}}
  It re-derives every finding from the live wires through the wirewatch
  evaluator. Exit 0 = NOMINAL, 1 = ESCALATED (stops found), 3 = UNKNOWN,
  4 = LOCKED.
  Emit its verdict line verbatim as this fire's first line. UNKNOWN is not
  clear: it suppresses JOB 3 exactly like a stop does.
  If the wire set exceeds {{JOB0_MAX_BYTES}} or {{JOB0_MAX_WIRES}} wires, loopctl
  returns UNKNOWN(job0_detached_required) in about a second. Launch the same
  command detached (Bash run_in_background), report UNKNOWN this fire, and let
  a later fire pick up the landed summary. Never spawn a worker on the strength
  of last fire's summary.

JOB 1 - DELTA (measure only)
  Compare the ledger timestamps, live dispatches and worktrees against the
  ledger below. Report only what changed. No mutation, no fixing, no merge, no
  push, no PR, no steer. A fire that fixes things competes with the work.

JOB 2 - VERDICT + DELIVER
  Emit one machine line, then at most a few human lines:
    LOOP:v{{LOOP_VERSION}} FIRE <ESCALATED|NOMINAL|UNKNOWN|LOCKED|DRIFT> ...
  Dedupe on the identity key (trigger, wire, evidence fingerprint) held in
  {{STATE_DIR}}/delivered.jsonl. Routine findings once; stop-severity every
  fire until the owner runs `loopctl.py clear`. The toast path
  (SessionHeartbeat -> loopctl notify) is best-effort; the durable record is
  delivered.jsonl + escalations.jsonl + owner-inbox.jsonl.

JOB 3 - ADVANCE (at most ONE detached worker)
  Suppressed this fire - emit NOMINAL suppressed=<reason> and spawn nothing -
  when `loopctl.py advancement` says not allowed. That means: unresolved
  stop-severity findings, or this fire's JOB 0 did not land (UNKNOWN or stale).
  A PreToolUse hook (matcher "Agent") refuses the spawn at the host level in the
  same situations; the check here is the readable reason, not the enforcement.
  Before spawning, check TaskList and the journal for a worker already on this
  item: duplicate => NOMINAL, one line, done. Spawn only into a clean pinned
  tree. One worker per fire, detached.

JOB 4 - RECONCILE
  (a) Drift, accident detection only: python <loop>/loopctl.py verify
      --state-dir "{{STATE_DIR}}". A DRIFT verdict means the template changed
      after bind: emit DRIFT, skip JOB 1-3 this fire, tell the owner to re-bind.
      This catches an accident. It cannot prove the live cron prompt is the
      rendered one: both sides are agent-writable and CronList truncates the
      prompt. The "LOOP:v" header in transcripts is the only version proof, and
      it needs a reader.
  (b) Exactly one cron: list cron tasks, assert exactly one belongs to this
      loop. Any second slot is a defect: delete it, do not "keep it as a
      watcher".
  (c) Lock and cancel markers under {{STATE_DIR}}: respect an owner-cancel
      marker (act on nothing, say cancelled), refresh the lock only through
      loopctl.
  (d) Refresh the ledger below from what this fire measured.

OUTCOME VOCABULARY - every job must end in one of these, spelled exactly
  {{OUTCOMES}}
  ESCALATED is reserved for JOB 0. DRIFT is reserved for JOB 4. LOCKED means
  another fire holds the lock and this one acted on nothing. UNKNOWN is never a
  clear: unmeasurable is named, and named is not passed.

INVARIANTS - tool-enforced vs convention

  Tool-enforced (a program catches the violation, not the reader's trust):
    I1  one slot          JOB 4 asserts exactly one cron; bind renders the new
                          prompt before the old slot is deleted
    I3  cadence > work    `loopctl bind` refuses a spec whose cadence is under
                          3x the fire budget
    I6  dedupe            loopctl notify owns the delivered.jsonl comparison
    I13 advancement       loopctl advancement returns False on unresolved stops
        suppression       and on UNKNOWN/stale JOB 0 (fail closed); the
                          PreToolUse "Agent" hook blocks the spawn at the host
    I14 no self-suppress  evaluator.check_out_dir refuses findings written
                          inside the workspace or a session dir (TOOL-014)

  Convention (prompt-honored; nothing blocks a violation):
    I2  bounded fire      a cron fire is a model turn with no hard timeout
    I5  exact strings     string drift between this prompt and the emitters is
    I12                   the bbad08a0 defect class; only review catches it
    I7  transition-firing the prompt cannot prove a key was not re-delivered
    I9  self-lock         the lock is checked by the turn it is meant to restrain
    I10 owner-cancel      the marker is honored, not enforced
    I11 memory on disk    compaction erases context; the prompt is the memory

  Drift guard scope: {{STATE_DIR}}/installed.json records the scope string
  "accident-detection-only". Treat a green verify as "nothing obvious changed",
  never as "the live prompt is correct".

THRESHOLDS - bound, never forked
  {{THRESHOLDS}}
  These bind core/wire_triggers.DEFAULTS, which binds core/decisions.py. A
  session never supplies thresholds.

LEDGER - refreshed by JOB 4, carried here so it survives compaction
{{LEDGER}}

NEVER
  A second cron (not even "just a watcher"). An LLM reading worker output to
  decide anything. Mutation inside a fire. Killing a worker - sole kill
  authority is delegate/killauthority.py, owner-invoked. Automated mid-turn
  steering (unverified; the owner types, or waits for the boundary). Loop state
  inside a proctored workspace. A trigger reading a worker-authored value to
  decide not to fire. Hand-editing this prompt or exceeding the line cap.
