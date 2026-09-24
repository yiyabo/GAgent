import csv
import json
import statistics
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


def load_events():
    path = RESULT_DIR / "events.jsonl"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def turn_final_answer(events, turn):
    for e in reversed(events):
        payload = e.get("payload") or {}
        if e.get("turn") == turn and payload.get("type") == "final":
            body = payload.get("payload") or {}
            return str(body.get("response") or (body.get("llm_reply") or {}).get("message") or "")
    return ""


def execute_code_steps(events, turn):
    n = 0
    for e in events:
        payload = e.get("payload") or {}
        if e.get("turn") == turn and payload.get("type") == "thinking_step":
            action = str((payload.get("step") or {}).get("action") or "")
            if "execute_code" in action:
                n += 1
    return n


def execute_code_snippets(events, turn):
    codes = []
    for e in events:
        payload = e.get("payload") or {}
        if e.get("turn") != turn or payload.get("type") != "final":
            continue
        meta = (payload.get("payload") or {}).get("metadata") or {}
        for item in meta.get("tool_results") or []:
            if not isinstance(item, dict) or str(item.get("name") or "") != "execute_code":
                continue
            params = item.get("parameters") or {}
            if isinstance(params, dict) and isinstance(params.get("code"), str):
                codes.append(params["code"])
    return codes


assert RESULT.get("ok_run"), f"driver run failed: {RESULT.get('error')}"
assert not RESULT.get("bailout_phrase"), "final answer is a fallback/bailout phrase"
assert len(RESULT.get("run_ids") or []) == 2, f"expected 2 turns, got {RESULT.get('run_ids')}"

with (SESSION / "uploads" / "series.csv").open(newline="", encoding="utf-8") as fh:
    values = [float(row["value"]) for row in csv.DictReader(fh)]
mean_v = statistics.mean(values)
outside = {
    sum(1 for v in values if abs(v - mean_v) > statistics.pstdev(values)),
    sum(1 for v in values if abs(v - mean_v) > statistics.stdev(values)),
}
assert len(outside) == 1, f"fixture is not ddof-invariant: {outside}"
outside_n = outside.pop()

events = load_events()
turn1_answer = turn_final_answer(events, 0)
assert num_in_text(turn1_answer, mean_v), f"turn-1 answer missing mean {mean_v:.2f}: {turn1_answer[:120]!r}"
assert num_in_text(ANSWER, outside_n, 0), f"turn-2 answer missing outlier count {outside_n}"

assert execute_code_steps(events, 0) >= 1, "turn 1 shows no execute_code step"
assert execute_code_steps(events, 1) >= 1, "turn 2 shows no execute_code step"

# kernel-reuse evidence: when the turn-2 cell source is observable in the
# final metadata, it must reference the persisted variables and must NOT
# re-read the fixture. If sanitization clipped the parameters, the numeric
# assertions above still stand and this check is skipped (documented).
snippets = execute_code_snippets(events, 1)
if snippets:
    joined = "\n".join(snippets)
    assert "series.csv" not in joined, "turn-2 cell re-read the fixture instead of reusing kernel state"
    assert "mean_v" in joined or "std_v" in joined, "turn-2 cell does not reference the persisted variables"
else:
    print("note: turn-2 execute_code source not observable; kernel-reuse evidence skipped")
