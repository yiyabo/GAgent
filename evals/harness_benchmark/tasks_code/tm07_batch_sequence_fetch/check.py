import json
import sys
from pathlib import Path

RESULT_DIR = Path(sys.argv[1]).resolve()
RESULT = json.loads((RESULT_DIR / "result.json").read_text(encoding="utf-8"))
ANSWER = str(RESULT.get("final_answer") or "")
ERROR = str(RESULT.get("error") or "")

NET_SIGNALS = [
    "搜索失败", "检索失败", "无法联网", "无法访问", "网络错误", "网络异常",
    "连接超时", "请求超时", "无法获取", "暂时无法", "timed out", "timeout",
    "connection error", "network error", "failed to fetch", "unreachable",
    "rate limit", "429", "502", "503",
]


def netfail():
    blob = (ANSWER + " " + ERROR).lower()
    return any(sig.lower() in blob for sig in NET_SIGNALS)


def num_in_text(text, value, nd=1):
    compact = str(text).replace(",", "")
    target = round(float(value), nd)
    forms = {f"{target:.{nd}f}"}
    if float(target).is_integer():
        forms.add(str(int(target)))
    return any(form in compact for form in forms)


problems = []
used_code = "execute_code" in (RESULT.get("tools_used") or [])
if not used_code:
    problems.append("execute_code not in tools_used")

expected_path = RESULT_DIR / "truth" / "expected.json"
if expected_path.is_file():
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    seqs = expected["sequences"]
    threshold = float(expected["length_threshold"])
    kept = [s for s in seqs if s["length"] > threshold]
    dropped = [s for s in seqs if s["length"] <= threshold]
    for s in seqs:
        if s["id"] not in ANSWER:
            problems.append(f"{s['id']} not mentioned")
        if not num_in_text(ANSWER, s["length"], 0):
            problems.append(f"length {s['length']} for {s['id']} missing")
    mean_kept = sum(s["length"] for s in kept) / len(kept)
    if not num_in_text(ANSWER, mean_kept, 1):
        problems.append(f"kept mean {mean_kept:.1f} missing")
    for s in dropped:
        if s["id"] not in ANSWER:
            problems.append(f"dropped id {s['id']} not mentioned")
else:
    problems.append("truth/expected.json missing in result dir")

if not problems:
    sys.exit(0)
if netfail():
    sys.exit(3)
print("; ".join(problems), file=sys.stderr)
sys.exit(1)
