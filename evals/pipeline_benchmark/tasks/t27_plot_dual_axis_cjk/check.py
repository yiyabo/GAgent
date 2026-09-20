import json
import re
import sys
from pathlib import Path

RESULT_DIR = Path(sys.argv[1]).resolve()
RESULT = json.loads((RESULT_DIR / "result.json").read_text(encoding="utf-8"))
SESSION = (RESULT_DIR / "session").resolve()
ANSWER = str(RESULT.get("final_answer") or "")


def find_file(name):
    for base in (SESSION / "deliverables", SESSION / "results", SESSION):
        cand = base / name
        if cand.is_file():
            return cand
    hits = sorted(p for p in SESSION.rglob(name) if p.is_file())
    return hits[0] if hits else None


def uploads():
    udir = SESSION / "uploads"
    assert udir.is_dir(), "uploads directory missing in session"
    return udir


def num_in_text(text, value, nd=2):
    compact = str(text).replace(",", "")
    target = round(float(value), nd)
    forms = {f"{target:.{nd}f}"}
    if float(target).is_integer():
        forms.add(str(int(target)))
    return any(form in compact for form in forms)


def close2(a, b, nd=2):
    return abs(round(float(a), nd) - round(float(b), nd)) <= 1e-6


assert RESULT.get("ok_run"), f"driver run failed: {RESULT.get('error')}"


def check_png(name, min_bytes=10240, min_var=100.0):
    path = find_file(name)
    assert path, f"{name} not found under session dir"
    data = path.read_bytes()
    assert len(data) > min_bytes, f"{name} too small ({len(data)} bytes)"
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"
    import io as _io
    from PIL import Image, ImageStat
    img = Image.open(_io.BytesIO(data)).convert("L")
    var = ImageStat.Stat(img).var[0]
    assert var > min_var, f"{name} looks blank (pixel variance {var:.1f})"
    return path


def has_inline_image(text):
    return bool(re.search(r"!\[[^\]]*\]\([^)]+\)", text))

check_png('dual_axis.png')
assert has_inline_image(ANSWER), "final_answer has no inline ![...](...) image reference"
