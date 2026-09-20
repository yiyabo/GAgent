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

orders = pd.read_csv(uploads() / "orders.csv")
customers = pd.read_csv(uploads() / "customers.csv")
merged = orders.merge(customers, on="客户ID", how="inner")
truth = (merged.groupby(["客户ID", "姓名"])["金额"].sum().reset_index()
         .sort_values("金额", ascending=False).head(10))
out = find_file("customer_totals.csv")
assert out, "results/customer_totals.csv not found"
got = pd.read_csv(out)
assert len(got) == 10, f"row count {len(got)} != 10"
assert set(got["客户ID"]) == set(truth["客户ID"]), "top10 customers wrong"
vals = list(got["消费总额"])
assert all(close2(vals[i], vals[i + 1]) or vals[i] > vals[i + 1] for i in range(9)), "not desc"
truth_map = dict(zip(truth["客户ID"], truth["金额"]))
for _, row in got.iterrows():
    assert close2(row["消费总额"], truth_map[row["客户ID"]]), f"{row['客户ID']} total wrong"
top = truth.iloc[0]
assert str(top["姓名"]) in ANSWER and num_in_text(ANSWER, float(top["金额"])), "top customer missing"
