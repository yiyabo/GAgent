#!/usr/bin/env python3
"""Predeclared finite A/B blocks; all outcomes and incomplete rows are retained."""
import argparse,json,sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True);parser.add_argument('--baseline-root',required=True);parser.add_argument('--candidate-root',required=True)
    parser.add_argument('--baseline-revision',required=True);parser.add_argument('--candidate-revision',required=True)
    args=parser.parse_args();repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from app.services.harness_eval.config import EvalSuiteConfig
    from app.services.harness_eval.supervisor import run_suite
    flags={'AGENT_RUNTIME_V2_ENABLED':'1','ARTIFACT_VERSIONING_ENABLED':'1','SKILL_RECOMMENDATION_V2_ENABLED':'1','SKILL_CONTEXT_PROGRESSIVE_ENABLED':'1','CHAT_RUN_SYNTHESIS_RESERVE_SECONDS':'120'}
    variants={'baseline':{'target_root':args.baseline_root,'revision':args.baseline_revision,'feature_overrides':{}},'candidate':{'target_root':args.candidate_root,'revision':args.candidate_revision,'feature_overrides':flags}}
    groups=[['table_clean','figure','fasta'],['literature_report','correction','skill_reuse']]
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True);blocks=[]
    for repetition in range(3):
        for half,cases in enumerate(groups):
            config=EvalSuiteConfig(cases=cases,variants=variants,order_seed=42+repetition)
            result=run_suite(root/f'block-{repetition}-{half}',config,str(repo/'app/services'))
            blocks.append({'repetition':repetition,'half':half,'report':str(root/f'block-{repetition}-{half}'/'report.json'),'started':result['started'],'total':result['total'],'stop_reason':result['stop_reason']})
            print(json.dumps(blocks[-1]),flush=True)
            (root/'matrix.json').write_text(json.dumps({'baseline':args.baseline_revision,'candidate':args.candidate_revision,'declared_trials':108,'blocks':blocks},indent=2)+'\n')


if __name__=='__main__':main()
