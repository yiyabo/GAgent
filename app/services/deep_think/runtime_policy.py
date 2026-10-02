"""Independent, run-stable controls for bounded runtime experiments."""
from app.services.foundation.settings import get_settings


def configured_policy(settings=None):
    settings = settings if settings is not None else get_settings()
    umbrella = bool(getattr(settings, 'agent_runtime_v2_enabled', False))
    def inherited(name):
        value = getattr(settings, name, None)
        return umbrella if value is None else bool(value)
    return {'version': 1,
            'arguments': inherited('agent_argument_validation_enabled'),
            'schemas': inherited('agent_schema_disclosure_v2_enabled'),
            'receipts': bool(getattr(settings, 'agent_tool_receipt_compaction_enabled', False))}


def policy_for(agent, settings=None):
    policy = getattr(agent, '_runtime_policy', None)
    if policy is None:
        policy = configured_policy(settings)
        agent._runtime_policy = policy
    return dict(policy)


def restore_policy(agent, state):
    saved = state.get('runtime_policy')
    if saved is None:
        # Before independent controls, schema policy 2 and argument validation
        # were enabled by the same umbrella. Never upgrade old checkpoints.
        legacy = state.get('schema_policy', 1) == 2
        saved = {'version': 1, 'arguments': legacy, 'schemas': legacy, 'receipts': False}
    if saved.get('version') != 1 or any(type(saved.get(k)) is not bool for k in ('arguments', 'schemas', 'receipts')):
        raise ValueError('unsupported runtime checkpoint policy')
    agent._runtime_policy = dict(saved)


def restore_disclosure_controls(agent, state):
    for key, attr in [('schema_enabled','enabled'),('schema_force_full','_force_full')]:
        if key in state:
            if type(state[key]) is not bool:raise ValueError('invalid schema checkpoint control')
            setattr(agent._schema_disclosure,attr,state[key])
    cap=state.get('native_repair_cap')
    if cap is not None and (type(cap) is not int or cap!=8192):raise ValueError('invalid repair output allowance')
    agent._native_repair_cap=cap
