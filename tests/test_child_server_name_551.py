"""A consumer names its children's MCP server; graftling's default stays ``graftling`` (551)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

import graftling
from graftling import AgentControl, AgentsResult, Caller, bind
from graftling.claude.execution import ClaudeExecution
from graftling.codex.driver import CodexDriver
from graftling.codex.managed_session import CodexManagedExecution
from graftling.dsh.execution import DshExecution
from graftling.pi.execution import PiExecution
from test_codex_independent_control import native_settings  # noqa: F401 - pytest fixture

SOURCE = Path(graftling.__file__).resolve().parent
DESCRIPTOR = {'name': 'tasks', 'description': 'Host tasks.', 'inputSchema': {'type': 'object'}}


def test_default_server_name_is_unchanged() -> None:
    """Without a consumer's choice every Runtime keeps graftling's own name."""
    assert {ClaudeExecution.mcp_server, CodexManagedExecution.mcp_server, DshExecution.mcp_server,
            PiExecution.mcp_server} == {'graftling'}


def test_pi_names_its_server_only_when_the_owner_chose_one() -> None:
    """The Pi child's environment names a chosen server; the default adds nothing."""
    chosen = PiExecution.connection_environment(SimpleNamespace(mcp_server='host_name', extra_tools={}), 'addr')
    assert chosen['GRAFTLING_MCP_SERVER'] == 'host_name'
    default = PiExecution.connection_environment(SimpleNamespace(mcp_server='graftling', extra_tools={}), 'addr')
    assert 'GRAFTLING_MCP_SERVER' not in default


def test_pi_extension_reads_the_chosen_server() -> None:
    """The extension registers the owner's name, and refuses a malformed one for its default."""
    executable = shutil.which('node')
    if executable is None:
        pytest.skip('Node is required')
    script = ("const m = await import(process.argv[1]);"
              "console.log(JSON.stringify([m.childServer({GRAFTLING_MCP_SERVER: 'host_name'}),"
              " m.childServer({}), m.childServer({GRAFTLING_MCP_SERVER: 'bad name/'})]));")
    result = subprocess.run([executable, '--input-type=module', '-e', script, str(SOURCE / 'pi/extension.mjs')],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['host_name', 'graftling', 'graftling']


class _Named(CodexManagedExecution):
    """A consumer's Codex execution that names its children's server."""

    mcp_server = 'host_name'

    def __init__(self, *arguments, independent: bool = False, **options) -> None:
        super().__init__(*arguments, **options)


def test_codex_child_server_replaces_a_captured_entry_of_that_name(
    tmp_path: Path, native_settings: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child's entry under the chosen name is the owner's alone, never a captured host server."""
    config = native_settings['adapter_request']['session_parameters']['config']
    config['mcp_servers'] = {'host_name': {'url': 'http://127.0.0.1:9/harness-main', 'env': {'MAIN_ONLY': '1'},
                                           'enabled_tools': ['submit_result']}}
    driver = CodexDriver(native_settings, execution=_Named,
                         extra_tools=lambda run: [(DESCRIPTOR, lambda arguments: AgentsResult({'ok': 1}))])
    control = AgentControl(tmp_path / 'agents', {'codex': driver})
    call = bind(control, Caller('controlled-host', 'actual-parent', 'parent-session', 'codex'))
    try:
        agent = call({'action': 'create', 'runtime': 'codex', 'instruction': 'configuration'}).document['agent']
        done = call({'action': 'wait', 'agent': agent, 'timeout_seconds': 10}).document
        observed = json.loads(done['result']['last_result']['last_message'])['config']['mcp_servers']
        assert set(observed) == {'host_name'}
        entry = observed['host_name']
        assert entry['command'] == sys.executable and entry['args'] == ['-m', 'graftling.mcp']
        assert 'url' not in entry and 'enabled_tools' not in entry and 'MAIN_ONLY' not in entry['env']
        # That entry lists agents and the owner's tools (test_extra_tools_551 runs it).
        assert json.loads(entry['env']['GRAFTLING_EXTRA_TOOLS']) == [DESCRIPTOR]
    finally:
        for owned in control.interrupt_owned():
            call({'action': 'wait', 'agent': owned, 'timeout_seconds': 10})


def test_codex_child_host_tools_need_no_approval(tmp_path: Path, native_settings: dict) -> None:
    """The owner's declared tools carry Codex's per-tool approval_mode approve; agents is unchanged."""
    driver = CodexDriver(native_settings, execution=_Named,
                         extra_tools=lambda run: [(DESCRIPTOR, lambda arguments: AgentsResult({}))])
    control = AgentControl(tmp_path / 'agents', {'codex': driver})
    call = bind(control, Caller('controlled-host', 'actual-parent', 'parent-session', 'codex'))
    try:
        agent = call({'action': 'create', 'runtime': 'codex', 'instruction': 'configuration'}).document['agent']
        done = call({'action': 'wait', 'agent': agent, 'timeout_seconds': 10}).document
        entry = json.loads(done['result']['last_result']['last_message'])['config']['mcp_servers']['host_name']
        assert entry['tools'] == {'tasks': {'approval_mode': 'approve'}}
    finally:
        for owned in control.interrupt_owned():
            call({'action': 'wait', 'agent': owned, 'timeout_seconds': 10})


def _codex_entry(tmp_path: Path, native_settings: dict, **create: object) -> dict:
    """Create one Codex child with the owner's tasks tool and return its server entry."""
    driver = CodexDriver(native_settings, execution=_Named,
                         extra_tools=lambda run: [(DESCRIPTOR, lambda arguments: AgentsResult({}))])
    control = AgentControl(tmp_path / 'agents', {'codex': driver})
    call = bind(control, Caller('controlled-host', 'actual-parent', 'parent-session', 'codex'))
    try:
        created = call({'action': 'create', 'runtime': 'codex', 'instruction': 'configuration', **create})
        assert not created.failed, created.document
        done = call({'action': 'wait', 'agent': created.document['agent'], 'timeout_seconds': 10}).document
        return json.loads(done['result']['last_result']['last_message'])['config']['mcp_servers']['host_name']
    finally:
        for owned in control.interrupt_owned():
            call({'action': 'wait', 'agent': owned, 'timeout_seconds': 10})


def test_codex_disallowed_owner_tool_is_dropped_not_refused(tmp_path: Path, native_settings: dict) -> None:
    """Disallowing the owner's own tool by its native name starts the child without it."""
    entry = _codex_entry(tmp_path, native_settings, disallowed_tools=['mcp__host_name__tasks'])
    assert 'GRAFTLING_EXTRA_TOOLS' not in entry['env'] and 'tools' not in entry
    assert entry['args'] == ['-m', 'graftling.mcp']


def test_codex_tool_choice_keeps_the_owner_server(tmp_path: Path, native_settings: dict) -> None:
    """An available-tools choice never removes the owner's agents/tasks entry."""
    entry = _codex_entry(tmp_path, native_settings, tools=['mcp__host_name__subagents'])
    assert json.loads(entry['env']['GRAFTLING_EXTRA_TOOLS']) == [DESCRIPTOR]
    assert entry['tools'] == {'tasks': {'approval_mode': 'approve'}}
    assert 'enabled_tools' not in entry


def test_codex_unknown_server_is_still_refused() -> None:
    """A server that is neither captured nor the owner's keeps the unavailable refusal."""
    from graftling import AgentsError
    from graftling.codex.driver import apply_tools

    with pytest.raises(AgentsError) as refused:
        apply_tools({}, {'tools': None, 'disallowed_tools': ['mcp__docs__write']}, own_server='host_name')
    assert refused.value.code == 'unavailable'
    config: dict = {}
    apply_tools(config, {'tools': None, 'disallowed_tools': ['mcp__host_name__tasks', 'tasks']},
                own_server='host_name', own_tools=('tasks',))
    assert config == {}
