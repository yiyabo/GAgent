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


def url_in(text):
    return bool(re.search(r"https?://\S+", text))


def keywords_in(text, keywords):
    low = text.lower()
    missing = [k for k in keywords if k.lower() not in low]
    return missing

KEYWORDS = ['2021']
ANY_GROUPS = [('nature', '自然')]

missing = keywords_in(ANSWER, KEYWORDS)
low = ANSWER.lower()
group_missing = [g for g in ANY_GROUPS if not any(k.lower() in low for k in g)]
has_url = url_in(ANSWER)
if not missing and not group_missing and has_url:
    sys.exit(0)
if netfail():
    sys.exit(3)
print(f"missing keywords: {missing}, missing groups: {group_missing}, "
      f"url present: {has_url}", file=sys.stderr)
sys.exit(1)
