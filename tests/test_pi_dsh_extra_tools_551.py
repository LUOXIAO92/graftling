"""Pi and DSH children reach the host's extra tools beside ``agents`` (551).

Each test compares a child with and without extra tools: only the descriptor
variable and the tools served on the same authenticated channel differ. Pi's
extension runs under Node with a recording stand-in; DSH's launch patch is the
real one written by ``prepare_launch``. No model or real Runtime starts.
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import graftling
import graftling.connection
from graftling import AgentsResult
from graftling.dsh import binding, execution
from graftling.dsh.settings import prepare
from graftling.extra_tools import EXTRA_TOOLS_ENV, extra_tool_table
from graftling.member_processes import MEMBER_RUN_ENV
from graftling.pi.execution import PiExecution
from test_agents_server_name import node
from test_dsh_standalone_child import dsh  # noqa: F401 - pytest fixture

SOURCE = Path(graftling.__file__).resolve().parent
DESCRIPTOR = {'name': 'tasks', 'description': 'Host tasks.', 'inputSchema': {'type': 'object'}}
TOOLS = [(DESCRIPTOR, lambda arguments: AgentsResult({'echo': arguments}))]

# Pi: the MCP server registration the extension makes for a child with an owning connection.
PI = r'''
const module = await import(process.argv[1]);
const servers = [];
const pi = { registerFlag() {}, registerCommand() {}, on() {},
             registerMcpServer: (name, options) => servers.push({name, env: options.env}) };
await module.registerAgents(pi).start({hasUI: false, cwd: process.cwd(), sessionManager: {}});
console.log(JSON.stringify(servers));
'''


@pytest.mark.parametrize('declared', [False, True])
def test_pi_extension_forwards_only_the_declared_descriptors(declared: bool) -> None:
    """The child's MCP server gets the connection, plus the descriptors only when the owner declared some."""
    environment = {'GRAFTLING_CONNECTION': '/fake/connection', 'GRAFTLING_PYTHON': '/fake/python'}
    if declared:
        environment[EXTRA_TOOLS_ENV] = json.dumps([DESCRIPTOR])
    found = node(PI, (SOURCE / 'pi/extension.mjs').as_uri(), **environment)
    expected = {'GRAFTLING_CONNECTION': '/fake/connection',
                **({EXTRA_TOOLS_ENV: json.dumps([DESCRIPTOR])} if declared else {})}
    assert found == [{'name': 'graftling', 'env': expected}]


def pi_turn(tmp_path: Path) -> PiExecution:
    return PiExecution({}, 'unused', tmp_path, lambda *_: None, lambda *_: None, tmp_path / 'trace.jsonl', None)


def test_pi_connection_environment_differs_only_by_the_descriptors(tmp_path: Path) -> None:
    """Without extra tools the environment is unchanged; with them it adds exactly their descriptors."""
    turn = pi_turn(tmp_path)
    plain = turn.connection_environment('/fake/address')
    turn.extra_tools = extra_tool_table(TOOLS)
    served = turn.connection_environment('/fake/address')
    assert set(plain) == {'GRAFTLING_CONNECTION', 'GRAFTLING_PYTHON'}
    assert served == {**plain, EXTRA_TOOLS_ENV: json.dumps([DESCRIPTOR])}


def capture_connection(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Record the keyword arguments each agents_connection receives; it yields a fake address."""
    calls: list[dict] = []

    def record(*args, **options):
        calls.append(options)
        return nullcontext('/fake/address')

    monkeypatch.setattr(graftling.connection, 'agents_connection', record)
    monkeypatch.setattr(binding, 'agents_connection', record)
    return calls


def test_pi_channel_serves_the_extra_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The child's authenticated channel carries the same callbacks; none by default."""
    calls = capture_connection(monkeypatch)
    turn = pi_turn(tmp_path)
    turn.agent_call = lambda request: AgentsResult({})
    turn.bind_connection()
    turn.extra_tools = extra_tool_table(TOOLS)
    turn.bind_connection()
    assert calls[0]['extra_tools'] == []
    assert [descriptor for descriptor, _ in calls[1]['extra_tools']] == [DESCRIPTOR]
    assert calls[1]['extra_tools'][0][1]({'a': 1}).document == {'echo': {'a': 1}}


def dsh_patches(settings: dict, tmp_path: Path, extra_tools: list) -> tuple[dict, dict]:
    """The launch environment and the agents-mcp plugin row of a child with ``extra_tools``."""
    turn = execution.DshExecution({**settings, 'mcp': {'command': '/fake/python', 'args': ['-I', '-m', 'graftling.mcp']}},
                                  'unused', tmp_path, lambda *_: None, lambda *_: None, tmp_path / 'trace.jsonl', None)
    turn.extra_tools = extra_tool_table(extra_tools)
    environment = turn.prepare_launch()
    patch = Path(environment['DSH_HOME']) / 'profiles/web/cordis.patch.yml'
    rows = yaml.load(patch.read_text(), Loader=execution._PatchLoader)
    row = next(entry for item in rows for entry in item.get('insert', []) if entry.get('id') == 'agents-mcp')
    return environment, row


def test_dsh_plugin_row_and_environment_differ_only_by_the_descriptors(tmp_path: Path, dsh: dict,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP plugin row also passes the descriptor variable, and DSH's environment holds it, only with extra tools."""
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-551-key')
    settings = prepare(dict(dsh), tmp_path)
    plain_environment, plain_row = dsh_patches(settings, tmp_path / 'plain', [])
    served_environment, served_row = dsh_patches(settings, tmp_path / 'served', TOOLS)
    assert plain_row['config']['mcpEnv'] == ['GRAFTLING_CONNECTION']
    assert served_row['config'] == {**plain_row['config'], 'mcpEnv': ['GRAFTLING_CONNECTION', EXTRA_TOOLS_ENV]}
    assert EXTRA_TOOLS_ENV not in plain_environment
    assert served_environment[EXTRA_TOOLS_ENV] == json.dumps([DESCRIPTOR])
    differing = {key for key in {*plain_environment, *served_environment}
                 if plain_environment.get(key) != served_environment.get(key)}
    # Each launch has its own DSH home and member marker; nothing else may differ.
    assert differing - {EXTRA_TOOLS_ENV} <= {'DSH_HOME', MEMBER_RUN_ENV}


def test_dsh_binding_serves_the_extra_tools_on_its_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """bind_run keeps the tools on the turn and gives the same callbacks to the authenticated channel."""
    calls = capture_connection(monkeypatch)

    class Turn:
        request: dict = {}
        native_header = None
        service = None

    class Events:
        def receive_children(self, receive) -> None:
            pass

    for tools in (None, TOOLS):
        turn = Turn()
        # bind_run reads only these fields of the core's Run.
        run = SimpleNamespace(call=lambda request: AgentsResult({}), settings={}, agent='agent-x', events=Events())
        binding.bind_run(turn, run, extra_tools=tools)
        assert list(turn.extra_tools) == ([] if tools is None else ['tasks'])
        with turn.host_connection(tmp_path) as address:
            assert address == '/fake/address'
    assert calls[0]['extra_tools'] == []
    assert [descriptor for descriptor, _ in calls[1]['extra_tools']] == [DESCRIPTOR]
    assert calls[1]['extra_tools'][0][1]({'a': 1}).document == {'echo': {'a': 1}}
