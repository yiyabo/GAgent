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

df = pd.read_csv(uploads() / "sales12.csv")
df["月"] = df["日期"].str[5:7].astype(int)
truth = df.groupby(["地区", "月"])["销售额"].sum().unstack(fill_value=0.0)
out = find_file("pivot.csv")
assert out, "results/pivot.csv not found"
got = pd.read_csv(out)
assert "地区" in got.columns, "地区 column missing"
for m in range(1, 13):
    assert f"{m}月" in got.columns, f"column {m}月 missing"
got = got.set_index("地区")
for region in truth.index:
    assert region in got.index, f"region {region} missing"
    for m in range(1, 13):
        exp = float(truth.loc[region, m]) if m in truth.columns else 0.0
        assert close2(got.loc[region, f"{m}月"], exp), f"{region} {m}月 wrong"
best = truth.stack().idxmax()
assert best[0] in ANSWER and f"{best[1]}月" in ANSWER, "best combo missing in answer"
