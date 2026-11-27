"""647 (W8 + W9): the Claude Code host on Windows, and no Claude child there.

- W9: creating, resuming or sending to a Claude child on native Windows is refused
  (``unsupported-runtime``) before any setting is built or process started; no
  switch allows it (decision 1). Natively on Windows, simulated everywhere.
- The plugin that ``configuration --write`` generates is the Windows way to host:
  in a venv its ``.mcp.json`` starts the venv's base interpreter with
  ``__PYVENV_LAUNCHER__`` (CPython's venv launcher would otherwise sit between
  Claude Code and the host), and its mod starts the context call the same way.
  A stand-in Claude Code (a real Python executable, so the host's parent check
  compares a real image path) starts that host, which serves one MCP call whose
  per-call context, sent by a direct child of the stand-in, passes the caller
  check over the channel (the named pipe on Windows); closing the host's stdin,
  without a signal, ends the Session and the host exits 0 (M18).
- A host whose parent is not the selected Claude Code is refused with the facts.
- The marketplace plugin's sh launcher refuses under a Windows sh, without
  starting Python, and names configuration --write.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import site
import subprocess
import sys
import types
import uuid

import pytest

from graftling import AgentControl, AgentsError, Caller, bind
from graftling.claude import driver as claude_driver
from graftling.claude.driver import ClaudeDriver
from graftling.claude.plugin import MARKETPLACE_DIRECTORY, marketplace_module

WINDOWS = sys.platform == 'win32'
SOURCE = Path(__file__).resolve().parents[1] / 'src'
CALLER = Caller('test-host', None, 'main-session', 'claude')
LAUNCHER = MARKETPLACE_DIRECTORY / 'bin' / 'graftling-host'


def clean_environment() -> dict:
    """This environment without a parent's channel, data root or venv launcher variable."""
    return {key: value for key, value in os.environ.items()
            if key not in {'GRAFTLING_CONNECTION', 'GRAFTLING_DATA_ROOT', '__PYVENV_LAUNCHER__'}}


# --- W9 ------------------------------------------------------------------------------------

@pytest.mark.parametrize('native', [True, False], ids=['native', 'simulated'])
def test_claude_child_is_refused_on_native_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: bool,
) -> None:
    """create, and a resume or send (driver start), are refused with the reason; nothing is recorded."""
    if native and not WINDOWS:
        pytest.skip('Windows only: the native refusal')
    if not native:
        monkeypatch.setattr(claude_driver, 'sys', types.SimpleNamespace(platform='win32'))
    root = tmp_path / 'root'
    root.mkdir()
    # An executable that does not exist: nothing may try to start it.
    driver = ClaudeDriver(tmp_path, {'executable': str(tmp_path / 'never' / 'claude')})
    result = bind(AgentControl(root, {'claude': driver}), CALLER)(
        {'action': 'create', 'runtime': 'claude', 'instruction': 'Work.'})
    assert result.failed
    error = result.document['error']
    assert error['code'] == 'unsupported-runtime'
    assert 'native Windows' in error['message'] and 'sandbox' in error['message']
    assert not (root / 'control' / 'agents').exists()
    with pytest.raises(AgentsError) as refused:
        driver.start(None)
    assert refused.value.code == 'unsupported-runtime'


# --- The marketplace plugin's launcher under a Windows sh --------------------------------------

def test_marketplace_launcher_refuses_under_a_windows_sh(tmp_path: Path) -> None:
    """One line on stderr and in host.log names configuration --write; Python is never started.

    On Windows the runner's Git sh is used as it is; elsewhere ``uname`` is a
    stand-in that reports MINGW64, and GRAFTLING_PYTHON one that leaves a mark.
    """
    project = tmp_path / 'project'
    project.mkdir()
    environment = clean_environment()
    started = tmp_path / 'python-started'
    if WINDOWS:
        environment['GRAFTLING_PYTHON'] = str(tmp_path / 'missing' / 'python.exe')
    else:
        stubs = tmp_path / 'stubs'
        stubs.mkdir()
        (stubs / 'uname').write_text('#!/bin/sh\necho MINGW64_NT-10.0-20348\n')
        (stubs / 'python').write_text(f'#!/bin/sh\ntouch "{started}"\n')
        for stub in stubs.iterdir():
            stub.chmod(0o755)
        environment.update(PATH=f'{stubs}{os.pathsep}{environment.get("PATH", "")}',
                           GRAFTLING_PYTHON=str(stubs / 'python'))
    result = subprocess.run(['sh', str(LAUNCHER), 'serve', '--project', str(project)], env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    [line] = result.stderr.splitlines()
    assert 'not supported on native Windows' in line and 'configuration --write' in line
    assert line in (project / '.agents-control' / 'host.log').read_text(encoding='utf-8')
    assert not started.exists()


# --- The mod's context call for a Windows venv ------------------------------------------------

MOD = r'''
const {register} = await import(process.argv[1]);
const fs = await import('node:fs');
const handlers = new Map();
register((name, matcher, fn) => { handlers.set(fn && matcher.tool ? `${name}:${matcher.tool}` : name, fn ?? matcher);
  return {catch() {}}; });
const env = JSON.parse(process.argv[2]);
const runs = [];
const host = {
 session: {id: async () => 'main', cwd: async () => process.argv[4]},
 command: {register: async () => {}}, tool: {list: async () => []}, env: {get: async key => env[key]},
 fs: {exists: async path => fs.existsSync(path), read: async path => fs.readFileSync(path, 'utf8')},
 process: {run: async argv => { runs.push(argv); return {exitCode: 0, stdout: '{}', stderr: ''}; }},
 clock: {setTimeout: () => {}}, ui: {log: () => {}},
};
await handlers.get('session.start')(host, {}, async () => 'started');
const decided = await handlers.get(`tool.check:${process.argv[3]}`)(host, {input: {action: 'discover'}},
  async () => ({decision: 'allow'}));
console.log(JSON.stringify({run: runs[0] ?? null, decided}));
'''


def mod_context(module: Path, environment: dict, tool: str, cwd: Path) -> dict:
    """Run ``module``'s permission check once with ``environment``; return its context argv and decision."""
    if not shutil.which('node'):
        pytest.skip('Node is required')
    result = subprocess.run(['node', '--input-type=module', '-e', MOD, module.as_uri(), json.dumps(environment),
                             tool, str(cwd)], capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_mod_starts_a_windows_venvs_base_interpreter(tmp_path: Path) -> None:
    """Windows and a venv: base python.exe, -I, the bootstrap and the venv; otherwise unchanged."""
    venv, base = tmp_path / 'venv', tmp_path / 'base'
    (venv / 'Scripts').mkdir(parents=True)
    base.mkdir()
    python = venv / 'Scripts' / 'python.exe'
    python.write_text('')
    (base / 'python.exe').write_text('')
    (venv / 'pyvenv.cfg').write_text(f'home = {base}\ninclude-system-site-packages = false\n')
    module = tmp_path / 'host_context.mjs'
    module.write_text(marketplace_module(), encoding='utf-8')
    tool = 'mcp__plugin_graftling_subagents__subagents'

    found = mod_context(module, {'GRAFTLING_PYTHON': str(python), 'OS': 'Windows_NT'}, tool, tmp_path)
    assert found['decided'] == {'decision': 'allow'}
    assert found['run'][:3] == [str(base / 'python.exe'), '-I', '-c'] and found['run'][4] == str(venv)
    assert found['run'][5:] == ['context', '--project-from', str(tmp_path)]

    # Not Windows, or a python with no pyvenv.cfg beside its directory: the command as before.
    for environment in ({'GRAFTLING_PYTHON': str(python)},
                        {'GRAFTLING_PYTHON': str(tmp_path / 'plain' / 'python.exe'), 'OS': 'Windows_NT'}):
        found = mod_context(module, environment, tool, tmp_path)
        assert found['run'] == [environment['GRAFTLING_PYTHON'], '-m', 'graftling.claude.host', 'context',
                                '--project-from', str(tmp_path)]

    # A venv whose base interpreter is missing: one line, and no process.
    (base / 'python.exe').unlink()
    found = mod_context(module, {'GRAFTLING_PYTHON': str(python), 'OS': 'Windows_NT'}, tool, tmp_path)
    assert found['run'] is None and found['decided']['decision'] == 'deny'
    assert 'pyvenv.cfg names no base interpreter' in found['decided']['reason']
    assert '\n' not in found['decided']['reason']


# --- The host's direct parent ----------------------------------------------------------------

def other_executable() -> Path:
    """A real executable that is not this Python."""
    if WINDOWS:
        return Path(os.environ['SystemRoot'], 'System32', 'cmd.exe').resolve()
    return Path('/bin/sh').resolve()


def test_host_refuses_a_parent_that_is_not_the_selected_claude(tmp_path: Path) -> None:
    """Started by this test, not by the configured Claude Code: refused with the facts, no channel."""
    project = tmp_path / 'project'
    project.mkdir()
    config = project / '.agents-control' / 'claude-host.json'
    config.parent.mkdir()
    channel = config.parent / 'live' / 'channel.json'
    config.write_text(json.dumps({'root': str(config.parent), 'cwd': str(project), 'claude': str(other_executable()),
                                  'channel_file': str(channel), 'settings': {}}), encoding='utf-8')
    host = subprocess.run([sys.executable, '-m', 'graftling.claude.host', 'serve', '--config', str(config)],
                          cwd=project, env=clean_environment(), input='', capture_output=True, text=True,
                          encoding='utf-8', timeout=60)
    assert host.returncode == 0
    facts = json.loads(re.search(r'\{.*\}', host.stderr).group(0))
    assert 'launched directly by the selected Claude CLI' in host.stderr
    assert facts['parent_pid'] == os.getpid() and facts['stage'] == 'serve'
    assert Path(facts['expected_executable']) == other_executable()
    assert not channel.exists()


# --- The generated plugin's host, end to end ------------------------------------------------

STANDIN = r'''
import json, os, subprocess, sys, time, uuid
from pathlib import Path
server, context_argv = json.loads(sys.argv[1]), json.loads(sys.argv[2])
project, out = Path(sys.argv[3]), Path(sys.argv[4])
# As Claude Code starts a stdio server: command, args, env over this environment, cwd the project.
host = subprocess.Popen([server['command'], *server['args']], cwd=project, env={**os.environ, **server.get('env', {})},
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8')
def rpc(message):
    host.stdin.write(json.dumps(message) + '\n')
    host.stdin.flush()
    return json.loads(host.stdout.readline()) if 'id' in message else None
report = {'standin': os.getpid(), 'host': host.pid}
report['initialize'] = rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
    'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'stand-in', 'version': '0'}}})
rpc({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
channel = project / '.agents-control' / 'live' / 'channel.json'
deadline = time.monotonic() + 60
while not channel.exists() and time.monotonic() < deadline:
    time.sleep(0.1)
report['channel'] = json.loads(channel.read_text(encoding='utf-8'))
# What the mod does at the permission check: the per-call context, from a direct child of this process.
request = {'action': 'discover'}
document = {'operation': 'prepare', 'tool': 'subagents', 'session': str(uuid.uuid4()), 'cwd': str(project),
            'agent': None, 'request': request}
context = subprocess.run(context_argv, input=json.dumps(document), capture_output=True, text=True,
                         encoding='utf-8', timeout=120)
report['context'] = {'code': context.returncode, 'stdout': context.stdout, 'stderr': context.stderr[-3000:]}
report['call'] = rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                      'params': {'name': 'subagents', 'arguments': request}})
# The end of the Session as Claude Code ends it on Windows: stdin closed, no signal (M18).
host.stdin.close()
try:
    report['exit'] = host.wait(timeout=60)
except subprocess.TimeoutExpired:
    host.kill()
    report['exit'] = 'timeout'
report['channel_after'] = channel.exists()
out.write_text(json.dumps(report), encoding='utf-8')
'''


def make_venv(tmp_path: Path) -> Path:
    """A real venv that imports this checkout and its dependencies; returns its python."""
    venv = tmp_path / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True, timeout=300)
    python = venv / 'Scripts' / 'python.exe'
    # Only the paths, not this interpreter's .pth files: graftling from the checkout, PyYAML and websockets beside it.
    (venv / 'Lib' / 'site-packages' / 'graftling-647-test.pth').write_text(
        '\n'.join([str(SOURCE), *site.getsitepackages()]) + '\n', encoding='utf-8')
    return python


def test_generated_plugin_host_serves_one_call_and_ends_at_stdin_eof(tmp_path: Path) -> None:
    """configuration --write's .mcp.json and mod, started by a stand-in Claude Code (647).

    On Windows the configuration is written by a real venv's python, whose
    launcher is shown to start a child; the host then runs with the venv's
    prefix and the stand-in as its direct parent. Elsewhere this Python writes
    it, and the same session runs unchanged.
    """
    project = tmp_path / 'project'
    project.mkdir()
    environment = clean_environment()
    # The stand-in Claude Code is a real executable, this Python's own image; configured as "claude".
    standin = Path(sys.executable).resolve()
    python = make_venv(tmp_path) if WINDOWS else Path(sys.executable)
    if WINDOWS:
        probe = subprocess.Popen([str(python), '-c', 'import os; print(os.getpid(), os.getppid())'],
                                 stdout=subprocess.PIPE, text=True)
        child, parent = map(int, probe.communicate(timeout=60)[0].split())
        # The venv's python.exe is a launcher: the interpreter is its child, not the started process.
        assert child != probe.pid and parent == probe.pid

    written = subprocess.run([str(python), '-m', 'graftling.claude.host', 'configuration', '--project', str(project),
                              '--claude', str(standin), '--write'],
                             cwd=project, env=environment, capture_output=True, text=True, timeout=120)
    assert written.returncode == 0, written.stderr
    server = json.loads((project / '.mcp.json').read_text(encoding='utf-8'))['mcpServers']['graftling']
    settings = json.loads((project / '.claude' / 'settings.local.json').read_text(encoding='utf-8'))['env']
    plugin = project / '.claude' / 'skills' / 'agents-claude-host'
    generated = [path.read_text(encoding='utf-8') for path in plugin.rglob('*') if path.is_file()]
    # No sh anywhere in what configuration --write generates (M9 for the generated plugin).
    assert json.loads((plugin / 'hooks' / 'hooks.json').read_text(encoding='utf-8')) == {'modules': ['./host_context.mjs']}
    assert not any("'sh'" in text or '"sh"' in text for text in generated)
    assert Path(server['command']).stem.lower().startswith('python')
    assert server['args'][:3] == ['-m', 'graftling.claude.host', 'serve']
    if WINDOWS:
        base = Path(server['command'])
        assert base.name.lower() == 'python.exe' and base.resolve() != python.resolve()
        assert set(server['env']) == {'__PYVENV_LAUNCHER__'} and Path(server['env']['__PYVENV_LAUNCHER__']) == python
        prefix = subprocess.run([server['command'], '-c', 'import sys; print(sys.prefix)'],
                                env={**environment, **server['env']}, capture_output=True, text=True, timeout=60)
        assert Path(prefix.stdout.strip()).resolve() == python.parent.parent.resolve()
    else:
        assert set(server) == {'command', 'args'} and Path(server['command']).resolve() == standin

    context = {**settings, **({'OS': os.environ['OS']} if 'OS' in os.environ else {})}
    found = mod_context(plugin / 'hooks' / 'host_context.mjs', context, 'mcp__graftling__subagents', project)
    argv = found['run']
    if WINDOWS:
        assert Path(argv[0]) == Path(server['command']) and argv[1:3] == ['-I', '-c']
        assert Path(argv[4]) == python.parent.parent
    else:
        assert argv[:3] == [settings['GRAFTLING_CLAUDE_HOST_PYTHON'], '-m', 'graftling.claude.host']

    out = tmp_path / 'report.json'
    run = subprocess.run([str(standin), '-I', '-c', STANDIN, json.dumps(server), json.dumps(argv), str(project),
                          str(out)], env=environment, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr
    report = json.loads(out.read_text(encoding='utf-8'))
    log = (project / '.agents-control' / 'host.log').read_text(encoding='utf-8')
    assert 'result' in report['initialize'], (report, log)
    # The stand-in is the host's direct parent, and the image the host verified.
    assert report['channel']['parent'] == report['standin'], (report, log)
    assert Path(report['channel']['executable']).resolve() == standin
    assert report['context']['code'] == 0, (report, log)
    assert json.loads(report['context']['stdout']) == {'prepared': True}
    assert report['call']['result']['isError'] is False, (report, log)
    assert report['exit'] == 0 and report['channel_after'] is False, (report, log)
    assert 'subagents host recorded its children stopped.' in log

    if WINDOWS:
        # The same host through the venv launcher, as .mcp.json named it before 647: refused, the launcher named.
        refused = subprocess.run([str(python), '-m', 'graftling.claude.host', 'serve', '--config',
                                  str(project / '.agents-control' / 'claude-host.json')],
                                 cwd=project, env=environment, input='', capture_output=True, text=True, timeout=60)
        facts = json.loads(re.search(r'\{.*\}', refused.stderr).group(0))
        assert Path(facts['observed_executable']) == python and Path(facts['expected_executable']) == standin
