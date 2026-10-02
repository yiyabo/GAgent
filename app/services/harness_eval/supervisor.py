"""Finite process supervisor; private oracles run only after a worker exits."""
import hashlib,json,os,signal,subprocess,sys,time
from dataclasses import replace
from pathlib import Path
from .config import EvalSuiteConfig
from .oracles import check,ORACLE_VERSION


def descendants(pid):
    out={}
    try:
        for child in Path(f'/proc/{pid}/task/{pid}/children').read_text().split():
            child=int(child);out[child]=Path(f'/proc/{child}/stat').read_text().split(') ',1)[1].split()[19];out.update(descendants(child))
    except (OSError,ValueError):pass
    return out


def stop(process,children):
    process.terminate()
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    for pid,start in children.items():
        try:
            if Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]!=start:continue
            if os.getpgid(pid)==pid:os.killpg(pid,signal.SIGKILL)
            else:os.kill(pid,signal.SIGKILL)
        except (OSError,ValueError):pass


def run_suite(root:Path,cfg:EvalSuiteConfig,module_root=None):
    cfg.validate();root.mkdir(parents=True,exist_ok=True)
    snapshot=root/'config.json';payload=cfg.to_dict();fingerprint=cfg.fingerprint()
    if snapshot.exists() and json.loads(snapshot.read_text())!=payload:raise ValueError('suite configuration changed')
    snapshot.write_text(json.dumps(payload,indent=2)+'\n')
    journal=root/'trials.jsonl';rows=[json.loads(x) for x in journal.read_text().splitlines()] if journal.exists() else []
    done={r['trial_id'] for r in rows};started=time.monotonic();tokens=sum(r.get('total_tokens',0) for r in rows);launches=sum(r.get('external_launches',0) for r in rows);reason=None
    for i,item in enumerate(cfg.schedule()):
        identity=f'{i:03d}-{item["case"]}-{item["entry"]}-{item["repetition"]}-{item.get("variant","single")}'
        if identity in done:continue
        if time.monotonic()-started>=cfg.suite_wall_seconds:reason='suite_wall_limit';break
        if tokens>=cfg.token_stop_threshold:reason='token_stop_before_next_trial';break
        if item['entry']=='plan-external' and launches>=cfg.external_launch_limit:reason='external_launch_limit';break
        trial=root/identity
        if trial.exists():
            # Never silently re-run an interrupted trial with possible effects.
            row={**item,'trial_id':identity,'error':'interrupted_trial_not_reexecuted','passed':False}
        else:
            trial.mkdir();request=trial/'request.json'
            effective=replace(cfg,**cfg.variants.get(item.get('variant'),{})).validate()
            request.write_text(json.dumps({**item,'config':effective.to_dict(),'external_remaining':cfg.external_launch_limit-launches}))
            args=[sys.executable,str(Path(__file__).resolve().parents[3]/'scripts/run_harness_workflow_eval.py'),'--worker',str(request)]
            if module_root:args+=['--module-root',module_root]
            if effective.target_root:args+=['--target-root',effective.target_root]
            with (trial/'worker.log').open('w') as log:
                trial_started=time.monotonic()
                proc=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                children={}
                while proc.poll() is None:
                    children.update(descendants(proc.pid))
                    if time.monotonic()-started>=cfg.suite_wall_seconds or time.monotonic()-trial_started>cfg.trial_wall_seconds+15:
                        stop(proc,children);break
                    time.sleep(.5)
            result=trial/'result.json'
            row=json.loads(result.read_text()) if result.exists() else {**item,'production_status':'failed','error':'worker_stopped_without_report','artifacts':[]}
            delivered=trial/'oracle-input';delivered.mkdir()
            for artifact in row.get('artifacts',[]):
                path=Path(artifact['path'])
                if path.is_file() and trial in path.resolve().parents:
                    import shutil
                    shutil.copy2(path,delivered/artifact['name'])
            row['oracle']=check(item['case'],delivered)
            row['manual_review_required']=row['oracle']['manual_review_required'];row['manual_review_status']='pending' if row['manual_review_required'] else 'not_required'
            row['oracle_passed']=row['oracle']['passed'];row['passed']=bool(row['oracle_passed'] and row.get('answer_completion_passed') and not row.get('error'))
            row.update(item,trial_id=identity,config_hash=fingerprint)
        tokens+=row.get('total_tokens',0);launches+=row.get('external_launches',0);rows.append(row)
        with journal.open('a') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
    report={'schema_version':2,'config':payload,'config_hash':fingerprint,'oracle_version':ORACLE_VERSION,'results':rows,'total':len(cfg.schedule()),'started':len(rows),'passed':sum(r.get('passed',False) for r in rows),'stop_reason':reason,'token_limit_enforcement':'stop_before_next_trial','total_tokens':tokens,'cost_usd':None}
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report
