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

df = pd.read_csv(uploads() / "measurements.csv")
q1 = df["测量值"].quantile(0.25)
q3 = df["测量值"].quantile(0.75)
iqr = q3 - q1
lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
truth = df[(df["测量值"] < lo) | (df["测量值"] > hi)]
out = find_file("outliers.csv")
assert out, "results/outliers.csv not found"
got = pd.read_csv(out)
assert set(got["样本号"]) == set(truth["样本号"]), f"outlier ids wrong: {sorted(got['样本号'])}"
for _, row in truth.iterrows():
    match = got[got["样本号"] == row["样本号"]]
    assert len(match) == 1 and close2(match["测量值"].iloc[0], row["测量值"])
assert num_in_text(ANSWER, len(truth), 0), f"count {len(truth)} missing in answer"
