"""Request accounting and deterministic anchors; estimates are not billing."""
import json
from .context_manager import estimate_tokens,estimate_messages_tokens


class ContextBudgetExceeded(RuntimeError):pass


def breakdown(messages,schemas,output):
    groups={'system':0,'skills':0,'recall':0,'history_tool_results':0,'tool_schemas':estimate_tokens(json.dumps(schemas,ensure_ascii=False)) if schemas else 0,'output_reserve':max(0,output),'provider_framing':128 if schemas or output else 0}
    for m in messages:
        key='system' if m.get('role')=='system' else 'history_tool_results'
        text=str(m.get('content') or '')
        if 'LEARNED PROCEDURES AVAILABLE' in text:key='skills'
        elif 'RECALL' in text:key='recall'
        groups[key]+=estimate_messages_tokens([m])
    return groups


def anchor_text(anchors):return '[Deterministic execution anchors]\n'+json.dumps(anchors,ensure_ascii=False,sort_keys=True,default=str) if anchors else ''
