"""graftling-dsh installed from the repository root builds its own Python environment (569).

Each test starts plugin.mjs from a copy of the repository, as DSH loads it after
``dsh plugin add git+https://github.com/LUOXIAO92/graftling.git#<commit>``, with stub
DSH packages and a ``graftling.dsh.host`` that only records the Python running it.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_dsh_standalone_host import STUBS

SOURCE = Path(__file__).resolve().parents[1]

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

# python3 on PATH that reports an older version, as an older interpreter would.
OLD_PYTHON = '''#!/bin/sh
shift
exec '{executable}' -I -c 'import runpy, sys
sys.version_info = (3, 11, 9, "final", 0)
sys.version = "3.11.9"
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")' "$@"
'''


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A copy of the repository as the root package installs it, with the recording host.

    Beside it: ``stub/`` (DSH's packages) and ``bin/python3`` (this test's Python).
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required')
    root = tmp_path / 'graftling-dsh'
    shutil.copytree(SOURCE / 'src' / 'graftling', root / 'src' / 'graftling',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy(SOURCE / 'pyproject.toml', root)
    (root / 'src' / 'graftling' / 'dsh' / 'host.py').write_text(RECORDING_HOST)

    modules = tmp_path / 'stub' / 'node_modules' / '@deepseek-ai'
    for name, text in STUBS.items():
        (modules / name).mkdir(parents=True)
        (modules / name / 'package.json').write_text(json.dumps({'name': f'@deepseek-ai/{name}', 'type': 'module',
                                                                 'main': 'index.js'}))
        (modules / name / 'index.js').write_text(text)
    python_on_path(tmp_path, f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
    return root


def python_on_path(tmp_path: Path, script: str) -> None:
    """Make ``script`` the only ``python3`` on the plugin's PATH."""
    python = tmp_path / 'bin' / 'python3'
    python.parent.mkdir(exist_ok=True)
    python.write_text(script)
    python.chmod(0o755)


def start(repository: Path, home: Path, **environment: str) -> dict:
    """Start one DSH main Session's host through plugin.mjs; return the Pythons used and any refusal."""
    tmp_path = repository.parent
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GRAFTLING_', 'DSH_')) and key != 'GRAPHTRAJ_CLI_CONNECTION'}
    env.update({'PATH': f'{tmp_path / "bin"}:/usr/bin:/bin', 'GRAFTLING_DSH_PACKAGE': str(tmp_path / 'stub' / 'entry.js'),
                'GRAFTLING_DSH_CONFIG': '/abs/dsh-host.json', 'DSH_HOME': str(home),
                'TEST_LOG': str(tmp_path / 'host.log'), **environment})
    log = tmp_path / 'host.log'
    log.unlink(missing_ok=True)
    result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', DRIVER,
                             str(repository / 'src' / 'graftling' / 'dsh' / 'plugin.mjs')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    shown['host'] = log.read_text().split() if log.exists() else []
    return shown


def provide_dependencies(python: Path) -> None:
    """Do offline what the refusal's pip command does: put PyYAML and websockets into the environment."""
    purelib = Path(subprocess.run([str(python), '-I', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
                                  capture_output=True, text=True, check=True).stdout.strip())
    for name in ('PyYAML', 'websockets'):
        distribution = importlib.metadata.distribution(name)
        for file in distribution.files or []:
            source = Path(distribution.locate_file(file))
            if not str(file).startswith('..') and source.is_file():
                (purelib / file).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, purelib / file)


def started(repository: Path, home: Path) -> Path:
    """Start once, supplying the dependencies if the refusal asks for them; return the Python used."""
    shown = start(repository, home)
    if shown['error']:
        assert 'lacks' in shown['error'], shown['error']
        provide_dependencies(Path(shown['error'].split(' -m pip install ')[0].split(' && ')[-1]))
        shown = start(repository, home)
    assert shown['error'] is None, shown['error']
    assert shown['attached'] == shown['host']
    return Path(shown['host'][0])


def environments(home: Path) -> list[str]:
    """The names in the DSH home's graftling-python directory, without lock files."""
    return sorted(path.name for path in (home / 'graftling-python').iterdir() if not path.name.endswith('.lock'))


def test_the_repository_root_is_the_same_bundle_without_install_scripts() -> None:
    """``dsh plugin add <repository>`` registers graftling-dsh; pnpm would refuse an install script."""
    import graftling

    root   = json.loads((SOURCE / 'package.json').read_text())
    plugin = json.loads((SOURCE / 'src/graftling/dsh/package.json').read_text())
    inside = SOURCE / 'src/graftling/dsh'
    assert (root['name'], root['version'], root['dsh']['client']) == (
        plugin['name'], graftling.__version__, plugin['dsh']['client'])
    assert plugin['version'] == graftling.__version__ and 'scripts' not in root
    assert (SOURCE / root['dsh']['bundle']['patch']).resolve() == (inside / plugin['dsh']['bundle']['patch']).resolve()
    assert (SOURCE / root['main']).resolve() == (inside / plugin['main']).resolve()
    assert {key: (SOURCE / value).resolve() for key, value in root['exports'].items()} == {
        key: (inside / value).resolve() for key, value in plugin['exports'].items()}


def test_first_start_builds_under_the_dsh_home_and_later_starts_reuse_it(repository: Path, tmp_path: Path) -> None:
    """Without GRAFTLING_PYTHON the host and MCP server run one environment under DSH_HOME."""
    home   = tmp_path / 'dsh-home'
    python = started(repository, home)
    assert python.parent.parent.parent == home / 'graftling-python'
    assert python.parent.parent.name.startswith('0.7.0-')
    (python.parent.parent / 'kept').write_text('')

    assert started(repository, home) == python
    assert (python.parent.parent / 'kept').exists() and environments(home) == [python.parent.parent.name]


def test_missing_dependencies_name_the_command_that_installs_them(repository: Path, tmp_path: Path) -> None:
    """A base Python without PyYAML or websockets: the refusal names the exact install command."""
    home  = tmp_path / 'dsh-home'
    shown = start(repository, home)
    if shown['error'] is None:
        pytest.skip(f'{sys.executable} provides PyYAML and websockets to the environment')
    environment = home / 'graftling-python' / environments(home)[0]
    python = environment / 'bin' / 'python'
    assert shown['host'] == [] and shown['attached'] == []
    assert f"{python} -m ensurepip && {python} -m pip install 'PyYAML>=6,<7' 'websockets>=15,<17'" in shown['error']
    provide_dependencies(python)
    assert started(repository, home) == python


def test_changed_sources_get_their_own_environment(repository: Path, tmp_path: Path) -> None:
    """An upgrade, or the same version with other content, builds anew; the old one is left as it was."""
    home  = tmp_path / 'dsh-home'
    first = started(repository, home)
    with (repository / 'src' / 'graftling' / 'notices.py').open('a') as stream:
        stream.write('\n# changed\n')
    second = started(repository, home)
    assert second != first and second.parent.parent.name.startswith('0.7.0-')
    assert environments(home) == sorted([first.parent.parent.name, second.parent.parent.name])
    copied = next((second.parent.parent / 'lib').glob('python*/site-packages/graftling/notices.py'))
    assert copied.read_text().endswith('# changed\n')


def test_concurrent_starts_build_once_and_agree(repository: Path, tmp_path: Path) -> None:
    """Several starts at once: one environment, no unfinished copy, every start names it."""
    home    = tmp_path / 'dsh-home'
    command = [str(tmp_path / 'bin' / 'python3'), '-I', str(repository / 'src/graftling/dsh/plugin_python.py'), str(home)]
    runs    = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    outputs = [run.communicate(timeout=120) for run in runs]
    [name] = environments(home)
    for (stdout, stderr), run in zip(outputs, runs):
        assert 'failed' not in stderr
        assert str(home / 'graftling-python' / name / 'bin' / 'python') in stdout + stderr
        assert run.returncode == (0 if stdout else 1)


def test_an_unfinished_build_is_never_used_and_the_next_start_recovers(repository: Path, tmp_path: Path) -> None:
    """A failed build leaves no environment; after the fix the next start removes the leftover and builds."""
    if os.geteuid() == 0:
        pytest.skip('root reads files without permission bits')
    home        = tmp_path / 'dsh-home'
    python      = started(repository, home)
    environment = python.parent.parent
    shutil.rmtree(environment)

    # The build cannot write its copy.
    environment.parent.chmod(0o555)
    try:
        shown = start(repository, home)
    finally:
        environment.parent.chmod(0o755)
    assert shown['host'] == [] and 'building the Python environment' in shown['error']
    assert environments(home) == []

    # A build killed after it began leaves its unfinished copy.
    (environment.parent / f'{environment.name}.partial' / 'bin').mkdir(parents=True)
    assert started(repository, home) == python
    assert environments(home) == [environment.name] and python.exists()


def test_old_or_missing_python3_is_refused_with_the_fix(repository: Path, tmp_path: Path) -> None:
    """python3 older than 3.12, or none on PATH: nothing is built and the message names the fix."""
    home = tmp_path / 'dsh-home'
    python_on_path(tmp_path, OLD_PYTHON.format(executable=sys.executable))
    shown = start(repository, home)
    assert shown['host'] == [] and not home.exists()
    assert 'Python 3.11.9' in shown['error'] and 'Put a Python 3.12+ first on PATH as python3' in shown['error']

    (tmp_path / 'bin' / 'python3').unlink()
    shown = start(repository, home, PATH=str(tmp_path / 'bin'))
    assert shown['host'] == [] and 'no python3 on PATH' in shown['error']


def test_explicit_graftling_python_wins_and_is_used_unchanged(repository: Path, tmp_path: Path) -> None:
    """GRAFTLING_PYTHON is used as given and nothing is built, even with the sources present."""
    home   = tmp_path / 'dsh-home'
    python = tmp_path / 'chosen-python'
    python.write_text(f'#!{sys.executable}\n{RECORDING_HOST}')
    python.chmod(0o755)
    shown = start(repository, home, GRAFTLING_PYTHON=str(python))
    assert shown['error'] is None and shown['attached'] == [str(python)] and len(shown['host']) == 1
    assert not home.exists()


def test_without_sources_the_plugin_still_needs_graftling_python(repository: Path, tmp_path: Path) -> None:
    """The subdirectory or installed-package plugin has no sources to build from: it names GRAFTLING_PYTHON."""
    plugin = tmp_path / 'profile' / 'node_modules' / 'graftling-dsh'
    shutil.copytree(repository / 'src' / 'graftling' / 'dsh', plugin)
    env ={key: value for key, value in os.environ.items() if not key.startswith(('GRAFTLING_', 'DSH_'))}
    env.update(GRAFTLING_DSH_PACKAGE=str(tmp_path / 'stub' / 'entry.js'), GRAFTLING_DSH_CONFIG='/abs/dsh-host.json',
               DSH_HOME=str(tmp_path / 'dsh-home'))
    result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', DRIVER, str(plugin / 'plugin.mjs')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    shown = json.loads(result.stdout)
    assert shown['attached'] == [] and 'needs GRAFTLING_PYTHON' in shown['error']
    assert not (tmp_path / 'dsh-home').exists()
