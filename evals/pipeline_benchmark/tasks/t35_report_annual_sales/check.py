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


def report_text(name, min_chars):
    path = find_file(name)
    assert path, f"{name} not found under session dir"
    text = path.read_text(encoding="utf-8")
    n_chars = len(re.sub(r"\s", "", text))
    assert n_chars >= min_chars, f"{name} too short ({n_chars} non-space chars < {min_chars})"
    return text


def has_md_table(text):
    lines = text.splitlines()
    for i, ln in enumerate(lines[:-1]):
        if "|" not in ln:
            continue
        nxt = lines[i + 1]
        if "|" in nxt and "-" in nxt and re.match(r"^[\s:|-]+$", nxt.replace("|", " ").strip() or "-"):
            return True
    return False


def require_sections(text, sections):
    for sec in sections:
        assert sec in text, f"section {sec!r} missing in report"

import pandas as pd

df = pd.read_csv(uploads() / "daily_sales.csv")
total = float(df["销售额"].sum())
by_month = df.groupby(df["日期"].str[:7])["销售额"].sum().sort_values(ascending=False)
best_month, best_val = str(by_month.index[0]), float(by_month.iloc[0])

text = report_text("sales_report.md", 1500)
require_sections(text, ["## 数据概览", "## 月度趋势分析", "## 关键指标", "## 结论与建议"])
assert has_md_table(text), "no markdown table in report"
assert num_in_text(text, total), f"annual total {total:.2f} missing"
assert best_month in text, f"best month {best_month} missing"
assert num_in_text(text, best_val), f"best month value {best_val:.2f} missing"
