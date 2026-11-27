"""Pi and DSH children call the owner's declared tools (e.g. tasks) without an approval ask, like agents (551).

User decision: a child's tasks calls are allowed outright unless explicitly disallowed. Without a reviewer
neither Runtime asks for MCP calls natively (DSH's pre-execute default is allow; Pi has no permission system);
the review hooks graftling adds are where an ask would come from, so they exempt the owner's tools. A
disallowed_tools entry naming such a tool stops it being offered. Review of every other tool is unchanged.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import graftling
from graftling import AgentsError, AgentsResult
from graftling.dsh import binding, execution
from graftling.dsh.settings import dsh_package, prepare
from graftling.extra_tools import EXTRA_TOOLS_ENV
from graftling.pi.execution import tool_arguments
from test_agents_server_name import DSH, node
from test_dsh_standalone_child import dsh  # noqa: F401 - pytest fixture

SOURCE = Path(graftling.__file__).resolve().parent
TASKS = {'name': 'tasks', 'description': 'Host tasks.', 'inputSchema': {'type': 'object'}}
CALLS = ['mcp__graphtraj__tasks', 'mcp__graphtraj__subagents', 'mcp__other__tasks', 'Bash']

# Pi: which tool calls the review hook turns into an ask (the confirm answers deny).
PI = r'''
const module = await import(process.argv[1]);
const handlers = [], asked = [];
const pi = { registerFlag() {}, registerCommand() {}, registerMcpServer() {},
             on: (event, handler) => { if (event === 'tool_call') handlers.push(handler); } };
module.registerReview(pi, process.env, 'graphtraj');
const ctx = { ui: { confirm: async (title, call) => { asked.push(JSON.parse(call).tool); return false; } } };
for (const toolName of JSON.parse(process.argv[2])) {
  for (const handler of handlers) await handler({ toolName, toolCallId: toolName, input: {} }, ctx);
}
console.log(JSON.stringify(asked));
'''


@pytest.mark.parametrize(('declared', 'asked'), [
    (True, ['mcp__other__tasks', 'Bash']),
    (False, ['mcp__graphtraj__tasks', 'mcp__other__tasks', 'Bash']),
])
def test_pi_review_lets_the_owners_declared_tools_through(declared: bool, asked: list) -> None:
    """Only the owner's tools on its own server skip review; without a declaration nothing changes."""
    environment = {'GRAFTLING_PI_REVIEW': 'review-title',
                   **({EXTRA_TOOLS_ENV: json.dumps([TASKS])} if declared else {})}
    assert node(PI, (SOURCE / 'pi/extension.mjs').as_uri(), json.dumps(CALLS), **environment) == asked


@pytest.mark.parametrize(('config', 'asked'), [
    ({'mcpServer': 'graphtraj', 'ownerTools': ['tasks']}, ['mcp__other__tasks', 'Bash']),
    ({'mcpServer': 'graphtraj'}, ['mcp__graphtraj__tasks', 'mcp__other__tasks', 'Bash']),
])
def test_dsh_review_row_lets_the_owners_declared_tools_through(tmp_path: Path, config: dict, asked: list) -> None:
    """DSH's own decision is allow; the review row asks for everything but the owner's entries."""
    found = node(DSH, (SOURCE / 'dsh/review.mjs').as_uri(),
                 json.dumps({**config, 'readyFile': str(tmp_path / 'ready')}), json.dumps(CALLS))
    assert found == asked


def review_row(settings: dict, tmp_path: Path, extra: dict) -> dict:
    turn = execution.DshExecution({**settings, 'review': {'name': 'r'},
                                   'mcp': {'command': '/fake/python', 'args': ['-I', '-m', 'graftling.mcp']}},
                                  'unused', tmp_path, lambda *_: None, lambda *_: None, tmp_path / 'trace.jsonl', None)
    turn.extra_tools = extra
    import yaml
    patch = Path(turn.prepare_launch()['DSH_HOME']) / 'profiles/web/cordis.patch.yml'
    rows = yaml.load(patch.read_text(), Loader=execution._PatchLoader)
    return next(entry for item in rows for entry in item.get('insert', []) if entry.get('id') == 'agents-review')


def test_dsh_review_row_names_the_owner_tools_only_when_declared(tmp_path: Path, dsh: dict,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-551-key')
    settings = prepare(dict(dsh), tmp_path)
    plain = review_row(settings, tmp_path / 'plain', {})
    served = review_row(settings, tmp_path / 'served', {'tasks': (TASKS, lambda arguments: AgentsResult({}))})
    assert 'ownerTools' not in plain['config']
    assert served['config'] == {**plain['config'], 'readyFile': served['config']['readyFile'], 'ownerTools': ['tasks']}


def bind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied: list) -> SimpleNamespace:
    monkeypatch.setattr(binding, 'agents_connection', lambda *args, **options: nullcontext('/fake/address'))
    turn = SimpleNamespace(request={'disallowed_tools': denied}, native_header=None, service=None, mcp_server='graphtraj')
    run = SimpleNamespace(call=lambda request: AgentsResult({}), settings={}, agent='agent-x',
                          events=SimpleNamespace(receive_children=lambda receive: None))
    binding.bind_run(turn, run, extra_tools=[(TASKS, lambda arguments: AgentsResult({}))])
    return turn


def test_dsh_disallowed_owner_tool_is_not_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert list(bind(tmp_path, monkeypatch, []).extra_tools) == ['tasks']
    assert list(bind(tmp_path, monkeypatch, ['mcp__graphtraj__tasks', 'tool-web']).extra_tools) == []
    with pytest.raises(AgentsError) as refused:
        bind(tmp_path, monkeypatch, ['mcp__graphtraj__subagents'])
    assert refused.value.code == 'unavailable'


def test_dsh_apply_tools_keeps_an_mcp_deny_for_the_binding() -> None:
    import shutil
    executable = shutil.which('dsh')
    if executable is None:
        pytest.skip('Installed DSH is required for its own preset.')
    request = {'package': str(dsh_package(executable))}
    execution.apply_tools(request, {'tools': None, 'disallowed_tools': ['mcp__graphtraj__tasks']})
    assert request['disallowed_tools'] == ['mcp__graphtraj__tasks']


def test_pi_disallowed_owner_tool_is_excluded_natively(tmp_path: Path) -> None:
    """Pi's own --exclude-tools removes MCP tools too, so a denied tasks is not offered."""
    assert tool_arguments({'disallowed_tools': ['mcp__graphtraj__tasks']}, tmp_path) == (
        ['--exclude-tools', 'mcp__graphtraj__tasks'], [])


OWNED = ['mcp__graphtraj__subagents', 'mcp__graphtraj__tasks']


def test_pi_available_set_keeps_the_owners_entries(tmp_path: Path) -> None:
    """Main's AU pi run: `--tools read,grep` declared no MCP tool. The owner's entries are always named."""
    assert tool_arguments({'tools': ['read', 'grep']}, tmp_path, OWNED)[0] == [
        '--tools', 'read,grep,mcp__graphtraj__subagents,mcp__graphtraj__tasks']
    argv, _ = tool_arguments({'tools': ['read', 'grep'], 'disallowed_tools': ['mcp__graphtraj__tasks']}, tmp_path, OWNED)
    assert argv == ['--tools', 'read,grep,mcp__graphtraj__subagents', '--exclude-tools', 'mcp__graphtraj__tasks']
    # +/- entries change Pi's defaults and keep MCP tools; plain and +/- cannot be mixed, so nothing is added.
    assert tool_arguments({'tools': ['+grep']}, tmp_path, OWNED)[0] == ['--tools', '+grep']
    assert tool_arguments({}, tmp_path, OWNED)[0] == []


def test_pi_launch_names_the_owner_entries_in_its_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The launch passes the child's server name and declared extra tools to tool_arguments."""
    from graftling.pi import execution as pi_execution

    # prepare_launch needs a full Pi install to run; the call site's argument is checked in its source.
    import inspect

    source = inspect.getsource(pi_execution.PiExecution.prepare_launch)
    assert "f'mcp__{self.mcp_server}__{TOOL_NAME}'" in source
    assert "f'mcp__{self.mcp_server}__{name}' for name in self.extra_tools" in source
    assert 'tool_arguments(request, source, owned)' in source


def test_dsh_tool_rows_never_touch_the_owner_mcp_row(tmp_path: Path, dsh: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `tools` row selection disables preset tool rows only; the agents-mcp row serving agents/tasks stays."""
    import shutil
    import yaml

    installed = shutil.which('dsh')
    if installed is None:
        pytest.skip('Installed DSH is required for its own preset.')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-551-key')
    settings = prepare({**dsh, 'executable': installed}, tmp_path)
    execution.apply_tools(settings, {'tools': ['tool-fs'], 'disallowed_tools': []})
    turn = execution.DshExecution({**settings, 'mcp': {'command': '/fake/python', 'args': ['-I', '-m', 'graftling.mcp']}},
                                  'unused', tmp_path, lambda *_: None, lambda *_: None, tmp_path / 'trace.jsonl', None)
    turn.extra_tools = {'tasks': (TASKS, lambda arguments: AgentsResult({}))}
    patch = Path(turn.prepare_launch()['DSH_HOME']) / 'profiles/web/cordis.patch.yml'
    rows = yaml.load(patch.read_text(), Loader=execution._PatchLoader)
    mcp = [entry for item in rows for entry in item.get('insert', []) if entry.get('id') == 'agents-mcp']
    assert len(mcp) == 1 and 'disabled' not in mcp[0]
    preset = next(item for item in rows if item.get('id') == 'preset-standard')
    disabled = {row['id'] for row in execution._tool_rows(preset['config']['plugins']) if row.get('disabled') is True}
    assert 'tool-fs' not in disabled and 'tool-web' in disabled
    assert not any(row.get('id') == 'agents-mcp' for row in execution._tool_rows(preset['config']['plugins']))
