import csv
import json
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

uploads = SESSION / "uploads"
assert uploads.is_dir(), "uploads directory missing in session"

total = 0
kept = 0
group_values = {}
for path in sorted(uploads.glob("batch_*.csv")):
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            total += 1
            if row["status"] == "ok" and float(row["value"]) > 50:
                kept += 1
                group_values.setdefault(row["group"], []).append(float(row["value"]))

assert total > 0 and kept > 0, "fixture recompute produced no rows"
assert "execute_code" in (RESULT.get("tools_used") or []), "execute_code not in tools_used"

assert num_in_text(ANSWER, total, 0), f"total rows {total} missing in answer"
assert num_in_text(ANSWER, kept, 0), f"kept rows {kept} missing in answer"
for group, values in sorted(group_values.items()):
    mean = sum(values) / len(values)
    assert group in ANSWER, f"group {group} missing in answer"
    assert num_in_text(ANSWER, mean), f"group {group} mean {mean:.2f} missing in answer"
