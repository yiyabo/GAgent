"""Report output paths that a local bio-tool run actually created."""

from pathlib import Path
from typing import Any, Dict, Optional


def collect_local_outputs(
    tool_dir: Path,
    *,
    tool_name: str,
    operation: str,
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    root = tool_dir.resolve()
    values = [output_file]
    values.extend((params or {}).get(key) for key in ("output", "protein", "nucleotide", "output_prefix"))
    found = []

    def add(value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        relative = value.removeprefix("/work/")
        if Path(relative).is_absolute():
            return
        path = (root / relative).resolve()
        if path.is_relative_to(root) and path.exists() and path not in found:
            found.append(path)

    for value in values:
        add(value)

    # makeblastdb writes several files with one prefix, not a file at the prefix itself.
    if operation == "makeblastdb" and tool_name == "blast":
        prefix = (params or {}).get("db")
    elif operation == "index" and tool_name == "bwa":
        prefix = (params or {}).get("index_prefix")
    else:
        prefix = None
    if prefix:
        if isinstance(prefix, str) and prefix and Path(prefix).name == prefix:
            for path in sorted(root.glob(prefix + ".*")):
                add(path.name)

    if not found:
        return {}
    result: Dict[str, Any] = {"produced_files": [str(path) for path in found]}
    if len(found) == 1 or (tool_name, operation) not in {("blast", "makeblastdb"), ("bwa", "index")}:
        result["output_path"] = str(found[0])
    return result
