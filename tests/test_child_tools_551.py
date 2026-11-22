"""A child's tools: the host's by default, narrowed or chosen at create (551)."""
from __future__ import annotations

from pathlib import Path

import pytest

from graftling import AgentControl, AgentsError, Caller, bind
from graftling.claude.execution import ClaudeExecution
from graftling.codex.driver import apply_tools
from graftling.config import read_connections
from graftling.control import tool_selection


def _claude(tmp_path: Path, **request: object) -> ClaudeExecution:
    directory = tmp_path / 'one'
    directory.mkdir(exist_ok=True)
    return ClaudeExecution({'worktree_path': str(tmp_path), 'runtime_home': str(tmp_path / 'home'), **request},
                           'prompt', directory, lambda *_: None, lambda *_: None, directory / 't.jsonl', None)


def test_selection_keeps_the_host_when_nothing_is_chosen() -> None:
    """Nothing chosen is None: the driver adds no tool setting at all."""
    assert tool_selection(None, None, None) is None
    assert tool_selection({}, None, []) is None


def test_selection_create_tools_replace_and_denies_add() -> None:
    """The create's available set replaces the preset's; denies from both apply."""
    preset = {'tools': ['Read', 'Grep'], 'disallowed_tools': ['WebFetch']}
    assert tool_selection(preset, None, None) == {'tools': ['Read', 'Grep'], 'disallowed_tools': ['WebFetch']}
    assert tool_selection(preset, ['Bash'], ['WebSearch', 'WebFetch']) == {
        'tools': ['Bash'], 'disallowed_tools': ['WebFetch', 'WebSearch']}
    with pytest.raises(AgentsError):
        tool_selection(None, ['Read', ''], None)
    with pytest.raises(AgentsError):
        tool_selection(None, 'Read', None)


def test_claude_inherits_host_tools_without_a_choice(tmp_path: Path) -> None:
    """No --tools flag is passed unless the create chose tools: none is hard-coded."""
    assert _claude(tmp_path).tool_arguments() == []
    assert _claude(tmp_path, tools=['Read', 'WebFetch'], disallowed_tools=['Bash']).tool_arguments() == [
        '--tools', 'Read,WebFetch', '--disallowedTools', 'Bash']
    assert _claude(tmp_path, tools=None, disallowed_tools=['WebFetch']).tool_arguments() == [
        '--disallowedTools', 'WebFetch']


def test_codex_narrows_what_it_can_express() -> None:
    """Denying web search or one MCP tool applies directly."""
    config: dict = {'mcp_servers': {'docs': {'command': 'x'}}}
    apply_tools(config, {'tools': None, 'disallowed_tools': ['web_search', 'mcp__docs__write']})
    assert config['web_search'] == 'disabled'
    assert config['mcp_servers']['docs'] == {'command': 'x', 'disabled_tools': ['write']}
    config = {'mcp_servers': {'docs': {'command': 'x'}}}
    apply_tools(config, {'tools': ['mcp__docs__read'], 'disallowed_tools': []})
    assert config['web_search'] == 'disabled'
    assert config['mcp_servers']['docs'] == {'command': 'x', 'enabled_tools': ['read']}


def test_codex_refuses_tools_of_a_server_it_does_not_define() -> None:
    """Codex rejects a transport-less server entry, so nothing is written and the create is refused."""
    for config in ({}, {'mcp_servers': {'other': {'command': 'x'}}}):
        before = {key: dict(value) for key, value in config.items()}
        with pytest.raises(AgentsError) as refused:
            apply_tools(config, {'tools': None, 'disallowed_tools': ['web_search', 'mcp__docs__write']})
        assert refused.value.code == 'unavailable' and 'docs' in refused.value.message
        assert config == before


def test_codex_refuses_what_it_cannot_express_or_authorize() -> None:
    """Built-ins without a native switch, and enabling a host-disabled tool, are refused."""
    with pytest.raises(AgentsError) as refused:
        apply_tools({}, {'tools': None, 'disallowed_tools': ['shell']})
    assert refused.value.code == 'unavailable' and 'shell' in refused.value.message
    config = {'web_search': 'disabled'}
    with pytest.raises(AgentsError) as refused:
        apply_tools(config, {'tools': ['web_search'], 'disallowed_tools': []})
    assert refused.value.code == 'authorization-required'
    assert config == {'web_search': 'disabled'}
    config = {'web_search': 'live'}
    apply_tools(config, {'tools': ['web_search'], 'disallowed_tools': []})
    assert config['web_search'] == 'live'


def test_presets_declare_tools(tmp_path: Path) -> None:
    """config.yml presets may name tools and disallowed_tools as lists."""
    (tmp_path / 'config.yml').write_text(
        'presets:\n  narrow:\n    runtime: claude\n    tools: [Read, Grep]\n    disallowed_tools: [WebFetch]\n')
    assert read_connections(tmp_path)['presets']['narrow'] == {
        'name': 'narrow', 'runtime': 'claude', 'tools': ['Read', 'Grep'], 'disallowed_tools': ['WebFetch']}


class _OldDriver:
    """A driver whose prepare predates the tools keyword."""

    def prepare(self, runtime: str, caller: Caller) -> dict:
        raise AssertionError('must not prepare a child it cannot give the chosen tools')


class _ToolDriver:
    """Record what prepare received, then refuse to start anything."""

    def __init__(self) -> None:
        self.received = None

    def prepare(self, runtime: str, caller: Caller, *, tools: dict | None = None) -> dict:
        self.received = tools
        raise AgentsError('stop-here', 'recorded')


def test_create_passes_tools_or_refuses_a_driver_without_them(tmp_path: Path) -> None:
    """A chosen tool set reaches prepare(tools=); an older driver refuses instead of ignoring it."""
    caller = Caller('host', None, None, 'claude')
    old = bind(AgentControl(tmp_path / 'a', {'claude': _OldDriver()}), caller)
    refused = old({'action': 'create', 'runtime': 'claude', 'instruction': 'x', 'disallowed_tools': ['Bash']})
    assert refused.failed and 'tools' in str(refused.document)
    driver = _ToolDriver()
    new = bind(AgentControl(tmp_path / 'b', {'claude': driver}), caller)
    new({'action': 'create', 'runtime': 'claude', 'instruction': 'x', 'tools': ['Read']})
    assert driver.received == {'tools': ['Read'], 'disallowed_tools': []}
    driver.received = 'untouched'
    new({'action': 'create', 'runtime': 'claude', 'instruction': 'x'})
    assert driver.received is None


def test_claude_reports_tool_entries_it_did_not_apply(tmp_path: Path) -> None:
    """Unknown or unapplied entries are reported after start against system init, never ignored."""
    execution = _claude(tmp_path, tools=['Read', 'NoSuchTool'],
                        disallowed_tools=['WebFetch', 'NoSuchDeny', 'Bash(rm:*)', 'mcp__graftling__x', 'mcp__nosuch__y'])
    offered = ['Read', 'WebFetch', 'mcp__graftling__agents']
    assert execution.tool_check(offered) == {
        'tools_not_offered': ['NoSuchTool'],
        'disallowed_still_offered': ['WebFetch'],
        'disallowed_unconfirmed': ['NoSuchDeny', 'mcp__nosuch__y'],
    }
    assert _claude(tmp_path, tools=['Read'], disallowed_tools=['Bash']).tool_check(['Read']) == {}
    assert _claude(tmp_path).tool_check(['Read']) == {}


def test_tool_check_reaches_the_parent_status(tmp_path: Path) -> None:
    """A reported tool_check is an execution fact that status shows."""
    from graftling.control import AgentControl as Control

    control = Control(tmp_path / 'agents', {})
    record = {'agent': 'agent-x', 'name': 'x', 'runtime': 'claude', 'parent': None, 'creator': {}, 'host': 'h',
              'session': None, 'retired_at': None, 'selection': {}, 'executions': [
                  {'id': 'e1', 'state': 'running', 'requests': [],
                   'process': {'tool_check': {'tools_not_offered': ['NoSuchTool']}}}]}
    assert control._document(record)['tool_check'] == {'tools_not_offered': ['NoSuchTool']}
