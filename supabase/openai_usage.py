from __future__ import annotations

from typing import Any


def _field(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def response_token_usage(response: Any, *, model: str, operation: str, **context: Any) -> dict[str, Any]:
    """Return a stable, JSON-serializable token usage payload from an OpenAI response."""
    usage = _field(response, 'usage')
    if usage is None and hasattr(response, 'model_dump'):
        usage = (response.model_dump() or {}).get('usage')
    raw_input_tokens = _field(usage, 'input_tokens', _field(usage, 'prompt_tokens'))
    raw_output_tokens = _field(usage, 'output_tokens', _field(usage, 'completion_tokens'))
    raw_total_tokens = _field(usage, 'total_tokens')
    usage_available = any(value is not None for value in (raw_input_tokens, raw_output_tokens, raw_total_tokens))
    input_tokens = int(raw_input_tokens or 0)
    output_tokens = int(raw_output_tokens or 0)
    total_tokens = int(raw_total_tokens if raw_total_tokens is not None else input_tokens + output_tokens)
    input_details = _field(usage, 'input_tokens_details')
    output_details = _field(usage, 'output_tokens_details')

    details: dict[str, Any] = {
        'provider': 'openai',
        'model': model,
        'operation': operation,
        'usage_available': usage_available,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'total_tokens': total_tokens,
    }
    cached_tokens = int(_field(input_details, 'cached_tokens', 0) or 0)
    reasoning_tokens = int(_field(output_details, 'reasoning_tokens', 0) or 0)
    if cached_tokens:
        details['cached_input_tokens'] = cached_tokens
    if reasoning_tokens:
        details['reasoning_tokens'] = reasoning_tokens
    details.update({key: value for key, value in context.items() if value is not None})
    return details
