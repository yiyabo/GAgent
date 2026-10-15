import json
import time
from types import SimpleNamespace

from app.services.deep_think.observation_progress import observe
from app.services.deep_think.checkpointing import restore_guard
from app.services.deep_think_agent import DeepThinkAgent


def receipt(output, count=1, success=True):
    return {'tool_name': 'execute_code', 'tool_params': {'code': "print(records)"},
            'tool_result_text': json.dumps({'success': True, 'result': {'success': success,
                'result': {'status': 'success' if success else 'error', 'output': output,
                           'kernel': {'execution_count': count}, 'duration_seconds': count / 3}}})}


def test_new_observations_progress_but_replay_timing_and_errors_do_not():
    guard = {}
    assert observe([receipt('Loaded 214 records')], guard)
    assert not observe([receipt('Loaded 214 records', 2)], guard)
    assert not observe([receipt('different error', success=False)], guard)
    assert observe([receipt('Classified first 20 abstracts')], guard)
    restored = {}
    restore_guard(restored, SimpleNamespace(controller_state={'guard': guard}))
    assert not observe([receipt('Classified first 20 abstracts', 30)], restored)


def test_plan_is_not_cut_off_at_step_14_while_it_is_reading_and_computing():
    agent = DeepThinkAgent(llm_client=SimpleNamespace(), available_tools=['execute_code'],
        tool_executor=lambda *_: None, request_profile={'intent_type': 'execute_task', 'current_plan_id': 1})
    agent._plan_agent_completion = True
    guard = {'verified_deliverables': [], 'failure_sig_counts': {}, 'failure_sig_warned': set(),
             'last_progress_iteration': 0, 'no_progress_nudge_sent': False, 'time_nudge_sent': False,
             'started_at': time.monotonic(), 'expected_outputs': [], 'acceptance_spec': None}
    messages = []
    for step in range(1, 22):
        result = agent._apply_loop_guards(messages=messages,
            tool_results=[receipt(f'Read and analyzed batch {step}', step)], iteration=step, guard_state=guard)
        assert result is None
    assert guard['last_progress_iteration'] == 21
    assert guard['verified_deliverables'] == []  # Observations are not fabricated output files.
    for step in range(22, 36):
        result = agent._apply_loop_guards(messages=messages,
            tool_results=[receipt('Read and analyzed batch 21', step)], iteration=step, guard_state=guard)
    assert result and agent._termination_reason == 'no_progress'


def test_progress_never_extends_the_time_budget(monkeypatch):
    from app.services import deep_think_agent as facade
    monkeypatch.setattr(facade, '_time_budget_break_seconds', lambda: 120)
    agent = DeepThinkAgent(llm_client=SimpleNamespace(), available_tools=['execute_code'],
        tool_executor=lambda *_: None, request_profile={'intent_type': 'execute_task'})
    agent._plan_agent_completion = True
    guard = {'verified_deliverables': [], 'failure_sig_counts': {}, 'failure_sig_warned': set(),
             'last_progress_iteration': 0, 'no_progress_nudge_sent': False, 'time_nudge_sent': False,
             'started_at': time.monotonic() - 130, 'expected_outputs': [], 'acceptance_spec': None}
    assert agent._apply_loop_guards(messages=[], tool_results=[receipt('New work')], iteration=14, guard_state=guard)
    assert agent._termination_reason == 'wall_clock_limit'
