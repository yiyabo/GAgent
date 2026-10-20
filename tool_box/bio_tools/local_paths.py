"""Map local input parameters into a sibling Docker container."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.session_paths import host_path_for


def mount_input_parameters(
    params: Dict[str, Any],
    mounts: List[str],
    *,
    work_dir: str,
    input_dir: Optional[str],
    input_keys: set,
    multi_keys: set,
) -> None:
    work_root = Path(work_dir).resolve()
    input_root = Path(input_dir).resolve() if input_dir else None

    def mapped(value: str, key: str, number: int) -> str:
        if not value or value.startswith(("/work/", "/input/", "/db/")):
            return value
        path = Path(value)
        if not path.is_absolute():
            return value  # Relative paths already resolve below /work.
        real = path.resolve()
        if real.is_relative_to(work_root):
            return "/work/" + str(real.relative_to(work_root))
        if input_root and real.is_relative_to(input_root):
            return "/input/" + str(real.relative_to(input_root))
        if not real.exists():
            raise ValueError("Input parameter %s does not exist: %s" % (key, value))
        destination = "/param_inputs/%s_%d" % (key, number)
        mount = "%s:%s:ro" % (host_path_for(real.parent), destination)
        if mount not in mounts:
            mounts.append(mount)
        return destination + "/" + real.name

    for key in input_keys - {"input", "db"}:
        raw = params.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if key in multi_keys:
            params[key] = ",".join(mapped(part.strip(), key, n) for n, part in enumerate(raw.split(",")))
        else:
            params[key] = mapped(raw, key, 0)
