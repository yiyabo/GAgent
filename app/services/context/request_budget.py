"""Request accounting and deterministic anchors; estimates are not billing."""
import json
from .context_manager import estimate_tokens,estimate_messages_tokens


class ContextBudgetExceeded(RuntimeError):pass


def breakdown(messages,schemas,output,components=None):
    groups={'system':0,'skills':0,'recall':0,'history_tool_results':0,'tool_schemas':estimate_tokens(json.dumps(schemas,ensure_ascii=False)) if schemas else 0,'output_reserve':max(0,output),'provider_framing':128 if schemas or output else 0}
    for m in messages:
        key='system' if m.get('role')=='system' else 'history_tool_results'
        groups[key]+=estimate_messages_tokens([m])
    for name,fragment in (components or {}).items():
        if not fragment:continue
        for m in messages:
            if fragment in str(m.get('content') or ''):
                source='system' if m.get('role')=='system' else 'history_tool_results'
                amount=min(groups[source],estimate_tokens(fragment));groups[source]-=amount;groups[name]=groups.get(name,0)+amount
                break
    return groups


def anchor_text(anchors):return '[Deterministic execution anchors]\n'+json.dumps(anchors,ensure_ascii=False,sort_keys=True,default=str) if anchors else ''
