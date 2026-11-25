"""Pi works once installed: default data root and the extension's own Python (572)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

from graftling import AgentControl, Caller, bind
from graftling.pi import host
from posix_only import SHEBANG, require_posix
from test_pi_standalone_host import SOURCE, Fake, fakes, host_environment  # noqa: F401 - pytest fixture

EXTENSION = SOURCE / 'graftling/pi/extension.mjs'
SITE = 'lib/python3.13/site-packages'

# Drive registerAgents.start once, as Pi's session_start does; print what it did.
DRIVER = r'''
import { pathToFileURL } from 'node:url';
const module = await import(pathToFileURL(process.argv[1]).href);
const servers = [];
const pi = {
  registerFlag() {}, on() {}, getFlag: name => name === 'agents-config' ? process.env.TEST_CONFIG : undefined,
  registerMcpServer: (name, options) => servers.push({name, command: options.command}),
};
// A Main has a persistent Session file; TEST_SESSION_FILE='' is a `pi --no-session` Session (undefined, 585).
const file = process.env.TEST_SESSION_FILE ?? '/sessions/pi-main.jsonl';
const ctx = {hasUI: false, cwd: process.env.TEST_CWD ?? process.cwd(), sessionManager: {
  getSessionId: () => 'pi-main', getSessionFile: () => file || undefined, getHeader: () => ({id: 'pi-main'}), getEntries: () => []}};
const owner = module.registerAgents(pi);
let started, error;
try { started = await owner.start(ctx); } catch (failure) { error = failure.message; }
await owner.close();
console.log(JSON.stringify({started, error, servers}));
'''

# A host stand-in: its argv and cwd go to TEST_LOG; with -c it answers the import check.
FAKE_PYTHON = '''
import json, os, sys
if sys.argv[1:3] == ['-I', '-c']:
    print(os.environ.get('TEST_IMPORTS', ''))
    raise SystemExit(0 if os.environ.get('TEST_IMPORTS') else 1)
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(json.dumps({'python': sys.argv[0], 'argv': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')
sys.stdin.readline()
print(json.dumps({'connection': '/fake/address'}), flush=True)
sys.stdin.read()
'''


def installed(tmp_path: Path, with_python: bool = True) -> tuple[Path, Path]:
    """Lay graftling's Pi package out as pip installs it; return the extension and the prefix's python."""
    package = tmp_path / 'prefix' / SITE / 'graftling'
    (package / 'pi').mkdir(parents=True)
    (package / '__init__.py').write_text('')
    shutil.copy(EXTENSION, package / 'pi' / 'extension.mjs')
    python = tmp_path / 'prefix' / 'bin' / 'python3.13'
    if with_python:
        fake(python)
    return package / 'pi' / 'extension.mjs', python


def fake(path: Path) -> Path:
    """Write an executable host stand-in at ``path``."""
    require_posix(SHEBANG)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!' + sys.executable + '\n' + FAKE_PYTHON)
    path.chmod(0o755)
    return path


def start(tmp_path: Path, extension: Path = EXTENSION, imports: Path | None = None, **environment: str) -> tuple[dict, list]:
    """Run one session_start of ``extension`` in ``tmp_path/project``; return its outcome and host launches."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required')
    project = tmp_path / 'project'
    project.mkdir(exist_ok=True)
    log = tmp_path / 'launches.jsonl'
    env = {key: value for key, value in os.environ.items() if key not in {
        'GRAFTLING_CONNECTION', 'GRAFTLING_PYTHON', 'DSH_SESSION_ID', 'DSH_AGENT_SOURCE',
        'GRAFTLING_DATA_ROOT', 'PI_SUBAGENT_CHILD', 'PI_SUBAGENT_DEPTH'}}
    env.update(PATH=str(tmp_path / 'empty-path'), TEST_LOG=str(log),
               TEST_IMPORTS=str((imports or extension.parents[1] / '__init__.py').resolve()), **environment)
    result = subprocess.run([node, '--input-type=module', '-e', DRIVER, str(extension)],
                            cwd=project, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    launches = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return json.loads(result.stdout), launches


def test_without_flag_or_variable_the_installed_python_hosts_the_project_default(tmp_path: Path) -> None:
    """No --agents-config and no GRAFTLING_PYTHON: the installation's python starts the host without --config."""
    extension, python = installed(tmp_path)
    found, launches = start(tmp_path, extension)
    assert found == {'started': True, 'servers': [{'name': 'graftling', 'command': str(python)}]}
    assert launches == [{'python': str(python), 'argv': ['-I', '-m', 'graftling.pi.host'],
                         'cwd': str((tmp_path / 'project').resolve())}]


def test_without_an_installation_python_the_error_names_the_variable(tmp_path: Path) -> None:
    """A layout with no interpreter that imports this graftling is refused with the fix; nothing starts."""
    extension, _ = installed(tmp_path, with_python=False)
    found, launches = start(tmp_path, extension)
    assert 'started' not in found and found['servers'] == [] and launches == []
    assert 'Set GRAFTLING_PYTHON' in found['error'] and 'no Python that imports this graftling' in found['error']


def test_a_python_importing_another_graftling_is_not_used(tmp_path: Path) -> None:
    """The candidate must import graftling from this very installation."""
    extension, _ = installed(tmp_path)
    found, launches = start(tmp_path, extension, imports=tmp_path / 'elsewhere/graftling/__init__.py')
    assert 'Set GRAFTLING_PYTHON' in found['error'] and launches == []


def test_an_editable_checkout_finds_python_on_path(tmp_path: Path) -> None:
    """A source checkout has no site-packages layout: python3 on PATH that imports it is used."""
    checkout = tmp_path / 'checkout/src/graftling'
    (checkout / 'pi').mkdir(parents=True)
    (checkout / '__init__.py').write_text('')
    shutil.copy(EXTENSION, checkout / 'pi' / 'extension.mjs')
    fake(tmp_path / 'empty-path' / 'python3')
    found, launches = start(tmp_path, checkout / 'pi' / 'extension.mjs')
    assert found['started'] is True and launches[0]['python'].endswith('empty-path/python3')


def test_the_variable_wins_over_the_installation(tmp_path: Path) -> None:
    """GRAFTLING_PYTHON set is used as is, with no lookup."""
    extension, _ = installed(tmp_path)
    chosen = fake(tmp_path / 'chosen' / 'python')
    found, launches = start(tmp_path, extension, GRAFTLING_PYTHON=str(chosen))
    assert found['servers'] == [{'name': 'graftling', 'command': str(chosen)}]
    assert [launch['python'] for launch in launches] == [str(chosen)]


def test_the_flag_wins_over_the_default_root(tmp_path: Path) -> None:
    """--agents-config is passed to the host as before."""
    extension, python = installed(tmp_path)
    found, launches = start(tmp_path, extension, TEST_CONFIG='/conf/pi-host.json')
    assert found['started'] is True
    assert launches[0]['argv'] == ['-I', '-m', 'graftling.pi.host', '--config', '/conf/pi-host.json']


@pytest.mark.parametrize('cwd', ['relative/project', '/no/such/project', 'FILE'])
def test_an_unusable_session_directory_is_refused_naming_the_flag(tmp_path: Path, cwd: str) -> None:
    """No default root without a usable project directory; nothing starts."""
    extension, _ = installed(tmp_path)
    if cwd == 'FILE':
        cwd = str(tmp_path / 'a-file')
        Path(cwd).write_text('')
    found, launches = start(tmp_path, extension, TEST_CWD=cwd)
    assert '--agents-config' in found['error'] and launches == [] and found['servers'] == []


@pytest.mark.parametrize('name', ['DSH_SESSION_ID', 'DSH_AGENT_SOURCE'])
def test_a_dsh_managed_context_without_the_flag_stays_inert(tmp_path: Path, name: str) -> None:
    """As before 572: no host, no tool, no error; with the flag it is still refused."""
    extension, _ = installed(tmp_path)
    assert start(tmp_path, extension, **{name: 'x'}) == ({'started': False, 'servers': []}, [])
    found, _ = start(tmp_path, extension, TEST_CONFIG='/conf/pi-host.json', **{name: 'x'})
    assert 'cannot replace an existing managed Agent context' in found['error']


def test_a_child_keeps_its_owner_connection_and_python(tmp_path: Path) -> None:
    """Pi as a child: the owner's GRAFTLING_PYTHON serves MCP, no host starts and no lookup runs."""
    extension, _ = installed(tmp_path)
    found, launches = start(tmp_path, extension, GRAFTLING_CONNECTION='/owner/address', GRAFTLING_PYTHON='/owner/python')
    assert found == {'started': True, 'servers': [{'name': 'graftling', 'command': '/owner/python'}]}
    assert launches == []


def run_default_host(project: Path) -> tuple[subprocess.Popen, str]:
    """Start the real host without --config in ``project``, as the extension now does; close at once."""
    environment = host_environment(project)
    process = subprocess.Popen([sys.executable, '-m', 'graftling.pi.host'], cwd=project, env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    header = {'type': 'session', 'version': 3, 'id': 'pi-main', 'cwd': str(project)}
    process.stdin.write(json.dumps({'header': header, 'entries': []}) + '\n')
    process.stdin.flush()
    first = process.stdout.readline()
    process.stdin.close()
    process.wait(20)
    return process, first


def test_the_host_without_config_serves_the_project_default(tmp_path: Path) -> None:
    """The real host creates <project>/.agents-control and serves under its live/pi-host."""
    project = tmp_path.resolve()
    process, first = run_default_host(project)
    assert process.returncode == 0, process.stderr.read()
    assert json.loads(first)['connection'].startswith(str(project / '.agents-control' / 'live' / 'pi-host'))


def test_an_uncreatable_default_root_is_refused_naming_the_flag(tmp_path: Path) -> None:
    """A project where .agents-control cannot be made stops the host with the fix."""
    project = tmp_path.resolve()
    (project / '.agents-control').write_text('a file, not a directory')
    process, first = run_default_host(project)
    error = json.loads(process.stderr.read())['error']
    assert process.returncode == 1 and first == ''
    assert error['code'] == 'invalid-config' and '--agents-config' in error['message']


def test_default_configuration_is_the_root_only_form(tmp_path: Path) -> None:
    """The default equals {"root": <cwd>/.agents-control} and keeps an existing root's files."""
    assert host.default_configuration(tmp_path) == host.read_configuration({'root': str(tmp_path / '.agents-control')})
    (tmp_path / '.agents-control' / 'config.yml').write_text('connections: {}\n')
    host.default_configuration(tmp_path)
    assert (tmp_path / '.agents-control' / 'config.yml').read_text() == 'connections: {}\n'


def test_default_root_is_shared_with_the_claude_code_host(
        tmp_path: Path, fakes: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Pi Main shares <project>/.agents-control with a Claude Code Main (570's conclusion), concurrently.

    Each sees only its own children, every record is intact and the Claude host's files are untouched.
    """
    from graftling.claude import host as claude_host

    project = tmp_path.resolve()
    monkeypatch.chdir(project)
    for name, value in host_environment(project).items():
        monkeypatch.setenv(name, value)
    for name in ('GRAFTLING_CONNECTION', 'DSH_SESSION_ID', 'DSH_AGENT_SOURCE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(host, 'verify_native_parent', lambda entry: None)
    header = {'type': 'session', 'version': 3, 'id': 'pi-main', 'cwd': str(project)}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'header': header, 'entries': []}) + '\n'))
    monkeypatch.setattr(claude_host.shutil, 'which', lambda name: '/usr/local/bin/claude')
    config = claude_host.plugin_configuration(project)
    root = config.parent
    (root / 'host.log').write_text('claude host log\n')
    (root / 'live').mkdir()
    (root / 'live' / 'channel.json').write_text(json.dumps({'address': str(root / 'live' / 'control-x')}))
    before = {path: path.read_bytes() for path in (config, root / 'host.log', root / 'live' / 'channel.json')}

    found, notices, operate, _, _, _ = host.start(None)
    assert found == root
    claude_call = bind(AgentControl(root, {'claude': Fake('claude')}, request_notices=True, hold_notices=True),
                       Caller('claude-native', None, 'claude-session', 'claude'))
    made: dict[str, list] = {'pi': [], 'claude': []}

    def create(name: str, call, runtime: str) -> None:
        for _ in range(4):
            result = call({'action': 'create', 'runtime': runtime, 'instruction': 'hi'})
            assert not result.failed, result.document
            made[name].append(result.document['result']['agent'])

    threads = [threading.Thread(target=create, args=('pi', operate, 'claude')),
               threading.Thread(target=create, args=('claude', claude_call, 'claude'))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(made['pi']) == len(made['claude']) == 4

    def listed(call) -> set[str]:
        return {agent['agent'] for agent in call({'action': 'status'}).document['result']['agents']}

    assert listed(operate) == set(made['pi']) and listed(claude_call) == set(made['claude'])
    assert len(list((root / 'control' / 'agents').glob('*.json'))) == 8
    assert claude_call({'action': 'status', 'agent': made['pi'][0]}).failed
    assert operate({'action': 'status', 'agent': made['claude'][0]}).failed
    assert {path: path.read_bytes() for path in before} == before
    claude_host.require_free_channel(root / 'live' / 'channel.json')   # the Pi host is not "another host"
    assert claude_host.plugin_configuration(project) == config
    notices.close()
