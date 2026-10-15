# Task input visibility, shell recovery and useful progress

The live Task 17 run exposed three execution issues after independent acceptance
was disabled. The application could read the upstream corpus but its code kernel
mounted only the current task directory. A terminal fallback then attempted to
bind application-private `/tmp` identity files on the host Docker daemon. Even
after the Agent recovered the data, the file-only 14-step progress guard ended
the run while it was still reading and analyzing records.

## Changes

- Code kernels mount the current session read-only at the same path as the app,
  plus their own task workspace read/write. They can directly read prior task
  outputs and uploads; output collection still scans only the current workspace.
  No whole-runtime or cross-user directory is exposed.
- The production sandbox terminal uses a real PTY inside its own container and
  the existing authenticated cell callback channel. It supports raw input,
  resize, Ctrl-C, EOF and teardown without Docker exec/attach hijacking or
  application-private passwd/group bind files. Native-host development retains
  the direct Docker transport. Explicit legacy qwen-code mode is unchanged.
- The terminal tool binds the trusted task work directory, and session reuse is
  scoped by workspace. The shell and code tools share the session input view.
- In Agent-managed Plan execution, new successful observations or computations
  reset stagnation. Stable fingerprints exclude transport timestamps, durations,
  request IDs and kernel counters. Repeated observations and failed calls do not
  reset it. Receipts survive existing controller checkpoints. This records
  progress, not acceptance: no output file or quality claim is synthesized.
  Wall-clock limits, iteration caps, cancellation and failure traps still apply.

## Proof and rollout

Unit tests cover mount translation and boundaries, observations/replay/error/time
limits, and a real local PTY/socket round-trip including resize and interruption.
`deploy/verify-task-runtime.py` runs through stdin in an actual app container:
it creates an isolated session, reads a predecessor file in a code kernel,
writes an output, confirms upstream read-only access, uses the terminal to read
the same file, and removes its test containers/files. It makes zero model calls.
The smoke is part of main CI's real-stack verification as well as the production
candidate check, so ordinary mocks cannot hide Docker namespace errors again.
CI uses a stdlib-only Python image for this transport probe; the production
candidate uses the deployed scientific sandbox image. The independent sandbox
image CI still builds and scans the full scientific recipe.

Deploy the application image only after CI, idle gate and backups. Keep the old
application image for rollback. No Plan definitions, research files, model
configuration or acceptance settings are changed; no research task is rerun.
