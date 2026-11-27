"""Pi and DSH children keep the host's tools unless create chooses them (551).

Pi: ``tools``/``disallowed_tools`` become Pi's own ``--tools``/``--exclude-tools``;
a built-in the host's settings do not enable is asked of the parent on first use
through Pi's confirm dialog (the extension runs under Node with a recording
stand-in). DSH: the choice disables tool rows of the installed agent preset; an
unknown row is ``unavailable`` and a row the installed preset disables is
``authorization-required``. No model or real Runtime starts.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest
import yaml

import graftling
from graftling import AgentsError, Caller
from graftling.dsh import execution as dsh_execution
from graftling.dsh.settings import dsh_package
from graftling.pi.driver import PiDriver, apply_tools as pi_apply_tools
from graftling.pi.execution import CONFIRM_TITLE, host_default_tools, tool_arguments
from test_agents_server_name import node

SOURCE = Path(graftling.__file__).resolve().parent
CALLER = Caller('test-host', None, 'main-session', 'claude')


def agent_dir(tmp_path: Path, settings: dict | None) -> Path:
    directory = tmp_path / 'agent'
    directory.mkdir()
    if settings is not None:
        (directory / 'settings.json').write_text(json.dumps(settings))
    return directory


@pytest.mark.parametrize(('settings', 'expected'), [
    (None, {'read', 'bash', 'edit', 'write'}),
    ({'defaultTools': ['+grep', '-write']}, {'read', 'bash', 'edit', 'grep'}),
    ({'defaultTools': ['read', 'ls']}, {'read', 'ls'}),
    ({'defaultTools': 'read'}, {'read', 'bash', 'edit', 'write'}),
])
def test_pi_host_default_tools_follow_its_settings(tmp_path: Path, settings: dict | None, expected: set) -> None:
    assert host_default_tools(agent_dir(tmp_path, settings)) == expected


def test_pi_without_a_choice_passes_no_flag(tmp_path: Path) -> None:
    """Omitted: Pi keeps the host's own settings; nothing is asked."""
    directory = agent_dir(tmp_path, None)
    assert tool_arguments({}, directory) == ([], [])
    assert tool_arguments({'tools': None, 'disallowed_tools': []}, directory) == ([], [])


def test_pi_choice_maps_to_native_flags_and_names_what_is_beyond_the_host(tmp_path: Path) -> None:
    """Narrowing passes freely; a built-in the host does not enable is returned for the parent's allow."""
    directory = agent_dir(tmp_path, {'defaultTools': ['+ls']})
    assert tool_arguments({'tools': ['read', 'ls'], 'disallowed_tools': ['bash']}, directory) == (
        ['--tools', 'read,ls', '--exclude-tools', 'bash'], [])
    assert tool_arguments({'tools': ['read', 'grep', 'mcp__graftling__*', 'custom'], 'disallowed_tools': []},
                          directory) == (['--tools', 'read,grep,mcp__graftling__*,custom'], ['grep'])
    assert tool_arguments({'tools': ['+grep', '-write']}, directory) == (['--tools', '+grep,-write'], ['grep'])
    assert tool_arguments({'disallowed_tools': ['grep']}, directory) == (['--exclude-tools', 'grep'], [])


def test_pi_driver_keeps_the_choice_only_when_given(tmp_path: Path) -> None:
    request = {'executable': '/x/pi', 'worktree_path': str(tmp_path), 'agent_dir': str(agent_dir(tmp_path, None))}
    driver = PiDriver(request)
    assert 'tools' not in driver.prepare('pi', CALLER)['adapter_request']
    chosen = driver.prepare('pi', CALLER, tools={'tools': ['read'], 'disallowed_tools': ['bash']})['adapter_request']
    assert (chosen['tools'], chosen['disallowed_tools']) == (['read'], ['bash'])
    plain = {'agent_dir': request['agent_dir']}
    pi_apply_tools(plain, {'tools': None, 'disallowed_tools': ['grep']})
    assert plain == {'agent_dir': request['agent_dir'], 'tools': None, 'disallowed_tools': ['grep']}


def test_pi_refuses_provably_unknown_entries(tmp_path: Path) -> None:
    """551 user decision: an entry Pi does not know is refused at create, never silently ignored."""
    request = {'agent_dir': str(agent_dir(tmp_path, None)), 'worktree_path': str(tmp_path)}
    for tools in ({'tools': ['nosuchtool'], 'disallowed_tools': []},
                  {'tools': None, 'disallowed_tools': ['nosuchtool']},
                  {'tools': ['read', 'mcp__nosuchserver__x'], 'disallowed_tools': []},
                  {'tools': ['read', 'mcp__graftling__tasks'], 'disallowed_tools': []}):
        with pytest.raises(AgentsError) as refused:
            pi_apply_tools(dict(request), tools, own_server='graphtraj')
        assert refused.value.code == 'unavailable'
        assert ([*(tools['tools'] or ()), *tools['disallowed_tools']][-1]) in refused.value.message
    known = dict(request)
    pi_apply_tools(known, {'tools': ['read', '+grep', 'mcp__graphtraj__tasks'], 'disallowed_tools': ['mcp__graphtraj__subagents']},
                   own_server='graphtraj')
    assert 'unverified_tools' not in known


def test_pi_reports_what_only_pi_can_resolve_at_start(tmp_path: Path) -> None:
    """Patterns, another configured server's tools, and (with host extensions) non-built-in names are reported."""
    directory = agent_dir(tmp_path, None)
    (directory / 'mcp.json').write_text(json.dumps({'mcpServers': {'playwright': {'command': 'x'}}}))
    request = {'agent_dir': str(directory), 'worktree_path': str(tmp_path)}
    pi_apply_tools(request, {'tools': ['read', 'mcp__playwright__browser_navigate', 'mcp__graftling__*'],
                             'disallowed_tools': []})
    assert request['unverified_tools'] == ['mcp__playwright__browser_navigate', 'mcp__graftling__*']
    with pytest.raises(AgentsError):  # no host extension: a non-built-in plain name is provably unknown
        pi_apply_tools(dict(request), {'tools': ['plan_mode'], 'disallowed_tools': []})
    (directory / 'extensions').mkdir()
    (directory / 'extensions' / 'plan.ts').write_text('export default () => {}\n')
    pi_apply_tools(request, {'tools': ['plan_mode'], 'disallowed_tools': []})
    assert request['unverified_tools'] == ['plan_mode']


# Pi's extension: what the confirm hook asks and blocks, with the parent's answer given per call.
PI = r'''
const module = await import(process.argv[1]);
const answers = JSON.parse(process.argv[2]);
const handlers = [], asked = [], blocked = [];
const pi = { registerFlag() {}, registerCommand() {}, registerMcpServer() {},
             on: (event, handler) => { if (event === 'tool_call') handlers.push(handler); } };
const installed = module.registerToolConfirm(pi);
for (const [toolName, answer] of answers) {
  const ctx = { ui: { confirm: async (title, call) => { asked.push([title, JSON.parse(call).tool]); return answer; } } };
  for (const handler of handlers) {
    const result = await handler({ toolName, toolCallId: toolName, input: {} }, ctx);
    if (result?.block) { blocked.push(toolName); break; }
  }
}
console.log(JSON.stringify({ installed, asked, blocked }));
'''


def test_pi_extension_asks_the_parent_before_a_beyond_host_tool() -> None:
    """Deny blocks the call; allow lets it run and is not asked again; other tools are untouched."""
    answers = [['grep', False], ['grep', True], ['grep', False], ['read', False]]
    found = node(PI, (SOURCE / 'pi/extension.mjs').as_uri(), json.dumps(answers),
                 GRAFTLING_PI_CONFIRM_TOOLS=json.dumps(['grep']))
    assert found == {'installed': True, 'asked': [[CONFIRM_TITLE, 'grep'], [CONFIRM_TITLE, 'grep']], 'blocked': ['grep']}


def test_pi_extension_without_beyond_host_tools_installs_nothing() -> None:
    found = node(PI, (SOURCE / 'pi/extension.mjs').as_uri(), json.dumps([['grep', False]]))
    assert found == {'installed': False, 'asked': [], 'blocked': []}


PRESET = """\
- insert:
    - id: preset-standard
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: standard
        plugins:
          - id: persona
            name: '@deepseek-ai/dsh-persona'
            config: {prefix: You are a coding agent.}
          - id: tool-bash
            name: '@deepseek-ai/dsh-tool-bash'
            disabled: !!js process.platform === 'win32'
          - id: tool-fs
            name: '@deepseek-ai/dsh-tool-fs'
          - id: delegation
            name: cordis:group
            group: true
            config:
              - id: tool-subagent
                name: '@deepseek-ai/dsh-tool-subagent'
          - id: tool-web
            name: '@deepseek-ai/dsh-tool-web'
            config: {fetch: true}
          - id: tool-plugin-market
            name: '@deepseek-ai/dsh-plugin-market/tools'
            disabled: true
"""


@pytest.fixture
def installed(tmp_path: Path) -> dict:
    """A DSH install root whose web-app bundle declares the preset above."""
    root = tmp_path / 'dsh'
    bundle = root / 'node_modules/@deepseek-ai/dsh-web-app'
    (bundle / 'presets').mkdir(parents=True)
    (bundle / 'package.json').write_text(json.dumps({'dsh': {'bundle': {'patch': ['./presets/standard.patch.yml']}}}))
    (bundle / 'presets/standard.patch.yml').write_text(PRESET)
    return {'package': str(root / 'package.json'), 'instructions': 'role text'}


def rows(request: dict) -> dict:
    preset = dsh_execution._deployment_agent_preset(Path(request['package']).parent, 'role text')
    if request.get('tools') is not None or request.get('disallowed_tools'):
        dsh_execution._select_tool_rows(preset, request.get('tools'), list(request.get('disallowed_tools') or ()))
    return {row['id']: row.get('disabled') for row in dsh_execution._tool_rows(preset['config']['plugins'])}


def test_dsh_without_a_choice_keeps_the_installed_rows(installed: dict) -> None:
    found = rows(installed)
    assert found['tool-fs'] is None and found['tool-web'] is None and found['tool-plugin-market'] is True
    assert isinstance(found['tool-bash'], dsh_execution._JsExpression)


def test_dsh_choice_disables_only_the_other_or_denied_rows(installed: dict) -> None:
    """Narrowing needs no authorization: rows outside tools, or denied, are disabled; group rows included."""
    request = dict(installed)
    dsh_execution.apply_tools(request, {'tools': ['tool-fs', 'tool-web', 'tool-subagent'], 'disallowed_tools': ['tool-web']})
    found = rows(request)
    assert found['tool-fs'] is None and found['tool-subagent'] is None
    assert found['tool-web'] is True and found['tool-bash'] is True and found['tool-plugin-market'] is True
    request = dict(installed)
    dsh_execution.apply_tools(request, {'tools': None, 'disallowed_tools': ['tool-web']})
    found = rows(request)
    assert found['tool-web'] is True and found['tool-fs'] is None
    assert isinstance(found['tool-bash'], dsh_execution._JsExpression)


def test_dsh_unknown_row_is_unavailable_and_naming_the_limit(installed: dict) -> None:
    with pytest.raises(AgentsError) as refused:
        dsh_execution.apply_tools(dict(installed), {'tools': ['web_fetch'], 'disallowed_tools': []})
    assert refused.value.code == 'unavailable'
    assert 'plugin row' in refused.value.message and 'tool-web' in refused.value.message


def test_dsh_enabling_a_host_disabled_row_needs_authorization_it_cannot_get(installed: dict) -> None:
    """DSH has no approval route that enables a plugin: the child is not started."""
    request = dict(installed)
    with pytest.raises(AgentsError) as refused:
        dsh_execution.apply_tools(request, {'tools': ['tool-fs', 'tool-plugin-market'], 'disallowed_tools': []})
    assert refused.value.code == 'authorization-required'
    assert 'tools' not in request


def test_dsh_installed_preset_rows_are_selectable() -> None:
    """The installed DSH's own standard preset names the rows a create may choose."""
    executable = shutil.which('dsh')
    if executable is None:
        pytest.skip('Installed DSH is required for its own preset.')
    package = dsh_package(executable)
    preset = dsh_execution._deployment_agent_preset(package.parent, 'role text')
    if preset is None:
        pytest.skip('This DSH has no readable web-app preset.')
    ids = {row['id'] for row in dsh_execution._tool_rows(preset['config']['plugins'])}
    assert {'tool-fs', 'tool-web'} <= ids
    request = {'package': str(package)}
    dsh_execution.apply_tools(request, {'tools': None, 'disallowed_tools': ['tool-web']})
    assert request == {'package': str(package), 'tools': None, 'disallowed_tools': ['tool-web']}
