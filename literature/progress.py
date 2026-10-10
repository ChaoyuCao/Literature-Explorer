"""Structured analysis progress carried alongside ordinary engine messages."""
import json

PREFIX = 'LITERATURE_PROGRESS '


def emit_progress(callback, stage, completed, total, **details):
    if callback:
        callback(PREFIX + json.dumps({'stage': stage, 'completed': completed, 'total': total, **details}))


def parse_progress(message):
    if not message.startswith(PREFIX):
        return None
    try:
        value = json.loads(message[len(PREFIX):])
        if (isinstance(value, dict) and value.get('stage') in ('references', 'citations', 'network', 'resolution')
                and type(value.get('completed')) is int and type(value.get('total')) is int
                and 0 <= value['completed'] <= value['total']):
            return value
    except (ValueError, TypeError):
        pass
    return None
