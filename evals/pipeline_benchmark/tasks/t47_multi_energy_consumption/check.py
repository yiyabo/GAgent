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


def check_png(name, min_bytes=10240, min_var=100.0):
    path = find_file(name)
    assert path, f"{name} not found under session dir"
    data = path.read_bytes()
    assert len(data) > min_bytes, f"{name} too small ({len(data)} bytes)"
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"
    import io as _io
    from PIL import Image, ImageStat
    img = Image.open(_io.BytesIO(data)).convert("L")
    var = ImageStat.Stat(img).var[0]
    assert var > min_var, f"{name} looks blank (pixel variance {var:.1f})"
    return path


def has_inline_image(text):
    return bool(re.search(r"!\[[^\]]*\]\([^)]+\)", text))


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

df = pd.read_csv(uploads() / "energy.csv")
truth = df.groupby(df["日期"].str[:7])["耗电量"].sum()
out = find_file("monthly_energy.csv")
assert out, "results/monthly_energy.csv not found"
got = pd.read_csv(out)
assert len(got) == 12, "month count wrong"
for a, b in zip(got["耗电量合计"], truth.values):
    assert close2(a, b), "monthly energy wrong"
check_png("energy_bar.png")
best = str(truth.idxmax())
text = report_text("energy_report.md", 600)
require_sections(text, ["## 数据概览", "## 月度分析", "## 结论"])
assert num_in_text(text, float(df["耗电量"].sum())), "annual total missing"
assert best in text, f"peak month {best} missing"
