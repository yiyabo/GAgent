"""Text response validation, kept separate from transport retry decisions."""
from .llm_observation import emit


class LLMResponseContentError(RuntimeError):
    """The provider answered, but did not deliver usable assistant text."""

    def __init__(self, reason, finish_reason=None):
        self.reason = reason
        self.finish_reason = finish_reason
        self.retryable = False
        super().__init__(f'LLM response has no usable text ({reason}; finish_reason={finish_reason})')


def text_completion(payload, *, logical_call_id, attempt_no):
    choice = None
    if isinstance(payload, dict):
        choices = payload.get('choices')
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
    message = choice.get('message') if choice else None
    content = message.get('content') if isinstance(message, dict) else None
    finish_reason = choice.get('finish_reason') if choice else None
    reason = 'valid' if isinstance(content, str) and content.strip() else (
        'output_truncated' if finish_reason == 'length' else 'missing_assistant_text')
    emit('text_result', logical_call_id=logical_call_id, attempt_no=attempt_no,
         finish_reason=finish_reason, content_status=reason,
         usage=payload.get('usage') if isinstance(payload, dict) else None)
    if reason != 'valid':
        raise LLMResponseContentError(reason, finish_reason)
    return content
