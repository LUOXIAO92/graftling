"""652: the Claude Code host's refusals say why, on Windows and POSIX alike.

- A ``subagents`` call that arrives before graftling's plugin hook has reached the
  host (Claude Code did not load the plugin, as in a folder whose trust was not
  accepted) is still refused, now naming the plugin and the folder trust; host.log
  gets that reason once. After the hook's context the call is served as before.
- A context step started by a process under the same Claude Code, not by Claude
  Code itself (a command of its Bash tool), is refused as such, not as another
  session's.
- The marketplace plugin's module on native Windows says that plugin is not
  supported there and names configuration --write: one transcript line,
  /agents-context and a host.log line; nothing is associated or started.

The stand-in Claude Code is this Python's own image, as in test_claude_windows_647.py.
No real Claude Code, model or credential; the real entries are .github/claude_real_entry.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from graftling.claude.plugin import marketplace_module

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
def call(number):
    return rpc({'jsonrpc': '2.0', 'id': number, 'method': 'tools/call',
                'params': {'name': 'subagents', 'arguments': request}})
report = {'standin': os.getpid()}
rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
    'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'stand-in', 'version': '0'}}})
rpc({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
channel = project / '.agents-control' / 'live' / 'channel.json'
deadline = time.monotonic() + 60
while not channel.exists() and time.monotonic() < deadline:
    time.sleep(0.1)
request = {'action': 'discover'}
document = {'operation': 'prepare', 'tool': 'subagents', 'session': str(uuid.uuid4()), 'cwd': str(project),
            'agent': None, 'request': request}
# 1. Calls before any context: Claude Code has not loaded the plugin whose hook sends it.
report['unreached'] = [call(2), call(3)]
# 2. The context step from a grandchild of this stand-in, as from a shell its Bash tool started.
wrapper = 'import subprocess, sys; sys.exit(subprocess.run(sys.argv[1:]).returncode)'
nested = subprocess.run([sys.executable, '-c', wrapper, *context_argv], input=json.dumps(document),
                        capture_output=True, text=True, encoding='utf-8', timeout=120)
report['nested'] = {'code': nested.returncode, 'stderr': nested.stderr[-3000:]}
# 3. The context step from a direct child, as the plugin's hook runs it; then the call is served.
direct = subprocess.run(context_argv, input=json.dumps(document), capture_output=True, text=True,
                        encoding='utf-8', timeout=120)
report['direct'] = {'code': direct.returncode, 'stdout': direct.stdout, 'stderr': direct.stderr[-3000:]}
report['served'] = call(4)
host.stdin.close()
try:
    report['exit'] = host.wait(timeout=60)
except subprocess.TimeoutExpired:
    host.kill()
    report['exit'] = 'timeout'
out.write_text(json.dumps(report), encoding='utf-8')
'''


def clean_environment() -> dict:
    """This environment without a parent's channel, data root or venv launcher variable."""
    return {key: value for key, value in os.environ.items()
            if key not in {'GRAFTLING_CONNECTION', 'GRAFTLING_DATA_ROOT', '__PYVENV_LAUNCHER__'}}


def result_document(response: dict) -> dict:
    """The JSON document of one tools/call response."""
    return json.loads(response['result']['content'][0]['text'])


def test_host_names_the_unloaded_plugin_and_a_nested_caller(tmp_path: Path) -> None:
    """configuration --write's host, started by a stand-in Claude Code: the three refusals and the served call."""
    project = tmp_path / 'project'
    project.mkdir()
    environment = clean_environment()
    standin = Path(sys.executable).resolve()
    written = subprocess.run([sys.executable, '-m', 'graftling.claude.host', 'configuration', '--project',
                              str(project), '--claude', str(standin), '--write'],
                             cwd=project, env=environment, capture_output=True, text=True, timeout=120)
    assert written.returncode == 0, written.stderr
    server = json.loads((project / '.mcp.json').read_text(encoding='utf-8'))['mcpServers']['graftling']
    config = project / '.agents-control' / 'claude-host.json'
    context_argv = [sys.executable, '-m', 'graftling.claude.host', 'context', '--config', str(config)]

    out = tmp_path / 'report.json'
    run = subprocess.run([str(standin), '-I', '-c', STANDIN, json.dumps(server), json.dumps(context_argv),
                          str(project), str(out)], env=environment, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr
    report = json.loads(out.read_text(encoding='utf-8'))
    log = (project / '.agents-control' / 'host.log').read_text(encoding='utf-8')

    # Before any context: refused (authority-denied, nothing executed), naming the plugin and the folder trust.
    for response in report['unreached']:
        assert response['result']['isError'] is True, (report, log)
        error = result_document(response)['error']
        assert error['code'] == 'authority-denied'
        assert 'has not loaded it' in error['message'] and 'folder trust' in error['message']
        assert 'configuration --write' in error['message'] and 'not executed' in error['message']
    assert log.count('subagents-call refused:') == 1, log
    assert error['message'] in log

    # A grandchild of the same Claude Code: refused as such, with that Claude Code's pid.
    nested = report['nested']
    assert nested['code'] == 1, report
    assert 'not started by Claude Code itself' in nested['stderr'], nested
    assert f"Claude Code pid {report['standin']}" in nested['stderr']
    assert 'another Claude Code session' not in nested['stderr']

    # The hook's own context step: prepared, and the call it prepared is served.
    assert report['direct']['code'] == 0, (report, log)
    assert json.loads(report['direct']['stdout']) == {'prepared': True}
    assert report['served']['result']['isError'] is False, (report, log)
    assert report['exit'] == 0, (report, log)


MOD = r'''
import assert from 'node:assert/strict';
const {register} = await import(process.argv[1]);
const env = JSON.parse(process.argv[2]);
const handlers = new Map();
register((name, matcher, fn) => { handlers.set(fn && matcher.tool ? `${name}:${matcher.tool}` : name, fn ?? matcher);
  return {catch() {}}; });
const files = new Map(JSON.parse(process.argv[3]));
const runs = [], log = [], registrations = [], timers = [];
const host = {
 session: {id: async () => 'main', cwd: async () => 'C:\\work\\project'},
 command: {register: async entry => registrations.push(entry)},
 tool: {list: async () => []},
 env: {get: async key => env[key]},
 fs: {exists: async path => files.has(path), read: async path => files.get(path),
      write: async (path, text) => { files.set(path, text); }},
 process: {run: async argv => { runs.push(argv); return {exitCode: 0, stdout: '{}', stderr: ''}; }},
 clock: {setTimeout: fn => timers.push(fn)},
 ui: {log: value => log.push(value)},
};
await handlers.get('session.start')(host, {}, async () => 'started');
const tool = 'mcp__plugin_graftling_subagents__subagents';
const decided = await handlers.get(`tool.check:${tool}`)(host, {input: {action: 'discover'}}, async () => ({decision: 'allow'}));
await handlers.get('command.run')(host);
console.log(JSON.stringify({runs, log, registrations: registrations.map(entry => entry.name), timers: timers.length,
  decided, files: Object.fromEntries(files)}));
'''


def windows_mod(tmp_path: Path, environment: dict, files: dict) -> dict:
    """Run the marketplace module's session start, a permission check and /agents-context as native Windows."""
    if not shutil.which('node'):
        pytest.skip('Node is required')
    module = tmp_path / 'host_context.mjs'
    module.write_text(marketplace_module(), encoding='utf-8')
    result = subprocess.run(['node', '--input-type=module', '-e', MOD, module.as_uri(),
                             json.dumps({'OS': 'Windows_NT', **environment}), json.dumps(list(files.items()))],
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('data_root', [None, 'C:\\agents-data'], ids=['project', 'data-root'])
def test_marketplace_module_on_native_windows_names_configuration_write(tmp_path: Path, data_root: str | None) -> None:
    """One transcript line, /agents-context and an appended host.log line; no Python started, no push, no denial."""
    directory = data_root or 'C:\\work\\project/.agents-control'
    hostlog = f'{directory}/host.log'
    environment = {'GRAFTLING_PYTHON': 'C:\\venv\\Scripts\\python.exe'}
    if data_root:
        environment['GRAFTLING_DATA_ROOT'] = data_root
    found = windows_mod(tmp_path, environment, {hostlog: 'an earlier line\n'})
    message = found['log'][0]
    assert 'not supported on native Windows' in message and 'configuration --write' in message
    assert found['log'] == [message, message], 'the session start and /agents-context say it, nothing else'
    assert found['registrations'] == ['agents-context']
    lines = found['files'][hostlog].splitlines()
    assert lines[0] == 'an earlier line' and len(lines) == 2 and lines[1].endswith(message)
    assert found['runs'] == [] and found['timers'] == 0
    assert found['decided'] == {'decision': 'allow'}, 'Claude Code decides a tool it lists; the module adds nothing'
