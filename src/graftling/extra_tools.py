"""Host-supplied MCP tools served beside ``TOOL_NAME`` to an independent child.

A host passes ``extra_tools``: a sequence of ``(descriptor, callback)`` pairs.
``descriptor`` is an MCP tool document (``name``, ``description``,
``inputSchema``); ``callback(arguments)`` returns an :class:`AgentsResult` and
keeps its own authorization. graftling only lists and routes these tools on the
child's existing authenticated channel; it never interprets them, and the name
``TOOL_NAME`` (``subagents``) stays graftling's own.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping, Sequence

from graftling.tool import TOOL_NAME, AgentsError, AgentsResult

# Set by the owner in the environment of the MCP process it starts for a child
# (``python -m graftling.mcp``): the JSON list of extra tool descriptors.
EXTRA_TOOLS_ENV = 'GRAFTLING_EXTRA_TOOLS'

ExtraTool = tuple[Mapping[str, Any], Callable[[dict], AgentsResult]]


def extra_tool_table(extra_tools: Sequence[ExtraTool] | None) -> dict[str, ExtraTool]:
    """Return the tools by name; a malformed, repeated or ``TOOL_NAME`` entry is a ``ValueError``."""
    table: dict[str, ExtraTool] = {}
    for entry in extra_tools or ():
        descriptor, callback = entry
        name = descriptor.get('name') if isinstance(descriptor, Mapping) else None
        if not isinstance(name, str) or not name or name == TOOL_NAME or name in table or not callable(callback):
            raise ValueError(f'Invalid extra tool {name!r}: it needs a unique name other than {TOOL_NAME} '
                             'and a callback.')
        table[name] = (json.loads(json.dumps(dict(descriptor))), callback)
    return table


def descriptors(table: Mapping[str, ExtraTool]) -> list[dict]:
    """Return the tool documents in the order the host gave them."""
    return [descriptor for descriptor, _ in table.values()]


def descriptor_environment(table: Mapping[str, ExtraTool]) -> dict[str, str]:
    """Return the MCP process environment that lists these tools (empty without any)."""
    return {EXTRA_TOOLS_ENV: json.dumps(descriptors(table), ensure_ascii=False)} if table else {}


def environment_descriptors() -> list[dict]:
    """Return the descriptors the owner declared for this MCP process; none if absent or malformed."""
    try:
        value = json.loads(os.environ.get(EXTRA_TOOLS_ENV) or '[]')
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and isinstance(item.get('name'), str)
            and item['name'] and item['name'] != TOOL_NAME]


def tool_request(name: str, arguments: dict) -> dict:
    """Return the channel request that calls extra tool ``name``."""
    return {'tool': name, 'arguments': arguments}


def requested_tool(request: object) -> tuple[str, object] | None:
    """Return ``(name, arguments)`` of an extra-tool channel request, else ``None``.

    A ``TOOL_NAME`` envelope always carries ``action``, so it never has this shape.
    """
    if isinstance(request, dict) and set(request) == {'tool', 'arguments'} and isinstance(request['tool'], str):
        return request['tool'], request['arguments']
    return None


def call_extra_tool(table: Mapping[str, ExtraTool], name: str, arguments: object) -> AgentsResult:
    """Run the host's callback for ``name`` and return its result.

    An unknown tool, a non-object input or the callback's ``AgentsError`` is a
    failed result, as ``TOOL_NAME`` reports its own refusals.
    """
    try:
        entry = table.get(name)
        if entry is None:
            raise AgentsError('invalid-input', f'Unknown tool: {name}')
        if not isinstance(arguments, dict):
            raise AgentsError('invalid-input', 'Tool arguments must be an object.')
        result = entry[1](arguments)
        if not isinstance(result, AgentsResult):
            raise AgentsError('internal-error', f'Extra tool {name!r} returned no AgentsResult.')
        return result
    except AgentsError as error:
        return AgentsResult({'error': {'code': error.code, 'message': error.message}}, failed=True)
