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

df = pd.read_csv(uploads() / "products.csv")
truth = df.sort_values("销量", ascending=False).groupby("类别").head(3)
out = find_file("top3_per_category.csv")
assert out, "results/top3_per_category.csv not found"
got = pd.read_csv(out)
assert len(got) == len(truth), f"row count {len(got)} != {len(truth)}"
key = lambda d: {(r["类别"], r["产品"], int(r["销量"])) for _, r in d.iterrows()}
assert key(got) == key(truth), "top3 rows wrong"
for cat, grp in got.groupby("类别"):
    qtys = list(grp["销量"])
    assert qtys == sorted(qtys, reverse=True), f"{cat} not sorted desc"
for cat, grp in truth.groupby("类别"):
    champ = grp.sort_values("销量", ascending=False).iloc[0]
    assert str(champ["产品"]) in ANSWER, f"{cat} champion missing in answer"
