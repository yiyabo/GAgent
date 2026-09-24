import json
import sys
from pathlib import Path

RESULT_DIR = Path(sys.argv[1]).resolve()
RESULT = json.loads((RESULT_DIR / "result.json").read_text(encoding="utf-8"))
SESSION = (RESULT_DIR / "session").resolve()
ANSWER = str(RESULT.get("final_answer") or "")

assert RESULT.get("ok_run"), f"driver run failed: {RESULT.get('error')}"
assert not RESULT.get("bailout_phrase"), "final answer is a fallback/bailout phrase"

note = SESSION / "uploads" / "note.txt"
assert note.is_file(), "fixture missing in session uploads"

# Truth recomputed from the fixture: every non-comment key line of the form
# "<label>: <value>" must appear in the answer.
missing = []
for line in note.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or ":" not in line or line.startswith("备注"):
        continue
    value = line.split(":", 1)[1].strip()
    if value and value not in ANSWER:
        missing.append(value)
assert not missing, f"note values missing in answer: {missing}"

# the file content only becomes reachable through a working read path; the
# model must have fallen back to a regular tool instead of forcing the
# execute_code allowlist (file_operations is NOT in the code-mode allowlist).
used = set(RESULT.get("tools_used") or [])
readers = {"file_operations", "document_reader"}
assert used & readers, f"no regular file-read tool used (tools_used={sorted(used)})"
