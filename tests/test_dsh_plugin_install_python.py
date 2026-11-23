"""graftling-dsh runs the Python of the one installation it belongs to (581).

``dsh plugin add <installed graftling/dsh>`` links the profile's ``graftling-dsh`` to the
installed directory. Each test builds such an installation (a venv holding a copy of
graftling whose ``graftling.dsh.host`` only records the Python running it), starts
plugin.mjs through a profile link with stub DSH packages, and reads which Python ran.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig

import pytest

from test_dsh_standalone_host import STUBS

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'graftling'

# Records the Python that runs it, then behaves as a host whose Session ends.
RECORDING_HOST = '''
import json, os, sys
sys.stdin.readline()
print(json.dumps({'connection': '/fake/address'}), flush=True)
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(sys.executable + '\\n')
sys.stdin.read()
'''

DRIVER = '''
import { pathToFileURL } from 'node:url';
const plugin = await import(pathToFileURL(process.argv[1]).href);
const handlers = {}, attached = [];
const ctx = {
  on: (event, handler) => { handlers[event] = handler; },
  effect: () => {}, plugin: () => ({}), logger: { error: () => {} },
  get: () => ({ receive: async () => ({ accepted: true }) }),
};
const agent = {
  session: { id: 'dsh-main', header: { version: 4, id: 'dsh-main', cwd: process.cwd(), isSeeded: false } },
  model: { current: () => ({ model: 'm' }) },
  ctx: { plugin: (_client, options) => { attached.push(options.command); return { await: async () => {} }; } },
};
plugin.apply(ctx, {});
let error = null;
try { await handlers['agent/created']({ agent }); } catch (failure) { error = failure.message; }
await handlers['session/disposed']({ id: 'dsh-main' });
console.log(JSON.stringify({ attached, error }));
'''


@pytest.fixture
def installation(tmp_path: Path) -> Path:
    """A venv with graftling in its site-packages, as ``uv tool install`` leaves one; returns its prefix.

    Beside it: ``stub/`` (DSH's packages) and ``bin/`` (the plugin's PATH, empty).
    """
    if not shutil.which('node'):
        pytest.skip('Node is required')
    prefix = tmp_path / 'tools' / 'graftling'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(prefix)], check=True)
    package = site_packages(prefix) / 'graftling'
    shutil.copytree(SOURCE, package, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('PyYAML', 'websockets'):    # graftling's dependencies, offline
        distribution = importlib.metadata.distribution(name)
        for file in distribution.files or []:
            source = Path(distribution.locate_file(file))
            if not str(file).startswith('..') and source.is_file():
                (site_packages(prefix) / file).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, site_packages(prefix) / file)
    (package / 'dsh' / 'host.py').write_text(RECORDING_HOST)

    modules = tmp_path / 'stub' / 'node_modules' / '@deepseek-ai'
    for name, text in STUBS.items():
        (modules / name).mkdir(parents=True)
        (modules / name / 'package.json').write_text(json.dumps({'name': f'@deepseek-ai/{name}', 'type': 'module',
                                                                 'main': 'index.js'}))
        (modules / name / 'index.js').write_text(text)
    (tmp_path / 'bin').mkdir()
    return prefix


def site_packages(prefix: Path) -> Path:
    """The site-packages directory of the venv at ``prefix``."""
    return Path(sysconfig.get_path('purelib', vars={'base': str(prefix), 'platbase': str(prefix)}))


def linked(tmp_path: Path, directory: Path) -> Path:
    """The profile's ``node_modules/graftling-dsh`` linked to ``directory``, as pnpm's ``link:`` leaves it."""
    plugin = tmp_path / 'profile' / 'node_modules' / 'graftling-dsh'
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.symlink_to(directory, target_is_directory=True)
    return plugin


def on_path(tmp_path: Path, python: str) -> None:
    """Make ``python`` the ``python3`` on the plugin's PATH."""
    script = tmp_path / 'bin' / 'python3'
    script.write_text(f"#!/bin/sh\nexec '{python}' \"$@\"\n")
    script.chmod(0o755)


def start(tmp_path: Path, plugin: Path, **environment: str) -> dict:
    """Start one DSH main Session's host through ``plugin``; return the Pythons used and any refusal."""
    home = tmp_path / 'dsh-home'
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GRAFTLING_', 'DSH_')) and key not in ('GRAPHTRAJ_CLI_CONNECTION', 'PYTHONPATH')}
    env.update({'PATH': f'{tmp_path / "bin"}:/usr/bin:/bin', 'GRAFTLING_DSH_PACKAGE': str(tmp_path / 'stub' / 'entry.js'),
                'GRAFTLING_DSH_CONFIG': '/abs/dsh-host.json', 'DSH_HOME': str(home),
                'TEST_LOG': str(tmp_path / 'host.log'), **environment})
    log = tmp_path / 'host.log'
    log.unlink(missing_ok=True)
    result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', DRIVER, str(plugin / 'plugin.mjs')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    shown['host'] = log.read_text().split() if log.exists() else []
    assert not (home / 'graftling-python').exists()
    return shown


def test_the_linked_plugin_runs_the_python_of_its_installation(installation: Path, tmp_path: Path) -> None:
    """No GRAFTLING_PYTHON: the host and the MCP server run the installation's own Python."""
    shown = start(tmp_path, linked(tmp_path, site_packages(installation) / 'graftling' / 'dsh'))
    assert shown['error'] is None, shown['error']
    used = Path(shown['attached'][0])
    assert shown['attached'] == shown['host'] and used.parent == installation / 'bin'


def test_a_python_of_another_graftling_is_refused_with_the_uv_command(installation: Path, tmp_path: Path) -> None:
    """A plugin copied away from its installation finds no Python of its own and names the one install."""
    copy = tmp_path / 'copied' / 'graftling-dsh'
    shutil.copytree(site_packages(installation) / 'graftling' / 'dsh', copy)
    on_path(tmp_path, str(installation / 'bin' / 'python'))    # imports graftling, but another copy
    shown = start(tmp_path, linked(tmp_path, copy))
    assert shown['attached'] == [] and shown['host'] == []
    version = json.loads((copy / 'package.json').read_text())['version']
    assert re.search(r'uv tool install "git\+https://github\.com/LUOXIAO92/graftling\.git@v'
                     + re.escape(version) + '"', shown['error']), shown['error']
    assert 'GRAFTLING_PYTHON' in shown['error']


def test_explicit_graftling_python_wins_and_is_used_unchanged(installation: Path, tmp_path: Path) -> None:
    """GRAFTLING_PYTHON is used as given even when the installation's Python would be found."""
    python = tmp_path / 'chosen-python'
    python.write_text(f'#!{sys.executable}\n{RECORDING_HOST}')
    python.chmod(0o755)
    shown = start(tmp_path, linked(tmp_path, site_packages(installation) / 'graftling' / 'dsh'),
                  GRAFTLING_PYTHON=str(python))
    assert shown['error'] is None and shown['attached'] == [str(python)] and len(shown['host']) == 1
