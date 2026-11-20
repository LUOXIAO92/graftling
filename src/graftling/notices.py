"""Which ended rounds of a child Agent its direct parent must handle.

One rule for every host and every parent level. Every other end is kept in
the child's records (status, reports, Trace) without notifying the parent.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

# Notice events that announce the end of one execution.
END_EVENTS = frozenset({'completed', 'interrupted', 'failed', 'runtime-error'})


def needs_parent(
    outcome: str,
    *,
    submitted: bool = False,
    pending_requests: int = 0,
    budget: bool = False,
    active_members: int = 0,
) -> bool:
    """Whether the end of a child's round must notify its direct parent.

    ``submitted`` is a report or result asking for the parent, ``budget`` a
    budget shortage, timeout or budget stop, and ``active_members`` the
    members under the child still executing or registered to start. A round
    whose child keeps no such member is the stall guard: the parent is the
    only one left to continue, so a leaf child's end always notifies.
    """
    return (submitted or pending_requests > 0 or budget or outcome != 'completed'
            or active_members == 0)


def notice_id(execution: object, event: object, request: object = None) -> str | None:
    """Return the stable identity of one execution's notice, if it has one.

    A notice announcing a native request also names that request, so two
    requests of one execution are two notices.
    """
    if isinstance(execution, str) and execution and isinstance(event, str) and event:
        return f'{execution}:{event}' if request is None else f'{execution}:{event}:{request}'
    return None


# What a Main needs to act on a structured notice, in this order; every other
# field stays in the notice record and the child's status, not in the text.
NOTICE_FACTS = ('execution_id', 'outcome', 'request_id', 'request_token', 'method', 'report', 'key')


def notice_text(event: Mapping[str, Any]) -> str:
    """Return the Markdown a Main reads for one child notice, the same on every Runtime.

    ``event`` is the queued notice event: ``alias``, ``event``, ``message`` and
    optionally ``execution`` and ``request``. The first line is the title
    ``[Graftling notice from <alias> · <event>[ · execution <id>]]``. The content
    follows as Markdown: a plain message verbatim; a message that is a label
    line ending in ``:`` and one JSON object (an end or a native request) gives
    its ``last_agent_message`` and ``error`` verbatim, otherwise the label and
    any ``params`` as a JSON block. A last line ``Facts: …`` names only what the
    Main needs to act: execution, outcome, request ID and token, method, report,
    budget key, error code.
    """
    alias = event.get('alias') or 'graftling'
    execution = event.get('execution')
    title = (f"[Graftling notice from {alias} · {event.get('event') or 'event'}"
             f"{f' · execution {execution}' if execution else ''}]")
    message = event.get('message') if isinstance(event.get('message'), str) else ''
    content, facts = message.strip(), []
    label, _, rest = message.partition(':\n')
    try:
        details = json.loads(rest) if rest.lstrip().startswith('{') and '\n' not in label else None
    except ValueError:
        details = None
    if isinstance(details, dict):
        parts = []
        if isinstance(details.get('last_agent_message'), str) and details['last_agent_message'].strip():
            parts.append(details['last_agent_message'].strip())
        error = details.get('error')
        error_text = error.get('message') if isinstance(error, dict) else error
        if isinstance(error_text, str) and error_text:
            parts.append(f'**Error:** {error_text}')
        if not parts:
            parts.append(label.strip())
            if 'params' in details:
                parts.append('```json\n' + json.dumps(details['params'], ensure_ascii=False, indent=2) + '\n```')
        content = '\n\n'.join(parts)
        facts = [f'{key} `{details[key]}`' for key in NOTICE_FACTS
                 if isinstance(details.get(key), (str, int, float)) and not isinstance(details.get(key), bool)]
        if isinstance(error, dict) and isinstance(error.get('code'), str):
            facts.append(f"error code `{error['code']}`")
    elif event.get('request') is not None:
        facts.append(f"request `{event['request']}`")
    return '\n\n'.join(part for part in (title, content, 'Facts: ' + ' · '.join(facts) if facts else '') if part)
