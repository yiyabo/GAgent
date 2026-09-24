import json
import re
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


urls = re.findall(r"https?://\S+", ANSWER)
low = ANSWER.lower()
topic_missing = [k for k in ("lysin", "endolysin", "phage") if k not in low]
used_code = "execute_code" in (RESULT.get("tools_used") or [])

problems = []
if len(urls) < 3:
    problems.append(f"only {len(urls)} urls (<3)")
if topic_missing:
    problems.append(f"topic words missing: {topic_missing}")
if not used_code:
    problems.append("execute_code not in tools_used")

if not problems:
    sys.exit(0)
if netfail():
    sys.exit(3)
print("; ".join(problems), file=sys.stderr)
sys.exit(1)
