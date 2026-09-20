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

df = pd.read_csv(uploads() / "requests.csv")
hours = df["时间戳"].str[:13] + ":00"
truth = hours.value_counts().sort_index()
peak_hour = truth.idxmax()
out = find_file("hourly_counts.csv")
assert out, "results/hourly_counts.csv not found"
got = pd.read_csv(out)
assert "小时" in got.columns and "请求数" in got.columns, "columns wrong"
got_map = dict(zip(got["小时"], got["请求数"]))
assert len(got_map) == len(truth), "hour count wrong"
for hour, cnt in truth.items():
    assert int(got_map.get(hour, -1)) == int(cnt), f"{hour} count wrong"
cands = [peak_hour, peak_hour + ":00", peak_hour.replace(" ", "T")[:-3],
         peak_hour[:-3] + "时"]
assert any(c in ANSWER for c in cands), f"peak hour {peak_hour} missing in answer"
