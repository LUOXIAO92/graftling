"""A Claude child without tools gets its Claude Code's own default set, probed at run time (576).

The stub executables stand in for Claude Code only to check graftling's argv, environment,
parsing, cache and failure paths; what the real Claude Code offers is checked on a real host.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

import pytest

from graftling.claude import execution as module
from graftling.claude.execution import (ClaudeExecution, probe_default_tools, read_init_tools, tool_probe_argv,
                                        tool_probe_environment, tool_probe_key)
from graftling.runtime import RuntimeAdapterError
from posix_only import SHEBANG, require_posix


def _claude(tmp_path: Path, **request: object) -> ClaudeExecution:
    directory = tmp_path / 'state' / 'agent' / 'one'
    directory.mkdir(parents=True, exist_ok=True)
    return ClaudeExecution({'worktree_path': str(tmp_path), 'runtime_home': str(tmp_path / 'home'), **request},
                           'prompt', directory, lambda *_: None, lambda *_: None, directory / 't.jsonl', None)


def _stub(tmp_path: Path, body: str) -> Path:
    """A stand-in executable that records its argv, cwd and environment, then runs ``body``."""
    require_posix(SHEBANG)
    path = tmp_path / 'claude-stub'
    path.write_text(f'''#!{sys.executable}
import json, os, sys, time
log = {str(tmp_path / 'calls.jsonl')!r}
with open(log, 'a') as handle:
    handle.write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': dict(os.environ)}}) + '\\n')
sys.stdin.readline()
{body}
''')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _calls(tmp_path: Path) -> list[dict]:
    log = tmp_path / 'calls.jsonl'
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


INIT = ('print(json.dumps({"type": "system", "subtype": "init", "tools": '
        '["Task", "Bash", "Read", "WebFetch", "mcp__x__y"]}), flush=True)\ntime.sleep(30)')


@pytest.fixture(autouse=True)
def _fresh_memo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, '_default_tools', {})


def _with_default(execution: ClaudeExecution, tools: list[str]) -> ClaudeExecution:
    execution.default_tools = tools
    return execution


def test_omitted_tools_name_the_probed_set(tmp_path: Path) -> None:
    """--tools is always passed: the probed default without a choice, the create's set with one."""
    assert _with_default(_claude(tmp_path), ['Bash', 'Read']).tool_arguments() == ['--tools', 'Bash,Read']
    assert _with_default(_claude(tmp_path, disallowed_tools=['Bash']), ['Bash', 'Read']).tool_arguments() == [
        '--tools', 'Bash,Read', '--disallowedTools', 'Bash']
    assert _claude(tmp_path, tools=['Read'], disallowed_tools=['WebFetch']).tool_arguments() == [
        '--tools', 'Read', '--disallowedTools', 'WebFetch']
    assert _claude(tmp_path, tools=[]).tool_arguments() == ['--tools', '']
    with pytest.raises(ValueError):   # outside launch preparation there is nothing to probe with
        _claude(tmp_path).tool_arguments()


def test_probe_runs_unrestricted_without_settings_credentials_or_mcp(tmp_path: Path) -> None:
    """The probe's argv and environment: no --restricted, no settings sources, no MCP, no credential."""
    argv = tool_probe_argv('/bin/claude', 'claude-haiku-5-5', 'auto')
    assert '--restricted' not in argv and '--tools' not in argv
    assert argv[argv.index('--setting-sources') + 1] == ''
    assert '--strict-mcp-config' in argv and json.loads(argv[argv.index('--mcp-config') + 1]) == {'mcpServers': {}}
    assert argv[argv.index('--model') + 1] == 'claude-haiku-5-5'
    assert argv[argv.index('--permission-mode') + 1] == 'auto'
    assert '--permission-mode' not in tool_probe_argv('/bin/claude', 'm', None)
    child = {'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_AUTH_TOKEN': 't', 'CLAUDE_CODE_OAUTH_TOKEN': 'o',
             'CLAUDE_CONFIG_DIR': str(Path.home() / '.claude'), 'ANTHROPIC_BASE_URL': 'https://example.invalid',
             'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1', 'PATH': '/bin'}
    env = tool_probe_environment(child, tmp_path / 'home')
    assert not {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN'} & set(env)
    assert env['CLAUDE_CONFIG_DIR'] == str(tmp_path / 'home')
    assert env['ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:9'
    assert env['CLAUDE_CODE_DISABLE_BACKGROUND_TASKS'] == '1' and env['PATH'] == '/bin'


def test_probe_reads_built_in_names_and_caches_them_in_graftling_state(tmp_path: Path) -> None:
    """One probe per key: built-ins only, kept under the private state, not under ~/.claude."""
    stub = _stub(tmp_path, INIT)
    state = tmp_path / 'state'
    env = {'PATH': os.environ['PATH'], 'ANTHROPIC_API_KEY': 'secret', 'CLAUDE_CODE_EFFORT_LEVEL': 'low'}
    tools = probe_default_tools(str(stub), '2.1.330', 'claude-haiku-5-5', None, env, state)
    assert tools == ['Task', 'Bash', 'Read', 'WebFetch']
    (call,) = _calls(tmp_path)
    assert '--restricted' not in call['argv'] and call['argv'][call['argv'].index('--setting-sources') + 1] == ''
    assert 'ANTHROPIC_API_KEY' not in call['env'] and call['env']['ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:9'
    home = Path(call['env']['CLAUDE_CONFIG_DIR'])
    assert home.is_relative_to(state / 'claude-default-tools') and not home.exists()   # removed after
    assert Path(call['cwd']).resolve().is_relative_to((state / 'claude-default-tools').resolve())
    key = tool_probe_key(str(stub), '2.1.330', 'claude-haiku-5-5', None, env)
    assert json.loads((state / 'claude-default-tools' / f'{key}.json').read_text())['tools'] == tools
    assert probe_default_tools(str(stub), '2.1.330', 'claude-haiku-5-5', None, env, state) == tools
    module._default_tools.clear()   # a new host process reads the cache file
    assert probe_default_tools(str(stub), '2.1.330', 'claude-haiku-5-5', None, env, state) == tools
    assert len(_calls(tmp_path)) == 1


def test_cache_key_follows_executable_version_model_and_claude_settings(tmp_path: Path) -> None:
    stub = _stub(tmp_path, INIT)
    base = tool_probe_key(str(stub), '1', 'm', None, {})
    assert tool_probe_key(str(stub), '1', 'm', None, {'OTHER': 'x'}) == base
    assert len({base, tool_probe_key(str(stub), '2', 'm', None, {}), tool_probe_key(str(stub), '1', 'n', None, {}),
                tool_probe_key(str(stub), '1', 'm', 'auto', {}),
                tool_probe_key(str(stub), '1', 'm', None, {'CLAUDE_CODE_SIMPLE': '1'})}) == 5
    stub.write_text(stub.read_text() + '\n')   # an updated binary in place
    assert tool_probe_key(str(stub), '1', 'm', None, {}) != base


@pytest.mark.parametrize('body, cause', [
    ('print("not json"); sys.exit(3)', 'no system/init (exit 3'),
    ('print(json.dumps({"type": "system", "subtype": "init", "tools": []}), flush=True)', 'the default set is empty'),
    ('print(json.dumps({"type": "system", "subtype": "init", "tools": ["mcp__a__b"]}), flush=True)',
     'the default set is empty'),
])
def test_a_failed_probe_fails_the_child_and_names_the_way_out(tmp_path: Path, body: str, cause: str) -> None:
    """No silent fallback: the error names the cause and create's tools; nothing is cached."""
    stub = _stub(tmp_path, body)
    with pytest.raises(RuntimeAdapterError) as error:
        probe_default_tools(str(stub), '1', 'm', None, {'PATH': os.environ['PATH']}, tmp_path / 'state')
    assert error.value.code == 'RUNTIME_REQUEST_FAILED'
    assert cause in str(error.value) and "create's tools" in str(error.value)
    assert not list((tmp_path / 'state' / 'claude-default-tools').glob('*.json'))


def test_probe_timeout_and_missing_executable(tmp_path: Path) -> None:
    stub = _stub(tmp_path, 'time.sleep(30)')
    tools, cause = read_init_tools([str(stub)], {'PATH': os.environ['PATH']}, tmp_path, timeout=1)
    assert tools is None and cause.startswith('no system/init within 1 s')
    tools, cause = read_init_tools([str(tmp_path / 'absent')], {}, tmp_path, timeout=1)
    assert tools is None and 'could not start' in cause


def test_prepare_keeps_restricted_and_passes_the_probed_set(tmp_path: Path) -> None:
    """The child's launch: --restricted and the sandbox stay; --tools carries the probed set."""
    stub = _stub(tmp_path, INIT)
    request = {'executable': str(stub), 'version': '2.1.330', 'model': 'claude-haiku-5-5', 'instructions': 'x',
               'reasoning_effort': None, 'worktree_access': 'write', 'disallowed_tools': ['WebFetch']}
    execution = _claude(tmp_path, **request)
    argv, _ = execution._prepare(None)
    assert '--restricted' in argv
    assert argv[argv.index('--tools') + 1] == 'Task,Bash,Read,WebFetch'
    assert argv[argv.index('--disallowedTools') + 1] == 'WebFetch'
    assert argv.index('--tools') < argv.index('--model')
    assert json.loads(argv[argv.index('--settings') + 1])['sandbox']['enabled'] is True
    assert execution.diagnostics['default_tools'] == ['Task', 'Bash', 'Read', 'WebFetch']
    probe = _calls(tmp_path)[0]
    assert probe['argv'][probe['argv'].index('--model') + 1] == 'claude-haiku-5-5'
    assert module.MEMBER_RUN_ENV not in probe['env'] and module.CONNECTION_ENV not in probe['env']
    assert probe['env']['CLAUDE_CODE_SDK_READS_SESSION_STATE'] == '1'
    chosen = _claude(tmp_path, **{**request, 'tools': ['Read']})
    argv, _ = chosen._prepare(None)
    assert argv[argv.index('--tools') + 1] == 'Read' and len(_calls(tmp_path)) == 1   # no probe


def test_tool_check_reports_probed_tools_the_child_lacks(tmp_path: Path) -> None:
    execution = _claude(tmp_path, disallowed_tools=['WebFetch'])
    execution.default_tools = ['Bash', 'Read', 'WebFetch', 'Monitor']
    assert execution.tool_check(['Bash', 'Read']) == {'default_tools_not_offered': ['Monitor']}
    execution.default_tools = ['Bash', 'Read']
    assert execution.tool_check(['Bash', 'Read', 'mcp__graftling__subagents']) == {}


class MemberShapedExecution(ClaudeExecution):
    """Shaped like a consumer subclass (GraphTraj's): fixed member tools unless independent."""
    MEMBER_TOOLS = 'Bash,Read,Write,Edit,Glob,Grep'

    def tool_arguments(self) -> list[str]:
        return super().tool_arguments() if self.independent else ['--tools', self.MEMBER_TOOLS]


def _member(tmp_path: Path, independent: bool, **request: object) -> MemberShapedExecution:
    directory = tmp_path / 'state' / 'agent' / 'one'
    directory.mkdir(parents=True, exist_ok=True)
    execution = MemberShapedExecution(
        {'worktree_path': str(tmp_path), 'runtime_home': str(tmp_path / 'home'), 'executable': str(_stub(tmp_path, INIT)),
         'version': '2.1.330', 'model': 'claude-haiku-5-5', 'instructions': 'x', 'reasoning_effort': None,
         'worktree_access': 'write', **request},
        'prompt', directory, lambda *_: None, lambda *_: None, directory / 't.jsonl', None)
    execution.independent = independent
    return execution


def test_a_subclass_with_fixed_member_tools_never_probes(tmp_path: Path) -> None:
    """A zero-argument override that does not call super keeps its flags and starts no probe."""
    execution = _member(tmp_path, independent=False)
    argv, _ = execution._prepare(None)
    assert argv[argv.index('--tools') + 1] == 'Bash,Read,Write,Edit,Glob,Grep'
    assert _calls(tmp_path) == [] and execution.default_tools is None
    assert 'default_tools' not in execution.diagnostics
    assert execution.tool_check(['Bash', 'Read']) == {}


def test_a_subclass_independent_child_gets_the_probed_set_or_its_choice(tmp_path: Path) -> None:
    execution = _member(tmp_path, independent=True)
    argv, _ = execution._prepare(None)
    assert argv[argv.index('--tools') + 1] == 'Task,Bash,Read,WebFetch' and len(_calls(tmp_path)) == 1
    assert execution.diagnostics['default_tools'] == ['Task', 'Bash', 'Read', 'WebFetch']
    module._default_tools.clear()
    other = tmp_path / 'chosen'
    other.mkdir()
    chosen = _member(other, independent=True, tools=['Read'])
    argv, _ = chosen._prepare(None)
    assert argv[argv.index('--tools') + 1] == 'Read'
    assert _calls(other) == [] and chosen.default_tools is None
