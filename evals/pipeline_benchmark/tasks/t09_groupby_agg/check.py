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

df = pd.read_csv(uploads() / "orders2.csv")
truth = df.groupby("类别")["金额"].agg(["count", "sum", "mean"]).sort_values("sum", ascending=False)
out = find_file("category_summary.csv")
assert out, "results/category_summary.csv not found"
got = pd.read_csv(out)
for col in ("类别", "订单数", "总金额", "平均金额"):
    assert col in got.columns, f"column {col} missing"
assert len(got) == len(truth), f"row count {len(got)} != {len(truth)}"
assert list(got["类别"]) == list(truth.index), "not sorted by 总金额 desc"
for i, cat in enumerate(truth.index):
    row = got.iloc[i]
    assert int(row["订单数"]) == int(truth["count"].iloc[i]), f"{cat} count wrong"
    assert close2(row["总金额"], truth["sum"].iloc[i]), f"{cat} sum wrong"
    assert close2(row["平均金额"], truth["mean"].iloc[i]), f"{cat} mean wrong"
top_cat = str(truth.index[0])
assert top_cat in ANSWER and num_in_text(ANSWER, truth["sum"].iloc[0]), "top category missing in answer"
