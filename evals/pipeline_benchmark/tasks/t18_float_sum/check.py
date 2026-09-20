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

from decimal import Decimal

lines = (uploads() / "payments.csv").read_text(encoding="utf-8").splitlines()[1:]
truth = sum(Decimal(ln.split(",")[1]) for ln in lines if ln.strip())
out = find_file("total.txt")
assert out, "results/total.txt not found"
body = out.read_text(encoding="utf-8").strip()
m = re.search(r"-?\d+(\.\d+)?", body)
assert m, "total.txt has no number"
assert close2(Decimal(m.group(0)), truth), f"total {m.group(0)} != {truth}"
assert num_in_text(ANSWER, float(truth)), "total missing in answer"
