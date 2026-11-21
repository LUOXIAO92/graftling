"""Host tools served beside ``agents`` to an independent child (551).

Codex runs the controlled stdio peer, which starts the configured
``graftling.mcp`` entry and sends it MCP requests; Claude's in-process SDK
server is driven through its control-request handler. No model runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading

import pytest

from graftling import AgentControl, AgentsResult, Caller, bind
from graftling.claude.execution import ClaudeExecution
from graftling.codex.driver import CodexDriver
from graftling.extra_tools import (EXTRA_TOOLS_ENV, call_extra_tool, environment_descriptors,
                                   extra_tool_table, requested_tool, tool_request)
from test_codex_independent_control import native_settings  # noqa: F401 - pytest fixture

DESCRIPTOR = {'name': 'tasks', 'description': 'Host tasks.', 'inputSchema': {'type': 'object'}}


def test_table_refuses_agents_and_repeats() -> None:
    """``agents`` stays graftling's own name, and each name is served once."""
    callback = lambda arguments: AgentsResult({})
    for tools in ([({'name': 'agents'}, callback)], [(DESCRIPTOR, callback), (DESCRIPTOR, callback)],
                  [({'name': ''}, callback)], [(DESCRIPTOR, None)]):
        with pytest.raises(ValueError):
            extra_tool_table(tools)
    assert extra_tool_table(None) == {}


def test_call_reports_refusals_as_failed_results() -> None:
    """An unknown tool, bad input or the host's own refusal is a failed result, never a crash."""
    from graftling import AgentsError

    def refuse(arguments: dict) -> AgentsResult:
        raise AgentsError('authority-denied', 'not for you')

    table = extra_tool_table([(DESCRIPTOR, refuse)])
    assert call_extra_tool(table, 'tasks', {}).document == {
        'error': {'code': 'authority-denied', 'message': 'not for you'}}
    assert call_extra_tool(table, 'other', {}).failed
    assert call_extra_tool(table, 'tasks', []).failed


def test_agents_envelopes_never_look_like_tool_requests() -> None:
    """Only the exact tagged shape selects an extra tool."""
    assert requested_tool(tool_request('tasks', {'a': 1})) == ('tasks', {'a': 1})
    assert requested_tool({'action': 'discover'}) is None
    assert requested_tool({'tool': 'tasks', 'arguments': {}, 'action': 'x'}) is None


def test_environment_descriptors_ignore_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing or malformed declaration lists nothing, and never re-declares agents."""
    monkeypatch.delenv(EXTRA_TOOLS_ENV, raising=False)
    assert environment_descriptors() == []
    monkeypatch.setenv(EXTRA_TOOLS_ENV, 'not json')
    assert environment_descriptors() == []
    monkeypatch.setenv(EXTRA_TOOLS_ENV, json.dumps([{'name': 'agents'}, DESCRIPTOR, 3]))
    assert environment_descriptors() == [DESCRIPTOR]


def test_codex_child_lists_and_calls_host_tools(tmp_path: Path, native_settings: dict,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """The child's MCP entry lists agents then the host's tool, which runs with that Agent's binding."""
    seen: list[tuple[str, dict]] = []

    def tools(run: object) -> list:
        def tasks(arguments: dict) -> AgentsResult:
            seen.append((run.agent, arguments))
            return AgentsResult({'served_for': run.agent})
        return [(DESCRIPTOR, tasks)]

    control = AgentControl(tmp_path / 'agents', {'codex': CodexDriver(native_settings, extra_tools=tools)})
    call = bind(control, Caller('controlled-host', 'actual-parent', 'parent-session', 'codex'))

    def invoke(action: str, **arguments: object) -> dict:
        result = call({'action': action, **arguments})
        assert not result.failed, result.document
        return result.document

    try:
        monkeypatch.setenv('PEER_MCP_REQUESTS', json.dumps([
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'tasks', 'arguments': {'action': 'discover'}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'agents',
             'arguments': {'action': 'execute', 'feature': 'status', 'arguments': {}}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
             'params': {'name': 'undeclared', 'arguments': {}}},
        ]))
        agent = invoke('create', runtime='codex', instruction='mcp-requests')['agent']
        responses = json.loads(invoke('wait', agent=agent, timeout_seconds=10)['result']['last_result']['last_message'])
        assert [tool['name'] for tool in responses[0]['result']['tools']] == ['agents', 'tasks']
        assert responses[1]['result']['structuredContent'] == {'served_for': agent}
        assert responses[2]['result']['structuredContent']['result']['agents'] == []
        assert responses[3]['error']['code'] == -32602
        assert seen == [(agent, {'action': 'discover'})]
    finally:
        for owned in control.interrupt_owned():
            invoke('wait', agent=owned, timeout_seconds=10)


def test_codex_child_without_host_tools_keeps_agents_only(tmp_path: Path, native_settings: dict,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Without extra tools the child's entry and its environment are as before."""
    control = AgentControl(tmp_path / 'agents', {'codex': CodexDriver(native_settings)})
    call = bind(control, Caller('controlled-host', 'actual-parent', 'parent-session', 'codex'))
    monkeypatch.setenv('PEER_MCP_REQUESTS', json.dumps([{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}]))
    agent = call({'action': 'create', 'runtime': 'codex', 'instruction': 'mcp-requests'}).document['agent']
    done = call({'action': 'wait', 'agent': agent, 'timeout_seconds': 10}).document
    responses = json.loads(done['result']['last_result']['last_message'])
    assert [tool['name'] for tool in responses[0]['result']['tools']] == ['agents']


def _claude(tmp_path: Path) -> tuple[ClaudeExecution, queue.Queue]:
    """Return an unstarted independent Claude owner whose replies are captured."""
    directory = tmp_path / 'agents' / 'sessions' / 'one'
    directory.mkdir(parents=True)
    execution = ClaudeExecution({'worktree_path': str(tmp_path), 'runtime_home': str(tmp_path / 'home')},
                                'prompt', directory, lambda *_: None, lambda *_: None,
                                directory / 'trace.jsonl', None)
    replies: queue.Queue = queue.Queue()
    execution._reply = lambda identity, response: replies.put((identity, response))
    return execution, replies


def _sdk(execution: ClaudeExecution, replies: queue.Queue, message: dict) -> dict:
    """Send one SDK MCP message and return its JSON-RPC response."""
    execution._sdk_message({'request_id': str(message['id']), 'request': {
        'server_name': execution.mcp_server, 'message': message}})
    identity, response = replies.get(timeout=5)
    assert identity == str(message['id'])
    return response['mcp_response']


def test_claude_child_lists_and_calls_host_tools(tmp_path: Path) -> None:
    """Claude's in-process server lists agents then the host's tool and routes its calls."""
    execution, replies = _claude(tmp_path)
    calls: list[dict] = []
    execution.configure_independent(
        archive=None, agents_callback=lambda request: AgentsResult({'agents': True}),
        extra_tools=[(DESCRIPTOR, lambda arguments: (calls.append(arguments), AgentsResult({'ok': 1}))[1])])
    execution.session = 'session'
    listed = _sdk(execution, replies, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert [tool['name'] for tool in listed['result']['tools']] == ['agents', 'tasks']
    called = _sdk(execution, replies, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                       'params': {'name': 'tasks', 'arguments': {'action': 'discover'}}})
    assert called['result']['structuredContent'] == {'ok': 1}
    assert calls == [{'action': 'discover'}]
    unknown = _sdk(execution, replies, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                        'params': {'name': 'undeclared', 'arguments': {}}})
    assert unknown['error']['code'] == -32601


def test_claude_child_without_host_tools_lists_agents_only(tmp_path: Path) -> None:
    """The default independent child keeps the agents-only server."""
    execution, replies = _claude(tmp_path)
    execution.configure_independent(archive=None, agents_callback=lambda request: AgentsResult({}))
    listed = _sdk(execution, replies, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert [tool['name'] for tool in listed['result']['tools']] == ['agents']
