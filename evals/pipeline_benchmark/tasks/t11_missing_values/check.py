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

df = pd.read_csv(uploads() / "sensors.csv")
missing = df.isna().sum()
rep = find_file("missing_report.md")
assert rep, "results/missing_report.md not found"
body = rep.read_text(encoding="utf-8")
for col in df.columns:
    assert col in body, f"{col} missing in report"
    m = re.search(re.escape(col) + r"\D*(\d+)", body)
    assert m and int(m.group(1)) == int(missing[col]), f"{col} missing-count wrong"
filled_truth = df.copy()
for col in ("温度", "湿度", "气压"):
    filled_truth[col] = filled_truth[col].fillna(filled_truth[col].median())
out = find_file("filled.csv")
assert out, "results/filled.csv not found"
got = pd.read_csv(out)
assert len(got) == len(df), "filled.csv row count wrong"
assert not got[["温度", "湿度", "气压"]].isna().any().any(), "filled.csv still has NaN"
for col in ("温度", "湿度", "气压"):
    for a, b in zip(got[col], filled_truth[col]):
        assert close2(a, b), f"{col} value mismatch"
