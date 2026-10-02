#!/usr/bin/env python3
"""Finite natural-query intent-to-treat comparison, not a claim of method adherence."""
import argparse,json,sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--revision',required=True);args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from app.services.harness_eval.config import EvalSuiteConfig
    from app.services.harness_eval.supervisor import run_suite
    flags={'AGENT_RUNTIME_V2_ENABLED':'1','ARTIFACT_VERSIONING_ENABLED':'1','SKILL_RECOMMENDATION_V2_ENABLED':'1','SKILL_CONTEXT_PROGRESSIVE_ENABLED':'1','CHAT_RUN_SYNTHESIS_RESERVE_SECONDS':'120'}
    variants={arm:{'skills_arm':arm,'target_root':str(repo),'revision':args.revision,'feature_overrides':flags} for arm in ('none','recommended')}
    config=EvalSuiteConfig(entries=['plan-native','plan-external'],repetitions=2,variants=variants,suite_wall_seconds=7200,external_launch_limit=24,token_stop_threshold=2_000_000)
    result=run_suite(Path(args.root).resolve(),config,str(repo/'app/services'))
    print(json.dumps({k:result[k] for k in ('started','total','passed','stop_reason','total_tokens')}))


if __name__=='__main__':main()
