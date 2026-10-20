---
name: bio-tools-troubleshooting
description: "Troubleshooting skill for bio_tools failures in remote execution mode. Trigger on permission/path/database/parameter/runtime errors and provide deterministic retry sequences."
---

# Bio Tools Troubleshooting

## Recovery sequence
1. Re-run with `operation="help"` for the same tool.
2. Validate path and required params against help output.
3. Confirm remote DB preconditions for DB-backed tools.
4. Retry with corrected params.
5. If still failing, switch to nearest supported alternative operation/tool.

## Native server error codes
- `database_unavailable` / `tool_unavailable`: the reference database or software is not installed on this server.
  Report it; do not retry. gtdbtk and iphop have no database yet.
- `native_runner_unavailable`: the host runner is not reporting; try again later or tell the user.
- `path_not_allowed`: the path is outside session files and shared reference data; use a session file.
- `not_native`: this operation has no host implementation (dorado, nextflow, snakemake).
- `failed` with tool output in `stderr`: the tool ran and rejected the data. Read the message first
  (maxbin2: "dataset cannot be binned" means too few marker genes; metabat2 writes nothing for tiny inputs).

## Error classes
- Permission/identity errors
- Missing file/path mapping errors
- Missing database/precondition errors
- Unsupported data format or tool-level constraints
- Timeout/runtime saturation

## Reference
- Error groups: `references/error_catalog.md`
