"""Normalize declarations at task persistence; undeclared success stays reviewable."""
from __future__ import annotations

from copy import deepcopy
from functools import wraps

from app.services.foundation.settings import get_settings
from .output_spec import output_spec_from_metadata


def seed_metadata(raw):
    metadata = deepcopy(raw['metadata']) if isinstance(raw.get('metadata'), dict) else {}
    for key in ('required_outputs', 'output_spec', 'artifact_contract', 'acceptance_criteria', 'delivery_mode'):
        if key in raw and key not in metadata:
            metadata[key] = deepcopy(raw[key])
    return metadata


def normalize(metadata, *, task_id, parent_id, new_definition=False):
    result = deepcopy(metadata or {})
    if new_definition and parent_id is not None and getattr(get_settings(), 'plan_output_contracts_required', False):
        result['delivery_contract_required'] = True
    spec = output_spec_from_metadata(result)
    if spec is None:
        return result
    if result.get('delivery_contract_required') and spec.authoritative:
        from .artifact_contracts import dynamic_artifact_alias
        contract = deepcopy(result.get('artifact_contract') or spec.artifact_contract or {})
        if not contract.get('publishes') or contract.get('source') in {'inferred_text', 'declared_outputs'}:
            aliases = [dynamic_artifact_alias('task_'+str(task_id), item.target_path)
                       for item in spec.required_outputs if item.target_path]
            aliases = list(dict.fromkeys(alias for alias in aliases if alias))
            if aliases or contract.get('source') == 'declared_outputs':
                # Only paths explicitly declared by the planner/user become
                # required publications. Never promote inferred input edges.
                if contract.get('source') == 'inferred_text':
                    contract = {'requires': []}
                contract.update(publishes=aliases, source='declared_outputs')
                result['artifact_contract'] = contract
                spec.artifact_contract = deepcopy(contract)
    # Persist only a supplied/declared envelope. Merely reading legacy metadata
    # must not fabricate a new contract or change its inference provenance.
    if any(k in result for k in ('required_outputs', 'output_spec', 'v2_spec', 'acceptance_spec')):
        result['output_spec'] = spec.to_dict()
    return result


def has_declared_acceptance(metadata):
    spec = output_spec_from_metadata(metadata)
    if spec and spec.authoritative and spec.required_outputs:
        return True
    contract = (metadata or {}).get('artifact_contract') or (spec.artifact_contract if spec else None) or {}
    if (contract.get('publishes') and contract.get('source') != 'inferred_text'
            and (metadata or {}).get('artifact_contract_source') != 'inferred_text'):
        return True
    criteria = (metadata or {}).get('acceptance_criteria') or (spec.acceptance_criteria if spec else None) or {}
    return bool(criteria.get('checks') and criteria.get('source') != 'inferred_text'
                and (metadata or {}).get('acceptance_criteria_source') != 'inferred_text')


def require_declared_delivery(function):
    @wraps(function)
    def wrapped(service, node, *args, **kwargs):
        finalization = function(service, node, *args, **kwargs)
        node_metadata = node.metadata or {}
        if not node_metadata.get('delivery_contract_required') or finalization.final_status not in {'completed','success','succeeded','done'}:
            return finalization
        metadata = finalization.payload.setdefault('metadata', {})
        if service.is_manual_acceptance_active(metadata) or has_declared_acceptance(node_metadata):
            return finalization
        finalization.final_status = 'needs_review'
        finalization.payload['status'] = 'needs_review'
        verification = {**(finalization.verification or {}), 'status': 'unchecked', 'authoritative': False}
        metadata.update(verification_status='unchecked', verification=verification,
            delivery_verification={'status': 'needs_review', 'reason': 'missing_declared_acceptance',
                                   'message': 'Execution finished, but no declared output or content check establishes completion.'})
        finalization.verification = verification
        return finalization
    return wrapped
