#!/usr/bin/env bash
set -euo pipefail

# One-click pipeline for our full process (科研模式，不做降级回退)
# Steps: propose → approve → recursive decompose → run (postorder + context + tools + evaluation) → assemble → summary

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TITLE="${TITLE:-因果推断简介}"
GOAL="${GOAL:-写一篇 因果推断简介 报告}"
SECTIONS="${SECTIONS:-8}"
EVAL_MODE="${EVAL_MODE:-llm}"         # llm|multi_expert|adversarial
MAX_ITERS="${MAX_ITERS:-3}"
QUALITY="${QUALITY:-0.8}"
USE_TOOLS="${USE_TOOLS:-true}"
DECOMP_DEPTH="${DECOMP_DEPTH:-3}"

echo "===> Title: $TITLE"
echo "===> Goal : $GOAL"
echo "===> URL  : $BASE_URL"

mkdir -p results workspace

echo "===> 1) Propose plan"
curl -sS -X POST "$BASE_URL/plans/propose" \
  -H 'Content-Type: application/json' \
  -d "{\"goal\":\"$GOAL\",\"title\":\"$TITLE\",\"sections\":$SECTIONS}" \
  -o plan.json
if ! grep -q '"tasks"' plan.json; then
  echo "[ERROR] Propose failed:"; cat plan.json; exit 1
fi

echo "===> 2) Approve (persist tasks; with dedupe)"
code=$(curl -sS -o approve.out -w "%{http_code}" -X POST "$BASE_URL/plans/approve" \
  -H 'Content-Type: application/json' --data-binary @plan.json)
if [ "$code" -lt 200 ] || [ "$code" -ge 300 ]; then
  echo "[ERROR] Approve failed ($code):"; cat approve.out; exit 1
fi

echo "===> 3) Recursive decompose (max_depth=$DECOMP_DEPTH)"
TITLE_ENC=$(python -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=""))' "$TITLE")
curl -sS -X POST "$BASE_URL/plans/$TITLE_ENC/decompose" \
  -H 'Content-Type: application/json' \
  -d "{\"max_depth\": $DECOMP_DEPTH}" >/dev/null

echo "===> 4) Execute (postorder + context + tools=$USE_TOOLS + eval=$EVAL_MODE)"
curl -sS -X POST "$BASE_URL/run" \
  -H 'Content-Type: application/json' \
  --data-binary @- \
  -o run.json <<JSON
{
  "title": "$TITLE",
  "schedule": "postorder",
  "use_context": true,
  "auto_decompose": false,
  "use_tools": $USE_TOOLS,
  "enable_evaluation": true,
  "evaluation_mode": "$EVAL_MODE",
  "evaluation_options": {"max_iterations": $MAX_ITERS, "quality_threshold": $QUALITY},
  "include_summary": true,
  "auto_assemble": true
}
JSON

echo "===> 5) Assemble outputs"
# Prefer assembled from /run response; fallback to endpoint
python - <<'PY'
import json,os,sys
data=json.load(open('run.json','r',encoding='utf-8'))
assembled=None
if isinstance(data,dict):
    assembled=data.get('assembled')
if assembled is None:
    print('[INFO] No assembled in run.json, fetching via endpoint...', flush=True)
    sys.exit(100)
title=assembled.get('title','Report')
combined=assembled.get('combined','')
os.makedirs('results',exist_ok=True)
out=f"results/{title}_assembled.md"
with open(out,'w',encoding='utf-8') as f:
    f.write(f"# {title}\n\n")
    f.write(combined)
print(out)
PY
if [ $? -eq 100 ]; then
  curl -sS "$BASE_URL/plans/$TITLE_ENC/assembled" -o assembled.json
  python - <<'PY'
import json,os
d=json.load(open('assembled.json','r',encoding='utf-8'))
title=d.get('title','Report')
combined=d.get('combined','')
os.makedirs('results',exist_ok=True)
out=f"results/{title}_assembled.md"
with open(out,'w',encoding='utf-8') as f:
    f.write(f"# {title}\n\n")
    f.write(combined)
print(out)
PY
fi

echo "===> 6) Summary"
python - <<'PY'
import json
d=json.load(open('run.json','r',encoding='utf-8'))
if isinstance(d,dict) and 'summary' in d:
    print(json.dumps(d['summary'],ensure_ascii=False,indent=2))
else:
    print('No summary object.')
PY

echo "===> 7) First tasks snapshot"
curl -sS "$BASE_URL/plans/$TITLE_ENC/tasks" | head -c 1500 || true
echo
echo 'Done.'

