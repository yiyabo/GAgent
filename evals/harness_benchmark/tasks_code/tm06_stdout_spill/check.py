import csv
import json
import sys
from pathlib import Path

RESULT_DIR = Path(sys.argv[1]).resolve()
RESULT = json.loads((RESULT_DIR / "result.json").read_text(encoding="utf-8"))
SESSION = (RESULT_DIR / "session").resolve()
ANSWER = str(RESULT.get("final_answer") or "")


def num_in_text(text, value, nd=4):
    compact = str(text).replace(",", "")
    target = round(float(value), nd)
    return f"{target:.{nd}f}" in compact


assert RESULT.get("ok_run"), f"driver run failed: {RESULT.get('error')}"
assert not RESULT.get("bailout_phrase"), "final answer is a fallback/bailout phrase"
assert "execute_code" in (RESULT.get("tools_used") or []), "execute_code not in tools_used"

with (SESSION / "uploads" / "sensors.csv").open(newline="", encoding="utf-8") as fh:
    readings = [float(row["reading"]) for row in csv.DictReader(fh)]
assert len(readings) == 3200, f"fixture row count drifted: {len(readings)}"
mean = sum(readings) / len(readings)
assert num_in_text(ANSWER, mean), f"MEAN {mean:.4f} missing in answer"

# Truncation + spill contract: the full stdout (3200-row dump ≈ 64KB, over the
# 50KB cap) must have been spilled under the session scratch dir, sentinels intact.
spill_dir = SESSION / "scratch" / "code_mode" / "spill"
spills = sorted(spill_dir.glob("stdout-*.txt")) if spill_dir.is_dir() else []
assert spills, f"no stdout spill under {spill_dir}"
big = [p for p in spills if p.stat().st_size > 50_000]
assert big, f"spill files all <=50KB: {[(p.name, p.stat().st_size) for p in spills]}"
content = big[0].read_text(encoding="utf-8", errors="replace")
assert "BEGIN-DUMP" in content, "spill content missing BEGIN-DUMP sentinel"
assert "END-DUMP" in content, "spill content missing END-DUMP sentinel"
