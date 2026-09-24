import csv
import json
import math
import sys
from pathlib import Path

RESULT_DIR = Path(sys.argv[1]).resolve()
RESULT = json.loads((RESULT_DIR / "result.json").read_text(encoding="utf-8"))
SESSION = (RESULT_DIR / "session").resolve()
ANSWER = str(RESULT.get("final_answer") or "")


def num_in_text(text, value, nd=2):
    compact = str(text).replace(",", "")
    target = round(float(value), nd)
    forms = {f"{target:.{nd}f}"}
    if float(target).is_integer():
        forms.add(str(int(target)))
    return any(form in compact for form in forms)


assert RESULT.get("ok_run"), f"driver run failed: {RESULT.get('error')}"
assert not RESULT.get("bailout_phrase"), "final answer is a fallback/bailout phrase"
assert "execute_code" in (RESULT.get("tools_used") or []), "execute_code not in tools_used"

xs, ys = [], []
with (SESSION / "uploads" / "measurements.csv").open(newline="", encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        xs.append(float(row["x"]))
        ys.append(float(row["y"]))
n = len(xs)
assert n > 2

mean_x = sum(xs) / n
mean_y = sum(ys) / n
sxx = sum((x - mean_x) ** 2 for x in xs)
syy = sum((y - mean_y) ** 2 for y in ys)
sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
pearson = sxy / math.sqrt(sxx * syy)
slope = sxy / sxx  # least squares, identical to numpy.polyfit(..., 1)[0]

assert num_in_text(ANSWER, pearson, 3), f"pearson {pearson:.3f} missing in answer"
assert num_in_text(ANSWER, mean_y, 2), f"mean_y {mean_y:.2f} missing in answer"
assert num_in_text(ANSWER, slope, 3), f"slope {slope:.3f} missing in answer"

# the import guard must not have leaked into the user-facing answer
assert "not allowed inside" not in ANSWER, "import-guard complaint leaked into final answer"
