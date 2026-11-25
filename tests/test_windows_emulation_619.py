"""graftling's Windows paths under an emulation of Windows on a POSIX host (619).

Each check runs in a fresh interpreter where graftling's own modules (only
those) find no ``fcntl``, get a stand-in ``msvcrt`` that records its calls,
and see an ``os`` whose ``name`` is ``'nt'`` and that lacks the POSIX-only
process functions. Patching ``os.name`` or ``sys.modules`` for the whole
interpreter would make ``pathlib`` and ``subprocess`` take the host for
Windows, so the emulation is scoped per module through its builtins. This is development evidence only: the real check is a
Windows runner.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import graftling

# Prepended to every emulated script; ``EMULATED`` holds the stand-in's calls.
EMULATION = r'''
import builtins, importlib.machinery, json, os, signal, sys, types

sys.path.insert(0, SOURCE)
EMULATED = []

msvcrt = types.ModuleType('msvcrt')
msvcrt.LK_UNLCK, msvcrt.LK_LOCK = 0, 1
msvcrt.locking = lambda descriptor, mode, length: EMULATED.append(['locking', mode, length])

nt_os = types.ModuleType('os')
nt_os.__dict__.update(os.__dict__)
nt_os.name = 'nt'
for attribute in ('killpg', 'getpgid', 'getuid', 'setsid', 'fork'):
    nt_os.__dict__.pop(attribute, None)
nt_signal = types.ModuleType('signal')
nt_signal.__dict__.update(signal.__dict__)
nt_signal.__dict__.pop('SIGKILL', None)
REPLACED = {'os': nt_os, 'signal': nt_signal, 'msvcrt': msvcrt}


def nt_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Give graftling modules the emulated ``os``, ``signal`` and ``msvcrt``, and no ``fcntl``.

    The standard library keeps the host's modules, so it does not take this
    host for Windows.
    """
    if name == 'fcntl' and level == 0:
        raise ModuleNotFoundError("No module named 'fcntl'", name='fcntl')
    if name == 'msvcrt' and level == 0:
        return msvcrt
    module = builtins.__import__(name, globals, locals, fromlist, level)
    return REPLACED.get(name, module) if level == 0 else module


class Loader(importlib.machinery.SourceFileLoader):
    """Run a graftling module with the emulated imports as its builtins."""

    def exec_module(self, module):
        module.__builtins__ = {**builtins.__dict__, '__import__': nt_import}
        super().exec_module(module)


class Finder:
    """Load graftling's own modules through ``Loader``."""

    @staticmethod
    def find_spec(name, path=None, target=None):
        if name != 'graftling' and not name.startswith('graftling.'):
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            spec.loader = Loader(spec.loader.name, spec.loader.path)
        return spec


sys.meta_path.insert(0, Finder)
'''


def emulated(script: str, tmp_path: Path) -> dict:
    """Run ``script`` under the emulation; return the JSON it prints last."""
    source = str(Path(graftling.__file__).resolve().parents[1])
    program = f'SOURCE = {source!r}\n' + EMULATION + script
    result = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def test_graftling_imports_under_windows_emulation(tmp_path: Path) -> None:
    """``import graftling`` and its Session archive succeed without ``fcntl``."""
    report = emulated(
        'import graftling, graftling.sessions, graftling.sessions.archive, graftling.locks\n'
        'print(json.dumps({"os": graftling.locks.os.name, "fcntl": "fcntl" in vars(graftling.locks),\n'
        '                  "read_session": callable(graftling.read_session)}))\n',
        tmp_path,
    )
    assert report == {'os': 'nt', 'fcntl': False, 'read_session': True}


def test_archive_lock_goes_through_windows_lock_under_emulation(tmp_path: Path) -> None:
    """Creating an archive takes and releases its lock through ``msvcrt``.

    Only the lock is checked here; writing archives on Windows stays unsupported.
    """
    report = emulated(
        'from pathlib import Path\n'
        'from graftling.sessions.archive import JsonlSessionStore\n'
        'from graftling.sessions.protocol import SESSION_FORMAT_VERSION\n'
        'header = {"version": SESSION_FORMAT_VERSION, "id": "s-619", "createdAt": 0, "isSeeded": False}\n'
        'path = Path("sessions/p/s-619.jsonl")\n'
        'try:\n'
        '    JsonlSessionStore.create(path, header)\n'
        '    error = None\n'
        'except Exception as caught:\n'
        '    error = type(caught).__name__ + ": " + str(caught)\n'
        'print(json.dumps({"calls": EMULATED, "lock": path.with_suffix(".lock").exists(), "error": error}))\n',
        tmp_path,
    )
    assert report['calls'][:2] == [['locking', 1, 1], ['locking', 0, 1]], report
    assert report['lock'], report


def test_process_listing_refuses_clearly_under_windows_emulation(tmp_path: Path) -> None:
    """Listing marked processes names Windows instead of loading libproc.dylib."""
    report = emulated(
        'from graftling.member_processes import ancestor_pids, find_marked_processes, stop_marked_processes\n'
        'try:\n'
        '    find_marked_processes("m-619")\n'
        '    raised = None\n'
        'except OSError as error:\n'
        '    raised = str(error)\n'
        'print(json.dumps({"raised": raised, "stop": stop_marked_processes("m-619"),\n'
        '                  "ancestors": sorted(ancestor_pids()) == [os.getpid()]}))\n',
        tmp_path,
    )
    assert report['raised'] == 'Listing marked processes is not supported on Windows.'
    assert report['stop'] == {'found': [], 'stopped': [], 'remaining': [], 'error': report['raised']}
    assert report['ancestors']


def test_codex_parent_host_refuses_clearly_under_windows_emulation(tmp_path: Path) -> None:
    """The Codex parent host names Windows instead of failing on ``import fcntl``."""
    report = emulated(
        'from graftling import AgentsError\n'
        'from graftling.codex.host import CodexHost\n'
        'try:\n'
        '    CodexHost.__new__(CodexHost).run()\n'
        '    raised = None\n'
        'except Exception as error:\n'
        '    raised = [type(error).__name__, getattr(error, "code", None), str(error)]\n'
        'print(json.dumps({"raised": raised}))\n',
        tmp_path,
    )
    assert report['raised'][:2] == ['AgentsError', 'authority-denied'], report
    assert 'Windows does not support the Codex parent host.' in report['raised'][2], report
