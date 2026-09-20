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

from collections import Counter

lines = (uploads() / "app.log").read_text(encoding="utf-8").splitlines()
errors = [ln for ln in lines if " ERROR " in ln]
first_ts = errors[0][:19]
types = Counter(re.search(r"\[(\w+Error)\]", ln).group(1) for ln in errors)
top_type = types.most_common(1)[0][0]

assert num_in_text(ANSWER, len(errors), 0), f"error count {len(errors)} missing"
assert first_ts in ANSWER, f"first error ts {first_ts} missing"
assert top_type in ANSWER, f"top error type {top_type} missing"

out = find_file("error_summary.txt")
assert out, "results/error_summary.txt not found"
body = out.read_text(encoding="utf-8")
assert body.strip(), "error_summary.txt is empty"
for etype, cnt in types.items():
    assert etype in body, f"{etype} missing in summary"
    m = re.search(re.escape(etype) + r"[^0-9]*(\d+)", body)
    assert m and int(m.group(1)) == cnt, f"{etype} count wrong in summary"
