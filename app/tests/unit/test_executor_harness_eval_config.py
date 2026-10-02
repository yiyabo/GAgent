import pytest
from app.services.harness_eval.config import EvalSuiteConfig
from app.services.harness_eval.fixtures import prepare


def test_frozen_schedule_and_invalid_limits():
    cfg=EvalSuiteConfig().validate()
    assert len(cfg.schedule())==18 and cfg.schedule()==EvalSuiteConfig().schedule()
    assert cfg.fingerprint()!=EvalSuiteConfig(output_max_tokens=1200).fingerprint()
    with pytest.raises(ValueError):EvalSuiteConfig(external_max_session_turns=4).validate()
    assert EvalSuiteConfig.diagnostic().trial_wall_seconds==100


def test_public_fixtures_are_case_specific_and_contain_no_answer(tmp_path):
    prepare('fasta',tmp_path)
    assert {p.name for p in tmp_path.iterdir()}=={'sequences.fasta'}
    assert not (tmp_path/'summary.json').exists()


def test_native_attempt_observer_sees_actual_attempts_and_usage():
    from app.services.execution.llm_observation import observer
    from app.llm import _record_attempt_context
    events=[];handle=observer.set(events.append)
    try:
        _record_attempt_context('call',1);_record_attempt_context('call',1,usage={'total_tokens':8});_record_attempt_context('call',2)
        assert {(e['logical_call_id'],e['attempt_no']) for e in events}=={('call',1),('call',2)}
        assert events[1]['usage']['total_tokens']==8
    finally:observer.reset(handle)


def test_campaign_worker_does_not_inherit_personal_cli_home(tmp_path):
    import json
    import os
    import subprocess
    import sys
    previous = os.environ.get('HOME')
    code = (
        'import json,os; from pathlib import Path; '
        'from app.services.harness_eval.config import EvalSuiteConfig; '
        'from app.services.harness_eval.trial import configure; '
        f'root=Path({str(tmp_path)!r}); '
        'configure(root,EvalSuiteConfig(campaign_root=str(root / "campaign")),"plan-external"); '
        'print(json.dumps({k:os.environ[k] for k in ("HOME","QWEN_RUNTIME_DIR")}))'
    )
    child = json.loads(subprocess.check_output([sys.executable, '-c', code], text=True))
    assert child == {'HOME': str(tmp_path / 'home'), 'QWEN_RUNTIME_DIR': str(tmp_path / 'home/.qwen')}
    assert os.environ.get('HOME') == previous
