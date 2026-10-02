#!/usr/bin/env python3
"""Run an explicitly selected, bounded real-model evaluation in an isolated DB.

Never uses business sessions or benchmark files as operational workspaces.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--module-root',help='Optional directory containing the evaluation package for a frozen baseline')
    parser.add_argument('--revision')
    parser.add_argument('--cases',default='table_clean,figure,fasta')
    parser.add_argument('--entry',choices=['chat-native','plan-native'],default='chat-native')
    parser.add_argument('--timeout',type=float,default=120)
    args=parser.parse_args()
    if not 10<=args.timeout<=300:parser.error('timeout must be between 10 and 300 seconds')
    root=Path(args.root).resolve()
    if root.exists() and any(root.iterdir()):parser.error('evaluation root must be new or empty')
    root.mkdir(parents=True,exist_ok=True)
    for name in ('DB_ROOT','APP_RUNTIME_ROOT','APP_INFO_SESSIONS_ROOT','EXECUTION_WORKSPACES_ROOT'):
        os.environ[name]=str(root/name.lower())
    os.environ['DATABASE_URL']='sqlite:///'+str(root/'db_root/main/plan_registry.db')
    os.environ.update({'SKILL_LEARNING_ENABLED':'0','QUALITY_EVALUATION_ENABLED':'0','CODE_MODE_ENABLED':'1','CODE_MODE_CELL_TIMEOUT_SECONDS':'20','LLM_MAX_TOKENS':'1200'})
    inferred=Path(__file__).resolve().parents[1]
    repo_root=inferred if (inferred/'app/services').is_dir() else Path.cwd()
    sys.path.insert(0,str(repo_root))
    if args.module_root:
        import app.services
        app.services.__path__.append(str(Path(args.module_root).resolve()))
    from app.database import init_db
    from app.services.harness_eval.corpus import CASES
    from app.services.harness_eval.runner import run_case
    cases=args.cases.split(',')
    if len(cases)>6 or any(case not in CASES for case in cases):parser.error('select at most six known cases')
    init_db()
    from app.repository.llm_usage import init_llm_usage_table
    init_llm_usage_table()
    async def run():
        results=[]
        for case in cases:
            result=await run_case(case,root/case,entry=args.entry,timeout=args.timeout)
            results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
        return results
    results=asyncio.run(run())
    revision=args.revision
    if not revision:
        try:revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:revision='unavailable'
    record={'revision':revision,'entry':args.entry,'results':results,'passed':sum(row['passed'] for row in results),'total':len(results),
            'note':'Small real-model pilot. Structural/content oracles do not prove scientific validity; no dollar pricing assumed.'}
    (root/'report.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
