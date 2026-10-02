#!/usr/bin/env python3
"""Finite real-model evaluation; production paths run in isolated child processes."""
import argparse,asyncio,json,os,sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root');parser.add_argument('--config');parser.add_argument('--worker')
    parser.add_argument('--module-root');parser.add_argument('--revision')
    parser.add_argument('--profile',choices=['diagnostic','production-like'])
    parser.add_argument('--cases');parser.add_argument('--entry',choices=['chat-native','plan-native','plan-external'])
    parser.add_argument('--repetitions',type=int);parser.add_argument('--timeout',type=float)
    parser.add_argument('--output-max-tokens',type=int);parser.add_argument('--max-iterations',type=int)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    if not (repo/'app').exists():repo=Path.cwd()
    sys.path.insert(0,str(repo))
    if args.module_root:
        import app.services
        app.services.__path__.append(str(Path(args.module_root).resolve()))
    from app.services.harness_eval.config import EvalSuiteConfig
    if args.worker:
        request=Path(args.worker).resolve();data=json.loads(request.read_text());cfg=EvalSuiteConfig(**data['config']).validate()
        from app.services.harness_eval.trial import configure,run_trial
        configure(request.parent,cfg,data['entry'])
        result=asyncio.run(run_trial(data['case'],data['entry'],request.parent,cfg))
        (request.parent/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');return
    if not args.root:parser.error('--root required')
    fields=json.loads(Path(args.config).read_text()) if args.config else {}
    profile=args.profile or fields.get('profile','production-like')
    if profile=='diagnostic':fields={'profile':profile,'trial_wall_seconds':100,'close_reserve_seconds':5,'output_max_tokens':1200,'native_max_iterations':4,'provider_attempt_limit':5,**fields}
    for key,value in [('revision',args.revision),('profile',args.profile),('repetitions',args.repetitions),('trial_wall_seconds',args.timeout),('output_max_tokens',args.output_max_tokens),('native_max_iterations',args.max_iterations)]:
        if value is not None:fields[key]=value
    if args.cases:fields['cases']=args.cases.split(',')
    if args.entry:fields['entries']=[args.entry]
    cfg=EvalSuiteConfig(**fields).validate()
    from app.services.harness_eval.supervisor import run_suite
    report=run_suite(Path(args.root).resolve(),cfg,args.module_root)
    print(json.dumps({k:report[k] for k in ('started','total','passed','stop_reason','total_tokens')},ensure_ascii=False))


if __name__=='__main__':main()
