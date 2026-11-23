"""One variable, GRAFTLING_DATA_ROOT, chooses the data root of every host (577).

Per host: the default stays ``<project>/.agents-control``; GRAFTLING_DATA_ROOT
replaces it; the host's own setting (GRAFTLING_DSH_CONFIG, --agents-config,
--directory, configuration --write material) still wins; a bound child holding
GRAFTLING_DATA_ROOT and GRAFTLING_CONNECTION starts no host. Stand-ins only:
fake native parents, no model or network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from graftling.claude import host as claude_host
from graftling.claude.plugin import MARKETPLACE_DIRECTORY, marketplace_module
from test_dsh_standalone_host import fake_dsh, header, run_host as run_dsh_host, run_plugin
from test_pi_default_host_572 import installed, run_default_host as run_pi_default, start as start_pi
from test_pi_standalone_host import SOURCE, host_environment


# ---------------------------------------------------------------- DSH


def run_dsh_default(project: Path, **environment: str) -> dict:
    """Start the DSH host without --config below a fake native DSH in ``project``, with ``environment`` added."""
    entry = fake_dsh(project.parent / 'fake')
    env = {key: value for key, value in os.environ.items()
           if key not in {'GRAFTLING_CONNECTION', 'DSH_SESSION_ID', 'DSH_AGENT_SOURCE', 'TEST_CONFIG',
                          'GRAFTLING_DATA_ROOT'}}
    env.update(GRAFTLING_DSH_NODE=sys.executable, GRAFTLING_DSH_ENTRY=str(entry), PYTHONPATH=str(SOURCE),
               TEST_LAUNCH=json.dumps({'header': header(project)}), **environment)
    result = subprocess.run([sys.executable, str(entry)], cwd=project, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_dsh_host_uses_the_variable_instead_of_the_project_root(tmp_path: Path) -> None:
    """GRAFTLING_DATA_ROOT, created when missing, holds the live endpoint; the project gets no .agents-control."""
    project = tmp_path / 'project'
    project.mkdir()
    root = tmp_path / 'chosen' / 'data'
    outcome = run_dsh_default(project, GRAFTLING_DATA_ROOT=str(root))
    assert outcome['code'] == 0, outcome['stderr']
    assert Path(json.loads(outcome['first'])['connection']).parent == root / 'live' / 'dsh-host'
    assert not (project / '.agents-control').exists()


def test_dsh_host_refuses_a_relative_variable(tmp_path: Path) -> None:
    """A relative value is refused with a message naming the variable; nothing is created."""
    project = tmp_path / 'project'
    project.mkdir()
    outcome = run_dsh_default(project, GRAFTLING_DATA_ROOT='relative/data')
    error = json.loads(outcome['stderr'])['error']
    assert outcome['code'] == 1 and 'GRAFTLING_DATA_ROOT must be an absolute directory' in error['message']
    assert not (project / 'relative').exists() and not (project / '.agents-control').exists()


def test_dsh_configuration_wins_over_the_variable(tmp_path: Path) -> None:
    """GRAFTLING_DSH_CONFIG's root is used as before; the variable's directory is not created."""
    outcome = run_dsh_host(tmp_path, {'root': str(tmp_path / 'configured')},
                           GRAFTLING_DATA_ROOT=str(tmp_path / 'unused'))
    assert outcome['code'] == 0, outcome['stderr']
    assert Path(json.loads(outcome['first'])['connection']).parent == tmp_path / 'configured' / 'live' / 'dsh-host'
    assert not (tmp_path / 'unused').exists()


def test_dsh_plugin_with_the_variable_starts_the_host_without_configuration(tmp_path: Path) -> None:
    """The plugin passes no --config and does not judge the Session cwd; GRAFTLING_DSH_CONFIG still wins."""
    shown, log = run_plugin(tmp_path / 'set', GRAFTLING_DATA_ROOT='/abs/data', TEST_CWD=json.dumps(None))
    assert 'refused' not in shown and log['argv'] == ['-I', '-m', 'graftling.dsh.host']
    shown, log = run_plugin(tmp_path / 'both', GRAFTLING_DATA_ROOT='/abs/data', GRAFTLING_DSH_CONFIG='/abs/c.json')
    assert log['argv'] == ['-I', '-m', 'graftling.dsh.host', '--config', '/abs/c.json']
    shown, _ = run_plugin(tmp_path / 'neither', TEST_CWD=json.dumps(None))
    assert 'GRAFTLING_DATA_ROOT' in shown['refused'] and 'GRAFTLING_DSH_CONFIG' in shown['refused']


def test_dsh_child_with_the_variable_starts_no_host(tmp_path: Path) -> None:
    """Plugin inert and host refused for a bound child holding GRAFTLING_DATA_ROOT; the root is not created."""
    root = tmp_path / 'parent-root'
    assert run_plugin(tmp_path / 'plugin', GRAFTLING_CONNECTION='/bound',
                      GRAFTLING_DATA_ROOT=str(root)) == ({'inert': True}, None)
    project = tmp_path / 'project'
    project.mkdir()
    outcome = run_dsh_default(project, GRAFTLING_CONNECTION='/bound', GRAFTLING_DATA_ROOT=str(root))
    assert outcome['code'] == 1 and 'already-bound child' in json.loads(outcome['stderr'])['error']['message']
    assert not root.exists() and not (project / '.agents-control').exists()


# ---------------------------------------------------------------- Pi


def run_pi_host(project: Path, *arguments: str, **environment: str) -> tuple[subprocess.Popen, str]:
    """Start the real Pi host in ``project`` with ``environment`` added; close at once."""
    env = host_environment(project)
    env.pop('GRAFTLING_DATA_ROOT', None)
    env.update(environment)
    process = subprocess.Popen([sys.executable, '-m', 'graftling.pi.host', *arguments], cwd=project, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    process.stdin.write(json.dumps({'header': {'type': 'session', 'version': 3, 'id': 'pi-main',
                                               'cwd': str(project)}, 'entries': []}) + '\n')
    process.stdin.flush()
    first = process.stdout.readline()
    process.stdin.close()
    process.wait(20)
    return process, first


def test_pi_host_uses_the_variable_instead_of_the_project_root(tmp_path: Path) -> None:
    """Without --config the host serves under GRAFTLING_DATA_ROOT; the project gets no .agents-control."""
    project = (tmp_path / 'project')
    project.mkdir()
    project = project.resolve()
    root = tmp_path.resolve() / 'chosen'
    process, first = run_pi_host(project, GRAFTLING_DATA_ROOT=str(root))
    assert process.returncode == 0, process.stderr.read()
    assert json.loads(first)['connection'].startswith(str(root / 'live' / 'pi-host'))
    assert not (project / '.agents-control').exists()


def test_pi_host_refuses_an_uncreatable_variable(tmp_path: Path) -> None:
    """A value naming a path below a file is refused with the variable's name."""
    project = tmp_path.resolve()
    (project / 'a-file').write_text('')
    process, first = run_pi_host(project, GRAFTLING_DATA_ROOT=str(project / 'a-file' / 'data'))
    error = json.loads(process.stderr.read())['error']
    assert process.returncode == 1 and first == ''
    assert 'GRAFTLING_DATA_ROOT' in error['message'] and not (project / '.agents-control').exists()


def test_pi_configuration_wins_over_the_variable(tmp_path: Path) -> None:
    """--config's root is used as before; the variable's directory is not created."""
    project = tmp_path.resolve()
    (project / 'host.json').write_text(json.dumps({'root': str(project / 'configured')}))
    process, first = run_pi_host(project, '--config', str(project / 'host.json'),
                                 GRAFTLING_DATA_ROOT=str(project / 'unused'))
    assert process.returncode == 0, process.stderr.read()
    assert json.loads(first)['connection'].startswith(str(project / 'configured' / 'live' / 'pi-host'))
    assert not (project / 'unused').exists()


def test_pi_extension_with_the_variable(tmp_path: Path) -> None:
    """No flag: the host starts without --config; the flag still wins; a managed DSH context stays inert."""
    extension, python = installed(tmp_path)
    found, launches = start_pi(tmp_path, extension, GRAFTLING_DATA_ROOT='/abs/data')
    assert found['started'] is True and launches[-1]['argv'] == ['-I', '-m', 'graftling.pi.host']
    found, launches = start_pi(tmp_path, extension, GRAFTLING_DATA_ROOT='/abs/data', TEST_CONFIG='/conf/pi.json')
    assert launches[-1]['argv'] == ['-I', '-m', 'graftling.pi.host', '--config', '/conf/pi.json']
    count = len(launches)
    assert start_pi(tmp_path, extension, GRAFTLING_DATA_ROOT='/abs/data', DSH_SESSION_ID='x') == (
        {'started': False, 'servers': []}, launches)
    found, launches = start_pi(tmp_path, extension, TEST_CWD='relative/x')
    assert 'GRAFTLING_DATA_ROOT' in found['error'] and '--agents-config' in found['error']
    assert len(launches) == count


def test_pi_child_with_the_variable_starts_no_host(tmp_path: Path) -> None:
    """The extension keeps the owner's connection; the host refuses a bound child; the root is not created."""
    extension, _ = installed(tmp_path)
    root = tmp_path / 'parent-root'
    found, launches = start_pi(tmp_path, extension, GRAFTLING_CONNECTION='/owner', GRAFTLING_PYTHON='/owner/python',
                               GRAFTLING_DATA_ROOT=str(root))
    assert found == {'started': True, 'servers': [{'name': 'graftling', 'command': '/owner/python'}]}
    assert launches == []
    project = (tmp_path / 'project').resolve()
    process, first = run_pi_host(project, GRAFTLING_CONNECTION='/owner', GRAFTLING_DATA_ROOT=str(root))
    assert process.returncode == 1 and first == ''
    assert 'already-bound child' in process.stderr.read()
    assert not root.exists() and not (project / '.agents-control').exists()


# ---------------------------------------------------------------- Codex


@pytest.fixture
def codex_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Settings for the controlled Codex peer, whose Session cwd is ``tmp_path/project``."""
    project = tmp_path / 'project'
    project.mkdir()
    peer = tmp_path / 'codex'
    peer.write_text('#!' + sys.executable + '\n' + Path(__file__).with_name('codex_stdio_peer.py').read_text())
    peer.chmod(0o755)
    monkeypatch.setenv('PYTHONPATH', str(SOURCE))
    monkeypatch.setenv('PEER_NATIVE_ROLLOUT', str(tmp_path / 'native'))
    monkeypatch.delenv('GRAFTLING_DATA_ROOT', raising=False)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'adapter_request': {
        'arguments': [str(peer)], 'worktree_path': str(project),
        'session_parameters': {'cwd': str(project), 'model': 'chosen-model', 'developerInstructions': '',
                               'config': {'default_permissions': 'chosen', 'permissions': {
                                   'chosen': {'extends': ':workspace', 'filesystem': {str(project): 'write'}}}}},
    }}))
    return settings


def run_codex(settings: Path, *arguments: str, **environment: str) -> subprocess.CompletedProcess:
    """Run one Codex host turn that the peer fails, so it ends at once."""
    return subprocess.run([sys.executable, '-m', 'graftling.codex.host', '--settings', str(settings),
                           *arguments, 'failed-turn'], env={**os.environ, **environment},
                          capture_output=True, text=True, timeout=30)


def test_codex_without_directory_uses_the_project_default_then_the_variable(
        tmp_path: Path, codex_settings: Path) -> None:
    """No --directory: <Session cwd>/.agents-control; with GRAFTLING_DATA_ROOT, that directory instead."""
    project = tmp_path / 'project'
    result = run_codex(codex_settings)
    assert 'native execution failed' in result.stderr, result.stdout + result.stderr
    assert (project / '.agents-control' / 'host').is_dir()
    shutil.rmtree(project / '.agents-control')
    result = run_codex(codex_settings, GRAFTLING_DATA_ROOT=str(tmp_path / 'chosen'))
    assert 'native execution failed' in result.stderr, result.stdout + result.stderr
    assert (tmp_path / 'chosen' / 'host').is_dir() and not (project / '.agents-control').exists()


def test_codex_directory_wins_and_resume_uses_the_same_root(tmp_path: Path, codex_settings: Path) -> None:
    """--directory is used as before; --resume without it looks in the same default root."""
    result = run_codex(codex_settings, '--directory', str(tmp_path / 'given'),
                       GRAFTLING_DATA_ROOT=str(tmp_path / 'unused'))
    assert 'native execution failed' in result.stderr, result.stdout + result.stderr
    assert (tmp_path / 'given' / 'host').is_dir() and not (tmp_path / 'unused').exists()
    # Nothing retained to resume in the variable's root: the refusal reads that root.
    result = run_codex(codex_settings, '--resume', GRAFTLING_DATA_ROOT=str(tmp_path / 'chosen'))
    assert result.returncode == 1 and str(tmp_path / 'chosen' / 'host' / 'session.yml') in result.stdout


@pytest.mark.parametrize('cwd', [None, 'relative/project', '/no/such/project-577'])
def test_codex_without_a_usable_session_cwd_names_both_ways(tmp_path: Path, codex_settings: Path,
                                                           cwd: str | None) -> None:
    """The refusal names --directory and GRAFTLING_DATA_ROOT; a relative variable is refused by name."""
    document = json.loads(codex_settings.read_text())
    if cwd is None:
        del document['adapter_request']['session_parameters']['cwd']
    else:
        document['adapter_request']['session_parameters']['cwd'] = cwd
    codex_settings.write_text(json.dumps(document))
    result = run_codex(codex_settings)
    error = json.loads(result.stdout.splitlines()[0])['error']['message']
    assert result.returncode == 1 and '--directory' in error and 'GRAFTLING_DATA_ROOT' in error
    result = run_codex(codex_settings, GRAFTLING_DATA_ROOT='relative/data')
    assert result.returncode == 1 and 'GRAFTLING_DATA_ROOT must be an absolute directory' in result.stdout


# ---------------------------------------------------------------- Claude Code


@pytest.fixture
def claude_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in ``claude`` first on PATH; GRAFTLING_DATA_ROOT starts unset."""
    directory = tmp_path / 'bin'
    directory.mkdir()
    claude = directory / 'claude'
    claude.write_text('#!/bin/sh\n')
    claude.chmod(0o755)
    monkeypatch.setenv('PATH', f'{directory}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.delenv('GRAFTLING_DATA_ROOT', raising=False)
    monkeypatch.delenv('GRAFTLING_CONNECTION', raising=False)
    return claude


def run_claude_host(*arguments: str, cwd: Path, **environment: str) -> subprocess.CompletedProcess:
    """Run the Claude host module from this checkout, as the plugin's launcher would."""
    return subprocess.run([sys.executable, '-m', 'graftling.claude.host', *arguments], cwd=cwd,
                          env={**os.environ, 'PYTHONPATH': str(SOURCE), **environment},
                          capture_output=True, text=True, timeout=30, input='')


def test_claude_plugin_keeps_its_configuration_in_the_variable(tmp_path: Path, claude_on_path: Path) -> None:
    """serve --project writes claude-host.json into GRAFTLING_DATA_ROOT, whose root it is; host.log is there too."""
    project = tmp_path / 'project'
    project.mkdir()
    root = tmp_path / 'chosen'
    result = run_claude_host('serve', '--project', str(project), cwd=project, GRAFTLING_DATA_ROOT=str(root))
    assert result.returncode == 0, result.stderr
    assert json.loads((root / 'claude-host.json').read_text()) == {
        'root': str(root), 'cwd': str(project.resolve()), 'claude': str(claude_on_path),
        'channel_file': str(root / 'live' / 'channel.json'), 'settings': {}}
    assert 'must be launched directly by the selected Claude CLI' in (root / 'host.log').read_text()
    assert not (project / '.agents-control').exists()
    # The mod's context finds it through the value it read.
    found = run_claude_host('context', '--project-from', str(project), '--data-root', str(root),
                            cwd=project, input='{}')
    assert found.returncode == 1 and 'The subagents host for this project is not running' in found.stderr
    missing = run_claude_host('context', '--project-from', str(project), '--data-root', str(tmp_path / 'none'),
                              cwd=project, input='{}')
    assert missing.returncode == 1 and 'GRAFTLING_DATA_ROOT' in missing.stderr


def test_claude_plugin_refuses_a_relative_variable(tmp_path: Path, claude_on_path: Path) -> None:
    """The refusal names the variable and is logged in the project's .agents-control as before."""
    project = tmp_path / 'project'
    project.mkdir()
    result = run_claude_host('serve', '--project', str(project), cwd=project, GRAFTLING_DATA_ROOT='relative')
    assert result.returncode == 0, result.stderr
    assert 'GRAFTLING_DATA_ROOT must be an absolute directory' in (project / '.agents-control' / 'host.log').read_text()


def test_claude_explicit_configurations_win_over_the_variable(tmp_path: Path, claude_on_path: Path) -> None:
    """configuration --write material still makes the plugin stand aside; serve --config ignores the variable."""
    project = tmp_path / 'project'
    project.mkdir()
    (project / '.mcp.json').write_text(json.dumps({'mcpServers': {'graftling': {
        'command': sys.executable, 'args': ['-m', 'graftling.claude.host', 'serve', '--config', 'x']}}}))
    root = tmp_path / 'chosen'
    run_claude_host('serve', '--project', str(project), cwd=project, GRAFTLING_DATA_ROOT=str(root))
    assert 'host-busy' in (root / 'host.log').read_text() and not (root / 'claude-host.json').exists()
    configured = tmp_path / 'configured' / 'claude-host.json'
    configured.parent.mkdir()
    configured.write_text(json.dumps({'root': str(configured.parent), 'cwd': str(project), 'claude': '/c',
                                      'channel_file': str(configured.parent / 'live' / 'channel.json')}))
    unused = tmp_path / 'unused'
    run_claude_host('serve', '--config', str(configured), cwd=project, GRAFTLING_DATA_ROOT=str(unused))
    assert (configured.parent / 'host.log').is_file() and not unused.exists()


def test_claude_bound_child_with_the_variable_writes_no_configuration(tmp_path: Path, claude_on_path: Path) -> None:
    """A bound child's plugin server refuses before touching the parent's GRAFTLING_DATA_ROOT."""
    project = tmp_path / 'project'
    project.mkdir()
    root = tmp_path / 'parent-root'
    result = run_claude_host('serve', '--project', str(project), cwd=project,
                             GRAFTLING_DATA_ROOT=str(root), GRAFTLING_CONNECTION='/owner')
    assert result.returncode == 0, result.stderr
    assert not root.exists() and not (project / '.agents-control' / 'claude-host.json').exists()
    assert 'authority-denied' in (project / '.agents-control' / 'host.log').read_text()


MOD = r'''
const {register} = await import(process.argv[1]);
const handlers = new Map();
register((name, matcher, fn) => { handlers.set(fn && matcher.tool ? `${name}:${matcher.tool}` : name, fn ?? matcher);
  return {catch() {}}; });
const env = JSON.parse(process.argv[2]);
const runs = [];
const host = {
 session: {id: async () => 'main', cwd: async () => '/project/sub'},
 command: {register: async () => {}}, tool: {list: async () => []}, env: {get: async key => env[key]},
 process: {run: async argv => { runs.push(argv); return {exitCode: 0, stdout: '{}', stderr: ''}; }},
 clock: {setTimeout: () => {}}, ui: {log: () => {}},
};
await handlers.get('session.start')(host, {}, async () => 'started');
await handlers.get('tool.check:mcp__plugin_graftling_subagents__subagents')(host, {input: {action: 'discover'}},
  async () => ({decision: 'allow'}));
console.log(JSON.stringify(runs[0]));
'''


@pytest.mark.parametrize('environment, extra', [({}, []), ({'GRAFTLING_DATA_ROOT': '/data'}, ['--data-root', '/data'])])
def test_claude_plugin_mod_passes_the_variable_to_context(tmp_path: Path, environment: dict, extra: list) -> None:
    """The mod hands context the GRAFTLING_DATA_ROOT it reads; without it the command is unchanged."""
    if not shutil.which('node'):
        pytest.skip('Node is required')
    module = tmp_path / 'host_context.mjs'
    module.write_text(marketplace_module())
    result = subprocess.run(['node', '--input-type=module', '-e', MOD, module.as_uri(),
                             json.dumps({'GRAFTLING_PYTHON': '/venv/python', **environment})],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['/venv/python', '-m', 'graftling.claude.host', 'context',
                                         '--project-from', '/project/sub', *extra]


def test_claude_launcher_logs_into_the_variable(tmp_path: Path) -> None:
    """A Python that cannot run graftling: the launcher's host.log line goes to GRAFTLING_DATA_ROOT."""
    project = tmp_path / 'project'
    project.mkdir()
    root = tmp_path / 'chosen'
    result = subprocess.run(['sh', str(MARKETPLACE_DIRECTORY / 'bin' / 'graftling-host'), 'serve', '--project',
                             str(project)], env={**os.environ, 'GRAFTLING_PYTHON': str(tmp_path / 'missing'),
                                                 'GRAFTLING_DATA_ROOT': str(root)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert 'the subagents host did not start' in (root / 'host.log').read_text()
    assert not (project / '.agents-control').exists()


def test_claude_default_is_unchanged_without_the_variable(tmp_path: Path, claude_on_path: Path) -> None:
    """Without GRAFTLING_DATA_ROOT the plugin's configuration stays <project>/.agents-control/claude-host.json."""
    project = tmp_path / 'project'
    project.mkdir()
    assert claude_host.plugin_configuration(project) == project.resolve() / '.agents-control' / 'claude-host.json'
