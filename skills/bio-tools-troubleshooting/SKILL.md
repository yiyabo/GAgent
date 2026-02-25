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

## Error classes
- Permission/identity errors
- Missing file/path mapping errors
- Missing database/precondition errors
- Unsupported data format or tool-level constraints
- Timeout/runtime saturation

## Reference
- Error groups: `references/error_catalog.md`
