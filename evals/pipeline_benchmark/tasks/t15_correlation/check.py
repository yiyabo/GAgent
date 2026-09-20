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

import pandas as pd

df = pd.read_csv(uploads() / "metrics.csv")
truth = df.corr()
out = find_file("correlation.csv")
assert out, "results/correlation.csv not found"
got = pd.read_csv(out)
cols = ["a", "b", "c", "d", "e"]
first = got.columns[0]
for c in cols:
    assert c in got.columns, f"column {c} missing"
got = got.set_index(first)
for r in cols:
    for c in cols:
        assert close2(got.loc[r, c], truth.loc[r, c], nd=4), f"corr({r},{c}) wrong"
best_pair, best_val = None, 0.0
for i, r in enumerate(cols):
    for c in cols[i + 1:]:
        if abs(truth.loc[r, c]) > best_val:
            best_val, best_pair = abs(truth.loc[r, c]), (r, c)
assert best_pair[0] in ANSWER and best_pair[1] in ANSWER, "strongest pair missing in answer"
