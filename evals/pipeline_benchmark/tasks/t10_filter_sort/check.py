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

df = pd.read_csv(uploads() / "transactions.csv")
truth = df[(df["状态"] == "完成") & (df["金额"] > 500)].sort_values("金额", ascending=False).head(20)
out = find_file("filtered_top20.csv")
assert out, "results/filtered_top20.csv not found"
got = pd.read_csv(out)
assert len(got) == len(truth), f"row count {len(got)} != {len(truth)}"
assert list(got["交易号"]) == list(truth["交易号"]), "交易号顺序不对"
for a, b in zip(got["金额"], truth["金额"]):
    assert close2(a, b), "金额值不对"
assert num_in_text(ANSWER, len(truth), 0), "count missing in answer"
assert num_in_text(ANSWER, float(truth["金额"].iloc[0])), "max amount missing in answer"
