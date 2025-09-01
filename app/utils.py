import json
import re
from typing import Any, Optional, Tuple, Union, List, Dict


def parse_json_obj(text: str):
    """Try to parse a JSON object or array from arbitrary LLM output.

    Strategy:
    - Prefer extracting the first {...} or [...] block.
    - Attempt json.loads; if it fails, try replacing single quotes with double quotes and retry.
    - Return a dict/list on success, else None.
    """
    # Extract a JSON-looking block first (support object or array)
    m = re.search(r"\{.*\}|\[.*\]", text, flags=re.S)
    cand = m.group(0) if m else text.strip()
    try:
        obj = json.loads(cand)
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    # Try single->double quotes
    try:
        obj = json.loads(cand.replace("'", '"'))
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    return None
