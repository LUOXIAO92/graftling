"""648 (W10): the Pi host on Windows, and no Pi child there (Windows design decision 2).

- A Pi child is refused on Windows with its reason (srt's Windows support is
  alpha), before any selection, sandbox or launch work; POSIX is unchanged. The
  Windows branch runs on every host (the driver module's ``os`` patched).
- ``directPython`` of the Pi extension: unchanged off Windows; on Windows a venv's
  python is replaced by its base interpreter with ``__PYVENV_LAUNCHER__``.
- The Pi host through a stand-in Pi with the real process shape. The stand-in is
  ``pi.cmd``, as npm writes it, starting ``node <entry>`` on Windows, and a ``#!``
  entry on POSIX. It loads the extension as Pi does, starts one Session, and calls
  the registered MCP server as Pi's MCP client would: one call over the host's
  channel (the named pipe on Windows), with the caller checked as a direct child
  of Pi. Then it ends the Session. The MCP server ends at its stdin's EOF with
  exit 0, and the host ends at its stdin's EOF with exit 0, with no signal (M18).
- Windows only: the host and the MCP server started from a venv. A venv's
  ``python.exe`` really starts its interpreter as a child, but the extension starts
  both as direct children of Pi, running as that venv.

How the real Pi passes ``env`` to an MCP server, and that it closes the server's
stdin at the Session's end, are inferences for the real Windows run (Task 650).
The stand-in passes only the MCP SDK's default variables plus the server's ``env``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from graftling import AgentControl, AgentsError, Caller, bind
from graftling.pi import driver as pi_driver
from graftling.pi.driver import WINDOWS_CHILD_REFUSAL, PiDriver
from graftling.pi.host import pi_child_driver

SOURCE = Path(__file__).resolve().parents[1] / 'src'
WINDOWS = sys.platform == 'win32'
MAIN = Caller('pi-native', None, session='pi-main', runtime='pi')
windows_only = pytest.mark.skipif(not WINDOWS, reason='Windows only: a venv launcher and a .cmd shim')
PLATFORMS = {'posix': 'posix', 'windows': 'nt'}


@pytest.fixture(params=sorted(PLATFORMS))
def platform(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run the test as on POSIX and as on Windows: the Pi driver module sees that ``os.name``."""
    monkeypatch.setattr(pi_driver, 'os', SimpleNamespace(name=PLATFORMS[request.param], environ=os.environ))
    return request.param


def node() -> str:
    """The Node that runs the extension; it is on the Windows runner and on macOS."""
    found = shutil.which('node')
    if not found:
        pytest.skip('Node is required')
    return found


# --- No Pi child on Windows ----------------------------------------------------------------

def test_require_platform_refuses_only_windows(platform: str) -> None:
    """Windows names srt and its alpha state; POSIX passes."""
    if platform == 'posix':
        assert pi_driver.require_platform() is None
        assert PiDriver({}).require_platform() is None
        return
    with pytest.raises(AgentsError) as refused:
        PiDriver({}).require_platform()
    assert refused.value.code == 'unsupported-runtime'
    assert refused.value.message == WINDOWS_CHILD_REFUSAL
    for part in ('Windows', 'srt', 'alpha', 'The Pi host itself works on Windows'):
        assert part in WINDOWS_CHILD_REFUSAL


def test_a_pi_child_create_is_refused_on_windows_before_selection_or_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create fails with the reason; nothing is selected, prepared, started or recorded."""
    monkeypatch.setattr(pi_driver, 'os', SimpleNamespace(name='nt', environ=os.environ))
    touched = []
    driver = PiDriver({'worktree_path': str(tmp_path), 'provider': 'p', 'model': 'm'})
    driver.prepare = lambda *arguments, **options: touched.append('prepare')
    driver.start = lambda run: touched.append('start')
    control = AgentControl(tmp_path / 'data', {'pi': driver})
    monkeypatch.setattr(control, 'select', lambda *arguments, **options: touched.append('select'))
    call = bind(control, MAIN)
    result = call({'action': 'create', 'runtime': 'pi', 'instruction': 'work', 'model': 'another'})
    assert result.failed
    assert result.document['error'] == {'code': 'unsupported-runtime', 'message': WINDOWS_CHILD_REFUSAL}
    assert touched == []
    listed = call({'action': 'status'})
    assert not listed.failed and listed.document['result']['agents'] == []


def test_a_pi_child_driver_of_another_host_is_refused_on_windows_first(platform: str, tmp_path: Path) -> None:
    """The Claude and Codex hosts' Pi driver: Windows refuses before any setting or dependency check."""
    with pytest.raises(AgentsError) as refused:
        pi_child_driver({}, tmp_path / 'project', tmp_path / 'data')
    if platform == 'windows':
        assert (refused.value.code, refused.value.message) == ('unsupported-runtime', WINDOWS_CHILD_REFUSAL)
    else:
        assert refused.value.code == 'invalid-config' and 'runtimes.pi.provider is required' in refused.value.message


# --- directPython ----------------------------------------------------------------------------

DIRECT = '''
import { pathToFileURL } from 'node:url';
const {directPython} = await import(pathToFileURL(process.argv[1]).href);
console.log(JSON.stringify([directPython(process.argv[2], process.env, 'linux'),
                            directPython(process.argv[3], process.env, 'win32')]));
'''


def test_direct_python_changes_only_a_windows_venv_python(tmp_path: Path) -> None:
    """Off Windows nothing is probed or changed; on Windows a venv python becomes its base interpreter."""
    result = subprocess.run([node(), '--input-type=module', '-e', DIRECT, str(SOURCE / 'graftling/pi/extension.mjs'),
                             str(tmp_path / 'not-run'), sys.executable],
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    posix, windows = json.loads(result.stdout)
    assert posix == {'command': str(tmp_path / 'not-run'), 'env': {}}
    if os.path.normcase(sys.executable) == os.path.normcase(sys._base_executable):
        assert windows == {'command': sys.executable, 'env': {}}
    else:
        assert windows == {'command': sys._base_executable, 'env': {'__PYVENV_LAUNCHER__': sys.executable}}


# --- The Pi host through a stand-in Pi -------------------------------------------------------

STAND_IN = r'''
// Stand-in Pi CLI (648): loads graftling's Pi extension as Pi does, starts one Session, calls the
// registered MCP server as Pi's MCP client would, then shuts the Session down.
import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';

process.title = 'pi';  // as Pi's setupCli does
const report = {node_pid: process.pid, node: process.execPath, entry: process.argv[1]};
// The MCP SDK's stdio client passes only these variables, plus the server's own env.
const INHERITED = process.platform === 'win32'
  ? ['APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'LOCALAPPDATA', 'PATH', 'PROCESSOR_ARCHITECTURE', 'SYSTEMDRIVE',
     'SYSTEMROOT', 'TEMP', 'USERNAME', 'USERPROFILE', 'PROGRAMFILES']
  : ['HOME', 'LOGNAME', 'PATH', 'SHELL', 'TERM', 'USER'];

async function mcpSession(server, calls) {
  const env = {};
  for (const name of INHERITED) if (process.env[name] !== undefined) env[name] = process.env[name];
  const child = spawn(server.command, server.args,
    {cwd: server.cwd, env: {...env, ...server.env}, stdio: ['pipe', 'pipe', 'inherit']});
  report.mcp_pid = child.pid;
  const exited = new Promise(done => child.on('exit', (code, signal) => done({code, signal})));
  const pending = new Map();
  createInterface({input: child.stdout}).on('line', line => {
    const message = JSON.parse(line);
    pending.get(message.id)?.(message);
  });
  let next = 0;
  const ask = (method, params) => new Promise(done => {
    const id = ++next;
    pending.set(id, done);
    child.stdin.write(JSON.stringify({jsonrpc: '2.0', id, method, params}) + '\n');
  });
  await ask('initialize', {protocolVersion: '2025-06-18', capabilities: {}, clientInfo: {name: 'pi-stand-in', version: '0'}});
  child.stdin.write(JSON.stringify({jsonrpc: '2.0', method: 'notifications/initialized'}) + '\n');
  const answers = [];
  for (const call of calls) answers.push((await ask('tools/call', {name: 'subagents', arguments: call})).result);
  child.stdin.end();  // the Session ends: its MCP server's stdin is closed
  report.mcp_exit = await exited;
  return answers;
}

try {
  const handlers = {}, servers = [];
  const pi = {registerFlag() {}, getFlag: () => undefined, on: (name, handler) => { handlers[name] = handler; },
    registerMcpServer: (name, config) => { servers.push({name, ...config}); },
    registerCommand() {}, sendMessage() {}, appendEntry() {}};
  const file = process.env.TEST_SESSION_FILE;
  const header = JSON.parse(readFileSync(file, 'utf8').split('\n')[0]);
  const ctx = {hasUI: false, cwd: process.cwd(), model: {id: 'parent-model', provider: 'anthropic'},
    sessionManager: {getSessionId: () => header.id, getSessionFile: () => file, getHeader: () => header,
                     getEntries: () => []}};
  (await import(pathToFileURL(process.env.TEST_EXTENSION).href)).default(pi);
  await handlers.session_start({type: 'session_start'}, ctx);
  report.servers = servers.map(({name, command, args, env}) => ({name, command, args, env}));
  report.answers = await mcpSession(servers[0], JSON.parse(process.env.TEST_CALLS));
  // Pi's session_shutdown: the extension closes the host's stdin and waits for its exit 0.
  await handlers.session_shutdown({type: 'session_shutdown'}, ctx);
  report.closed = true;
} catch (error) {
  report.error = String(error?.stack || error);
}
writeFileSync(process.env.TEST_OUT, JSON.stringify(report));
'''

PACKAGE = 'node_modules/@earendil-works/pi-coding-agent'
STATUS = {'action': 'execute', 'feature': 'status', 'arguments': {}}
CREATE_PI = {'action': 'execute', 'feature': 'create', 'arguments': {'runtime': 'pi', 'instruction': 'hold'}}


def stand_in_pi(directory: Path) -> Path:
    """Install the stand-in Pi as npm lays it out; return the command a user types.

    Windows: ``pi.cmd`` starts ``node`` with the entry, as npm's shim does (the doubled
    backslash after ``%~dp0`` included); ASCII only, so cmd.exe reads it under a
    non-ASCII directory. POSIX: ``pi`` links to the ``#!`` entry, as npm links it.
    """
    entry = directory / PACKAGE / 'dist/cli.js'
    entry.parent.mkdir(parents=True)
    (directory / PACKAGE / 'package.json').write_text(json.dumps(
        {'name': '@earendil-works/pi-coding-agent', 'type': 'module'}), encoding='utf-8')
    if WINDOWS:
        entry.write_text(STAND_IN, encoding='utf-8')
        shim = directory / 'pi.cmd'
        shim.write_text('@ECHO off\r\nSETLOCAL\r\n"%TEST_NODE%"  "%~dp0\\' + PACKAGE.replace('/', '\\')
                        + '\\dist\\cli.js" %*\r\n', encoding='ascii')
        return shim
    entry.write_text('#!' + node() + '\n' + STAND_IN, encoding='utf-8')
    entry.chmod(0o755)
    shim = directory / 'bin/pi'
    shim.parent.mkdir()
    shim.symlink_to(entry)
    return shim


def run_pi(tmp_path: Path, extension: Path, calls: list[dict], **environment: str) -> dict:
    """Run one Session of the stand-in Pi in ``tmp_path/project``; return its report."""
    project = tmp_path / 'project'
    project.mkdir()
    session = tmp_path / 'sessions' / 'pi-main-648.jsonl'
    session.parent.mkdir()
    session.write_text(json.dumps({'type': 'session', 'version': 3, 'id': 'pi-main-648', 'cwd': str(project)})
                       + '\n', encoding='utf-8')
    shim = stand_in_pi(tmp_path / 'npm')
    env = {key: value for key, value in os.environ.items() if key not in {
        'GRAFTLING_CONNECTION', 'GRAFTLING_PYTHON', 'GRAFTLING_DATA_ROOT', 'DSH_SESSION_ID', 'DSH_AGENT_SOURCE',
        'PI_SUBAGENT_CHILD', 'PI_SUBAGENT_DEPTH', 'GRAFTLING_PI_REVIEW'}}
    env.update(TEST_NODE=node(), TEST_EXTENSION=str(extension), TEST_SESSION_FILE=str(session),
               TEST_CALLS=json.dumps(calls), TEST_OUT=str(tmp_path / 'report.json'),
               GRAFTLING_DATA_ROOT=str(tmp_path / 'data'), **environment)
    result = subprocess.run([str(shim)], cwd=project, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=180)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / 'report.json').read_text(encoding='utf-8'))
    assert 'error' not in report, (report, result.stderr)
    return report


def check_session(report: dict, python: str, launcher: dict) -> None:
    """The registered server, its calls, and the Session's end at stdin EOF on both sides."""
    [server] = report['servers']
    assert server['command'] == python and server['args'] == ['-I', '-m', 'graftling.mcp']
    assert set(server['env']) == {'GRAFTLING_CONNECTION', *launcher}
    assert all(server['env'][name] == value for name, value in launcher.items())
    status = report['answers'][0]
    # The MCP server reached the host and passed its direct-child check.
    assert not status['isError'], status
    assert status['structuredContent']['result']['agents'] == []
    if WINDOWS:
        refused = report['answers'][1]
        assert refused['isError'] and refused['structuredContent']['error'] == {
            'code': 'unsupported-runtime', 'message': WINDOWS_CHILD_REFUSAL}
    # Both ended on their own at stdin EOF: the MCP server, then the host (close() rejects a non-zero exit).
    assert report['mcp_exit'] == {'code': 0, 'signal': None}
    assert report['closed'] is True
    # Pi's entry as node reports it: the shim's target, which the host checked against node's arguments.
    assert Path(report['entry']).resolve().parts[-4:] == ('@earendil-works', 'pi-coding-agent', 'dist', 'cli.js')


def test_pi_host_through_the_shim_serves_an_mcp_call_and_ends_at_stdin_eof(tmp_path: Path) -> None:
    """Pi (a shim, node, the extension) starts the host; one MCP call; the Session ends with exit 0."""
    calls = [STATUS, CREATE_PI] if WINDOWS else [STATUS]
    report = run_pi(tmp_path, SOURCE / 'graftling/pi/extension.mjs', calls, GRAFTLING_PYTHON=sys.executable)
    launcher = ({} if not WINDOWS or os.path.normcase(sys.executable) == os.path.normcase(sys._base_executable)
                else {'__PYVENV_LAUNCHER__': sys.executable})
    check_session(report, sys._base_executable if launcher else sys.executable, launcher)


# Appends one line per Python start in the venv; only a process running as the venv reads this file.
RECORD = ("import json, os, sys; open(os.path.join(sys.prefix, 'starts-648.jsonl'), 'a', encoding='utf-8').write("
          "json.dumps({'pid': os.getpid(), 'ppid': os.getppid(), 'prefix': sys.prefix, "
          "'executable': sys.executable, 'argv': sys.orig_argv}) + '\\n')\n")


@windows_only
def test_pi_host_from_a_venv_runs_as_a_direct_child_of_pi(tmp_path: Path) -> None:
    """The venv's python.exe starts a child; the extension starts the host and MCP server under node itself."""
    venv = tmp_path / 'venv'
    subprocess.run([sys._base_executable, '-m', 'venv', '--system-site-packages', '--without-pip', str(venv)],
                   check=True, capture_output=True, timeout=180)
    site = venv / 'Lib' / 'site-packages'
    # graftling installed into the venv (not editable), as a tool install lays it out.
    shutil.copytree(SOURCE / 'graftling', site / 'graftling',
                    ignore=shutil.ignore_patterns('__pycache__', 'install', 'PUBLIC_API.md'))
    (site / 'zz_record_648.pth').write_text(RECORD, encoding='utf-8')
    python = venv / 'Scripts' / 'python.exe'
    records = venv / 'starts-648.jsonl'

    def started(word: str) -> list[dict]:
        lines = records.read_text(encoding='utf-8').splitlines() if records.exists() else []
        return [item for item in map(json.loads, lines) if word in item['argv']]

    # The shape: the venv's python.exe is a launcher whose child runs the venv.
    shape = subprocess.Popen([str(python), '-I', '-c', 'pass'])
    assert shape.wait(60) == 0
    [record] = started('pass')
    assert record['ppid'] == shape.pid != record['pid'], record
    assert os.path.normcase(record['prefix']) == os.path.normcase(str(venv))

    # No GRAFTLING_PYTHON: the extension finds the venv's Scripts\python.exe from its own location.
    report = run_pi(tmp_path, site / 'graftling/pi/extension.mjs', [STATUS, CREATE_PI])
    base = report['servers'][0]['command']
    assert os.path.normcase(base) != os.path.normcase(str(python))
    check_session(report, base, {'__PYVENV_LAUNCHER__': str(python)})
    [host] = started('graftling.pi.host')
    [mcp] = started('graftling.mcp')
    for item in (host, mcp):
        # A direct child of Pi's node, running as the venv, with the extension's flags.
        assert item['ppid'] == report['node_pid'], (item, report['node_pid'])
        assert os.path.normcase(item['prefix']) == os.path.normcase(str(venv)), item
        assert item['argv'][1] == '-I', item
    assert mcp['pid'] == report['mcp_pid']
