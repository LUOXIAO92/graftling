"""DSH on Windows (649, W11): graftling finds an npm-installed DSH, and its service ends with its owner.

npm installs DSH on Windows as ``<prefix>\\dsh.cmd``, a shim that runs
``<prefix>\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js``; graftling reads the
package from that target (``graftling.dsh.settings.dsh_entry``), not from the
shim's own directory. The first tests check that resolution on any platform, and
the DSH plugin's ``directPython``: on Windows the host and the MCP server start a
venv's base interpreter directly, so they are direct children of DSH's node, as
the host's checks require (Pi's 648 made the same change for its extension).
The installed-DSH tests run only on Windows with DSH installed (the
dsh-windows-tests job of .github/workflows/windows.yml): the service starts
without a credential, answers ``session/list`` and leaves no process behind its
close or behind an owner killed without one (decision 3: the service's job is
killed on close; POSIX keeps its watcher, tested in test_dsh_standalone_child).
A model turn needs a key and is Task 650's.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from conftest import unsupported_dsh

from graftling.child_env import is_credential_name
from graftling.dsh import settings
from graftling.dsh.service import DshService
from graftling.dsh.settings import dsh_entry, dsh_package, prepare
from graftling.process import process_alive

# npm's cmd-shim for a package bin, as npm 11 writes it (C:\npm\prefix\dsh.cmd on the runner).
NPM_SHIM = ('@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n'
            '\r\nIF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n  SET "_prog=node"\r\n'
            '  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\nendLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & '
            '"%_prog%"  "%dp0%\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js" %*\r\n')
WINDOWS = sys.platform == 'win32'


def npm_prefix(root: Path) -> Path:
    """A global npm prefix as npm leaves it on Windows: the shims beside node_modules."""
    package = root / 'node_modules' / '@deepseek-ai' / 'dsh'
    (package / 'lib').mkdir(parents=True)
    (package / 'lib' / 'bin.js').write_text('')
    (package / 'package.json').write_text(json.dumps({'name': '@deepseek-ai/dsh', 'version': '0.2.2-rc.5'}))
    (root / 'dsh.cmd').write_text(NPM_SHIM)
    (root / 'dsh').write_text('#!/bin/sh\n')  # npm's sh shim, which Git Bash runs
    return package


@pytest.mark.parametrize('name', ['dsh.cmd', 'dsh.CMD', 'dsh'])
def test_npm_shim_names_the_installed_package_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         name: str) -> None:
    """shutil.which gives dsh.CMD (or a configured path without suffix); the package is the shim's target."""
    package = npm_prefix(tmp_path)
    monkeypatch.setattr(settings, 'windows', lambda: True)
    if name == 'dsh.CMD' and not (tmp_path / name).exists():
        (tmp_path / name).write_text(NPM_SHIM)  # a case-sensitive file system; Windows finds the same file
    assert dsh_entry(tmp_path / name) == (package / 'lib' / 'bin.js').resolve()
    assert dsh_package(tmp_path / name) == (package / 'package.json').resolve()
    assert settings.dsh_version_refusal(dsh_package(tmp_path / name)) is None
    prepared = prepare({'executable': str(tmp_path / name), 'model': 'm', 'sandbox': 'read-only'}, tmp_path)
    assert prepared['package'] == str((package / 'package.json').resolve())


def test_a_windows_executable_that_is_no_npm_shim_is_resolved_as_before(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """A stand-in .cmd (tests/dsh_standalone_peer.py layout) keeps <root>/bin/dsh -> <root>/package.json."""
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'dsh.cmd').write_text('@exit /b 1\r\n')
    monkeypatch.setattr(settings, 'windows', lambda: True)
    assert dsh_package(tmp_path / 'bin' / 'dsh.cmd') == tmp_path.resolve() / 'package.json'
    assert dsh_package(tmp_path / 'bin' / 'dsh.exe') == tmp_path.resolve() / 'package.json'


def test_posix_ignores_the_npm_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Off Windows the executable is only resolved: POSIX behaviour is unchanged."""
    npm_prefix(tmp_path)
    monkeypatch.setattr(settings, 'windows', lambda: False)
    assert dsh_entry(tmp_path / 'dsh.cmd') == (tmp_path / 'dsh.cmd').resolve()
    assert dsh_package(tmp_path / 'dsh') == tmp_path.resolve().parent / 'package.json'


def test_profile_patch_rows_name_graftling_modules_as_file_urls_on_windows(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """DSH's Loader imports a row's name as an ES module specifier: on Windows a file URL, never ``C:\\...``.

    Node reads ``C:`` as a URL scheme (ERR_UNSUPPORTED_ESM_URL_SCHEME), so a row named by a Windows path never
    loads and the child loses graftling's MCP and review rows (run 45996884900). Off Windows the path, as before.
    """
    import graftling.process_tree
    from graftling.dsh.execution import _module_name

    module = tmp_path / 'tools-\u6d4b\u8bd5-\u00fc' / 'plugin.mjs'
    monkeypatch.setattr(graftling.process_tree, 'windows', lambda: False)
    assert _module_name(module) == str(module)
    monkeypatch.setattr(graftling.process_tree, 'windows', lambda: True)
    name = _module_name(module)
    assert name == module.as_uri() and name.startswith('file:///')
    result = subprocess.run([shutil.which('node') or 'node', '--input-type=module', '-e',
                             'import { fileURLToPath } from "node:url"; console.log(fileURLToPath(process.argv[1]));',
                             name], capture_output=True, text=True, encoding='utf-8', timeout=30) \
        if shutil.which('node') else None
    if result is not None:
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == module


DIRECT = '''
import { pathToFileURL } from 'node:url';
const {directPython} = await import(pathToFileURL(process.argv[1]).href);
console.log(JSON.stringify([directPython(process.argv[2], process.env, 'linux'),
                            directPython(process.argv[3], process.env, 'win32')]));
'''


def test_dsh_plugin_runs_a_windows_venv_python_as_its_base_interpreter(tmp_path: Path) -> None:
    """The host and MCP server command: unchanged off Windows; on Windows a venv's python becomes its base
    interpreter with __PYVENV_LAUNCHER__, so both are direct children of DSH's node (as Pi's 648)."""
    from test_dsh_standalone_host import STUBS

    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required')
    modules = tmp_path / 'stub' / 'node_modules' / '@deepseek-ai'
    for name, text in STUBS.items():
        (modules / name).mkdir(parents=True)
        (modules / name / 'package.json').write_text(json.dumps({'name': f'@deepseek-ai/{name}', 'type': 'module',
                                                                 'main': 'index.js'}))
        (modules / name / 'index.js').write_text(text)
    environment = {key: value for key, value in os.environ.items() if key != 'DSH_AGENT_PACKAGE'}
    result = subprocess.run([node, '--input-type=module', '-e', DIRECT,
                             str(Path(settings.__file__).with_name('plugin.mjs')), str(tmp_path / 'not-run'),
                             sys.executable],
                            env={**environment, 'GRAFTLING_DSH_PACKAGE': str(tmp_path / 'stub' / 'entry.js')},
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    posix, windows = json.loads(result.stdout)
    assert posix == {'command': str(tmp_path / 'not-run'), 'env': {}}
    if os.path.normcase(sys.executable) == os.path.normcase(sys._base_executable):
        assert windows == {'command': sys.executable, 'env': {}}
    else:
        assert windows == {'command': sys._base_executable, 'env': {'__PYVENV_LAUNCHER__': sys.executable}}


SPAWN = '''
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
const {directPython} = await import(pathToFileURL(process.argv[1]).href);
const {command, env} = directPython(process.argv[2]);
const child = spawn(command, ['-I', '-c', 'import json, os, sys; print(json.dumps([os.getppid(), sys.prefix]))'],
  {env: {...process.env, ...env}, stdio: ['ignore', 'pipe', 'inherit']});
let out = '';
child.stdout.on('data', data => { out += data; });
child.on('close', code => console.log(JSON.stringify({node: process.pid, code, command, env, child: JSON.parse(out || 'null')})));
'''


def test_a_venv_python_the_dsh_plugin_starts_is_a_direct_child_running_as_the_venv(tmp_path: Path) -> None:
    """A real venv's python through the plugin's directPython: DSH's node is its parent and it runs as the venv.

    On Windows the venv's python.exe is a launcher, so only its base interpreter, started with
    __PYVENV_LAUNCHER__, is node's direct child; the host's parent check and the MCP caller check need
    exactly that. Off Windows the venv's python is started unchanged.
    """
    from test_dsh_standalone_host import STUBS

    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required')
    venv = tmp_path / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True, timeout=120)
    python = venv / ('Scripts/python.exe' if WINDOWS else 'bin/python')
    modules = tmp_path / 'stub' / 'node_modules' / '@deepseek-ai'
    for name, text in STUBS.items():
        (modules / name).mkdir(parents=True)
        (modules / name / 'package.json').write_text(json.dumps({'name': f'@deepseek-ai/{name}', 'type': 'module',
                                                                 'main': 'index.js'}))
        (modules / name / 'index.js').write_text(text)
    environment = {key: value for key, value in os.environ.items()
                   if key not in {'DSH_AGENT_PACKAGE', '__PYVENV_LAUNCHER__'}}
    result = subprocess.run([node, '--input-type=module', '-e', SPAWN,
                             str(Path(settings.__file__).with_name('plugin.mjs')), str(python)],
                            env={**environment, 'GRAFTLING_DSH_PACKAGE': str(tmp_path / 'stub' / 'entry.js')},
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown['code'] == 0 and shown['child'] is not None, result.stdout + result.stderr
    parent, prefix = shown['child']
    assert parent == shown['node']
    assert Path(prefix).resolve() == venv.resolve()
    if WINDOWS:
        assert os.path.normcase(shown['command']) != os.path.normcase(str(python))
        assert os.path.normcase(shown['env']['__PYVENV_LAUNCHER__']) == os.path.normcase(str(python))
    else:
        assert shown['command'] == str(python) and shown['env'] == {}


def installed_dsh() -> str:
    """The npm-installed DSH on Windows, else skip."""
    if not WINDOWS:
        pytest.skip('Windows only: the npm .cmd shim and the DSH service\'s Job Object (649)')
    executable = shutil.which('dsh')
    if executable is None or shutil.which('node') is None:
        pytest.skip('Installed DSH and Node are required.')
    if (reason := unsupported_dsh()) is not None:
        pytest.fail(f'graftling refuses the installed DSH: {reason}')
    return executable


def service_environment(tmp_path: Path) -> dict[str, str]:
    """This environment without credentials, with an empty DSH home: no login is used."""
    environment = {key: value for key, value in os.environ.items() if not is_credential_name(key)}
    (tmp_path / 'dsh-home').mkdir(exist_ok=True)
    return {**environment, 'DSH_HOME': str(tmp_path / 'dsh-home')}


def still_running(pids: list[int], seconds: float = 15) -> list[int]:
    deadline = time.monotonic() + seconds
    left = [pid for pid in pids if process_alive(pid)]
    while left and time.monotonic() < deadline:
        time.sleep(0.5)
        left = [pid for pid in left if process_alive(pid)]
    return left


def test_installed_dsh_is_found_through_its_npm_shim(tmp_path: Path) -> None:
    """which('dsh') is npm's dsh.cmd; preparation reads the real package beside its entry."""
    executable = installed_dsh()
    assert Path(executable).suffix.lower() == '.cmd'
    package = dsh_package(executable)
    assert json.loads(package.read_text(encoding='utf-8'))['name'] == '@deepseek-ai/dsh'
    assert dsh_entry(executable) == (package.parent / 'lib' / 'bin.js').resolve()
    prepared = prepare({'executable': executable, 'model': 'm', 'sandbox': 'read-only'}, tmp_path)
    assert prepared['package'] == str(package)


def test_installed_dsh_service_starts_lists_and_leaves_nothing_after_close(tmp_path: Path) -> None:
    """dsh web through the shim in a kill-on-close job: authenticated, session/list answers, close ends it all."""
    service = DshService(installed_dsh(), tmp_path, service_environment(tmp_path))
    try:
        service.start()
        assert service.cookie
        assert service.rpc('session/list', {})['items'] == []
        pids = service.tree.pids()
        assert service.process.pid in pids
    finally:
        service.close()
    assert service.process.poll() is not None
    assert still_running(pids) == []


OWNER = r'''
import json, os, sys, time
from pathlib import Path
from graftling.dsh.service import DshService
service = DshService(sys.argv[1], Path(sys.argv[2]), json.loads(sys.argv[3]))
service.start()
print(json.dumps({"owner": os.getpid(), "pids": service.tree.pids()}), flush=True)
time.sleep(600)
'''


def test_installed_dsh_service_ends_when_its_owner_dies_without_close(tmp_path: Path) -> None:
    """Decision 3: the owner is killed (TerminateProcess, no close); its DSH service and job end with it."""
    executable = installed_dsh()
    owner = subprocess.Popen([sys.executable, '-c', OWNER, executable, str(tmp_path),
                              json.dumps(service_environment(tmp_path))],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf-8', errors='replace')
    try:
        line = owner.stdout.readline()
        report = json.loads(line) if line.startswith('{') else None
        assert report is not None, line + owner.stdout.read()
        assert len(report['pids']) >= 2 and all(process_alive(pid) for pid in report['pids'])
    finally:
        owner.kill()
        owner.wait(timeout=30)
    assert still_running(report['pids']) == []
