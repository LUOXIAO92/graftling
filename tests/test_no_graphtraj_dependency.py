"""graftling never depends on GraphTraj (551): code, tests and packaging.

GraphTraj depends on graftling, never the reverse. Prose may name GraphTraj
(a consumer, the ``extra_fields.graphtraj`` archive namespace, ``.graphtraj``
data paths), but no graftling module or test may import it, start one of its
modules or executables, or declare it in packaging; and every graftling module
must import while GraphTraj cannot be imported at all.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src' / 'graftling'
FORBIDDEN = 'graphtraj'
# A dotted GraphTraj module path ('graphtraj.interfaces.mcp') or one of its executables.
MODULE_PATH = re.compile(r'^graphtraj(\.[A-Za-z_]\w*)+$')
EXECUTABLE = re.compile(r'\bgraphtraj-(mcp|session|desktop)\b')
RUN_MODULE = re.compile(r'-m\W{0,4}graphtraj\b')
IMPORT_CALLS = {'import_module', '__import__', 'find_spec', 'run_module'}


def _top(name: str | None) -> str:
    return (name or '').split('.')[0]


def python_files() -> list[Path]:
    return sorted(path for base in (PACKAGE, ROOT / 'tests') for path in base.rglob('*.py')
                  if '__pycache__' not in path.parts)


def offences(path: Path) -> list[str]:
    """Every place in ``path`` that imports, runs or names a GraphTraj module or executable."""
    found = []
    tree = ast.parse(path.read_text(encoding='utf-8'), str(path))
    for node in ast.walk(tree):
        where = f'{path.relative_to(ROOT)}:{getattr(node, "lineno", "?")}'
        if isinstance(node, ast.Import):
            found += [f'{where} import {alias.name}' for alias in node.names if _top(alias.name) == FORBIDDEN]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _top(node.module) == FORBIDDEN:
            found.append(f'{where} from {node.module} import ...')
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
            first = node.args[0] if node.args else None
            if (name in IMPORT_CALLS and isinstance(first, ast.Constant) and isinstance(first.value, str)
                    and _top(first.value) == FORBIDDEN):
                found.append(f'{where} {name}({first.value!r})')
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if MODULE_PATH.match(node.value) or EXECUTABLE.search(node.value) or RUN_MODULE.search(node.value):
                found.append(f'{where} names {node.value[:80]!r}')
        elif isinstance(node, (ast.List, ast.Tuple)):
            items = [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            for flag, value in zip(items, items[1:]):
                if flag == '-m' and _top(value) == FORBIDDEN:
                    found.append(f'{where} runs -m {value}')
    return found


def test_no_module_or_test_imports_or_runs_graphtraj() -> None:
    """Python sources and tests: no import, import call, running a GraphTraj module, or its executables."""
    files = python_files()
    assert len(files) > 50, 'the scan must see the package and its tests'
    found = [entry for path in files for entry in offences(path)]
    assert found == []


def test_non_python_package_files_do_not_run_graphtraj() -> None:
    """Shipped plugins, patches and manifests (not prose) never start a GraphTraj module or executable."""
    found = []
    for path in sorted(PACKAGE.rglob('*')):
        if not path.is_file() or path.suffix in ('.py', '.pyc', '.md') or '__pycache__' in path.parts \
                or path.name in ('LICENSE', 'DSH-LICENSE'):
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for pattern in (RUN_MODULE, EXECUTABLE):
            found += [f'{path.relative_to(ROOT)}: {match.group(0)}' for match in pattern.finditer(text)]
        found += [f'{path.relative_to(ROOT)}: import {match.group(1)}'
                  for match in re.finditer(r'''(?:from|import|require\()\s*['"](graphtraj[^'"]*)['"]''', text)]
    assert found == []


def test_packaging_never_declares_or_requires_graphtraj() -> None:
    """pyproject: no build requirement, dependency, extra or script target names GraphTraj; nor pytest.ini."""
    project = tomllib.loads((PACKAGE / 'pyproject.toml').read_text(encoding='utf-8'))
    requirements = [*project.get('build-system', {}).get('requires', []),
                    *project['project'].get('dependencies', []),
                    *[item for extra in project['project'].get('optional-dependencies', {}).values() for item in extra]]
    assert [item for item in requirements if re.match(r'\s*graphtraj\b', item, re.I)] == []
    targets = [*project['project'].get('scripts', {}).values(),
               *[target for group in project['project'].get('entry-points', {}).values() for target in group.values()]]
    assert targets and [target for target in targets if _top(target.split(':')[0]) == FORBIDDEN] == []
    assert project['tool']['setuptools']['packages'] and all(
        _top(name) == 'graftling' for name in project['tool']['setuptools']['packages'])
    for name in ('pytest.ini', 'setup.cfg', 'setup.py', 'tox.ini'):
        for path in (ROOT / name, PACKAGE / name):
            if path.is_file():
                assert FORBIDDEN not in path.read_text(encoding='utf-8').lower(), path
    assert not list(ROOT.glob('requirements*.txt')) or all(
        FORBIDDEN not in path.read_text(encoding='utf-8').lower() for path in ROOT.glob('requirements*.txt'))


IMPORT_ALL = r'''
import importlib, json, pkgutil, sys

class NoGraphTraj:
    def find_spec(self, name, path=None, target=None):
        if name == 'graphtraj' or name.startswith('graphtraj.'):
            raise ImportError('GraphTraj is blocked: graftling must not need it')
        return None

sys.meta_path.insert(0, NoGraphTraj())
sys.path.insert(0, sys.argv[1])
import graftling
failed, names = {}, ['graftling']
for info in pkgutil.walk_packages(graftling.__path__, 'graftling.'):
    names.append(info.name)
    try:
        importlib.import_module(info.name)
    except Exception as error:  # every failure is reported, GraphTraj or not
        failed[info.name] = f'{type(error).__name__}: {error}'
print(json.dumps({'modules': names, 'failed': failed,
                  'graphtraj_modules': sorted(m for m in sys.modules if m.split('.')[0] == 'graphtraj')}))
'''


def test_every_module_imports_with_graphtraj_blocked() -> None:
    """In a fresh interpreter where any GraphTraj import raises, every graftling module still imports."""
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith('GRAPHTRAJ_') and name != 'PYTHONPATH'}
    result = subprocess.run([sys.executable, '-I', '-c', IMPORT_ALL, str(ROOT / 'src')],
                            capture_output=True, text=True, timeout=120, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert len(evidence['modules']) > 30
    assert evidence['failed'] == {}
    assert evidence['graphtraj_modules'] == []
