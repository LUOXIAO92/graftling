"""Find and stop processes a member left behind after its Runtime exited.

Runtimes such as Claude Code start each tool command in its own session, so a
command like ``(nohup job &)`` leaves the Runtime's process group and is
re-parented to PID 1. Neither the group nor ancestry can find it afterwards.
Each member launch therefore carries a random per-execution value in
``MEMBER_RUN_ENV``; children inherit it through shells, ``setsid`` and
``nohup``. Only same-user processes whose environment holds exactly that value
are signalled. Names and command patterns are never matched.

The same scanner serves any such marker variable: ``variable`` names it (the
test suite uses its own per-run variable), and ``prefix=True`` matches every
value that starts with ``marker`` instead of exactly it.

Windows (637, 629 advice 3.5): the job is the list. Reading another process's
environment there means reading its memory, so nothing is scanned. A marker
resolves to the Job Object its tree was started in (``graftling-<marker>``,
:func:`graftling.process_tree.job_name`); its processes are listed with
JobObjectBasicProcessIdList and stopped by terminating the job, whatever
``variable`` names. A marker whose tree was not started through
:class:`graftling.process_tree.OwnedTree` has no job, and the scan raises
OSError saying so, which :func:`stop_marked_processes` reports in ``error``.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graftling.process_tree import OwnedTree

# The name avoids KEY, SECRET and TOKEN: Codex's default shell environment
# policy drops such variables from tool shells.
MEMBER_RUN_ENV = 'GRAFTLING_MEMBER_RUN'
_COMMAND_LIMIT = 300


def new_marker() -> str:
    """Return a fresh per-execution marker value."""
    return uuid.uuid4().hex


def _environment_darwin(pid: int) -> tuple[list[str], list[str]] | None:
    libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
    size = ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
    argmax = ctypes.c_int(0)
    if libc.sysctl((ctypes.c_int * 2)(1, 8), 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
        return None
    size = ctypes.c_size_t(argmax.value)
    buffer = ctypes.create_string_buffer(argmax.value)
    # CTL_KERN=1, KERN_PROCARGS2=49
    if libc.sysctl((ctypes.c_int * 3)(1, 49, pid), 3, buffer, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buffer.raw[:size.value]
    if len(raw) < 4:
        return None
    argc = int.from_bytes(raw[:4], sys.byteorder)
    position = raw.find(b'\0', 4)
    if position < 0:
        return None
    while position < len(raw) and raw[position] == 0:
        position += 1
    argv: list[str] = []
    for _ in range(argc):
        end = raw.find(b'\0', position)
        if end < 0:
            return None
        argv.append(raw[position:end].decode(errors='replace'))
        position = end + 1
    environment: list[str] = []
    while position < len(raw):
        end = raw.find(b'\0', position)
        if end <= position:
            break
        environment.append(raw[position:end].decode(errors='replace'))
        position = end + 1
    return argv, environment


def _environment_linux(pid: int) -> tuple[list[str], list[str]] | None:
    try:
        environ = Path(f'/proc/{pid}/environ').read_bytes()
        cmdline = Path(f'/proc/{pid}/cmdline').read_bytes()
    except OSError:
        return None
    split = lambda data: [item.decode(errors='replace') for item in data.split(b'\0') if item]
    return split(cmdline), split(environ)


def _process_view(pid: int) -> tuple[list[str], list[str]] | None:
    """Return (argv, environment) of a same-user process, or None if unreadable or gone."""
    try:
        if sys.platform == 'darwin':
            return _environment_darwin(pid)
        return _environment_linux(pid)
    except (OSError, ValueError):
        return None


def parent_pid(pid: int) -> int | None:
    """Return the parent of a process, or None when it is gone or unreadable."""
    try:
        if sys.platform.startswith('linux'):
            stat = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8')
            return int(stat[stat.rindex(')') + 2:].split()[1])
        if sys.platform == 'win32':
            # The recorded parent, only when it was created before ``pid`` (graftling.process).
            from graftling.process import _process_parent

            return _process_parent(pid) or None
        if os.name == 'nt':
            # Never load '/usr/lib/libproc.dylib' as a drive-relative Windows path.
            return None
        libproc = ctypes.CDLL(ctypes.util.find_library('proc') or '/usr/lib/libproc.dylib', use_errno=True)
        # PROC_PIDTBSDINFO=3: struct proc_bsdinfo, whose pbi_ppid is the fifth uint32.
        buffer = ctypes.create_string_buffer(136)
        if libproc.proc_pidinfo(pid, 3, 0, buffer, 136) < 20:
            return None
        return int.from_bytes(buffer.raw[16:20], sys.byteorder)
    except (OSError, ValueError, IndexError):
        return None


def ancestor_pids(pid: int | None = None) -> set[int]:
    """Return ``pid`` (default: this process) and all its ancestors."""
    current = os.getpid() if pid is None else pid
    found = set()
    while current and current > 1 and current not in found:
        found.add(current)
        current = parent_pid(current)
    return found


def _user_pids() -> list[int]:
    if sys.platform.startswith('linux'):
        uid = os.getuid()
        pids = []
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    if entry.stat().st_uid == uid:
                        pids.append(int(entry.name))
                except OSError:
                    continue
        return pids
    if os.name == 'nt':
        raise OSError('Listing marked processes is not supported on Windows.')
    # macOS: libproc lists every pid; KERN_PROCARGS2 later reads only same-user ones.
    libproc = ctypes.CDLL(ctypes.util.find_library('proc') or '/usr/lib/libproc.dylib', use_errno=True)
    count = libproc.proc_listallpids(None, 0)
    if count <= 0:
        raise OSError(ctypes.get_errno(), 'proc_listallpids failed')
    # A full buffer may have been cut short by processes started between the
    # two calls; retry with more room until the list fits (459).
    for _ in range(8):
        capacity = count * 2 + 64
        buffer = (ctypes.c_int * capacity)()
        count = libproc.proc_listallpids(buffer, ctypes.sizeof(buffer))
        if count <= 0:
            raise OSError(ctypes.get_errno(), 'proc_listallpids failed')
        if count < capacity:
            return [pid for pid in buffer[:count] if pid > 0]
    raise OSError(0, 'proc_listallpids kept filling its buffer')


def _carries(pid: int, marker: str, variable: str = MEMBER_RUN_ENV, prefix: bool = False) -> list[str] | None:
    """Return the argv when ``pid`` holds this marker (exactly, or as a prefix), else None."""
    view = _process_view(pid)
    if view is None:
        return None
    argv, environment = view
    entry = f'{variable}={marker}'
    if prefix:
        return argv if any(item.startswith(entry) for item in environment) else None
    return argv if entry in environment else None


def _command(argv: list[str]) -> str:
    text = ' '.join(argv)
    return text if len(text) <= _COMMAND_LIMIT else text[:_COMMAND_LIMIT] + '...'


def _open_job(name: str) -> OwnedTree:
    """Open the Windows job ``name``; OSError saying why its processes cannot be listed when none is open."""
    from graftling.process_tree import OwnedTree

    try:
        return OwnedTree.open(name=name)
    except OSError as error:
        raise OSError(f"No job {name} is open, so its processes cannot be listed on Windows: its tree was "
                      f"not started through graftling's OwnedTree, or every handle to the job has closed "
                      f"({type(error).__name__}: {error}).") from error


def _windows_argv(pid: int) -> list[str]:
    """Return a process's arguments as the kernel reports them; empty when unreadable."""
    from graftling.process import process_arguments

    try:
        return process_arguments(pid)
    except OSError:
        return []


def list_job(name: str, skipped: set[int] | frozenset[int]) -> dict[int, list[str]]:
    """Windows: map each PID in the job ``name``, except ``skipped``, to its arguments.

    OSError when no job of that name is open (:func:`_open_job`).
    """
    with _open_job(name) as tree:
        return {pid: _windows_argv(pid) for pid in tree.pids() if pid not in skipped}


def stop_job(name: str, skipped: set[int] | frozenset[int],
             seconds: float) -> tuple[dict[int, list[str]], set[int]]:
    """Windows: end the processes of the job ``name`` except ``skipped``; return (found, remaining).

    ``found`` maps each PID to its arguments, read before the stop. When no
    skipped process is in the job, the job is terminated whole; otherwise
    only the found processes are, each after checking it is still in the job.
    ``remaining`` holds those still in the job after up to ``seconds``.
    OSError when no job of that name is open.
    """
    with _open_job(name) as tree:
        listed = tree.pids()
        found = {pid: _windows_argv(pid) for pid in listed if pid not in skipped}
        if not found:
            return found, set()
        tree.terminate(None if skipped.isdisjoint(listed) else list(found))
        deadline = time.monotonic() + seconds
        while True:
            remaining = set(found) & set(tree.pids())
            if not remaining or time.monotonic() >= deadline:
                return found, remaining
            time.sleep(0.05)


def find_marked_processes(marker: str, exclude: set[int] | frozenset[int] = frozenset(), *,
                          variable: str = MEMBER_RUN_ENV, prefix: bool = False) -> dict[int, str]:
    """Map pid to command for same-user processes carrying ``marker`` in ``variable``.

    Windows: the processes of the job named after ``marker`` (module
    docstring); OSError when there is none, or for ``prefix``, which a job
    name cannot match.
    """
    if not marker:
        return {}
    skipped = {os.getpid(), *exclude}
    from graftling.process_tree import job_name, windows

    if windows():
        if prefix:
            raise OSError('Windows lists a marker\'s processes through the job named after it; '
                          'a prefix match is not supported.')
        return {pid: _command(argv) for pid, argv in sorted(list_job(job_name(marker), skipped).items())}
    found = {}
    for pid in _user_pids():
        if pid in skipped or pid <= 1:
            continue
        argv = _carries(pid, marker, variable, prefix)
        if argv is not None:
            found[pid] = _command(argv)
    return found


def _signal_marked(pids: dict[int, str], marker: str, sig: int, variable: str, prefix: bool) -> None:
    for pid in pids:
        # Re-check right before signalling: the pid may have been reused.
        if _carries(pid, marker, variable, prefix) is not None:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass


def _wait_gone(pids: dict[int, str], marker: str, seconds: float, variable: str,
               prefix: bool) -> dict[int, str]:
    deadline = time.monotonic() + seconds
    alive = dict(pids)
    while alive:
        for pid in list(alive):
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            if _carries(pid, marker, variable, prefix) is None:
                alive.pop(pid)
        if not alive or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return alive


def stop_marked_processes(marker: str, exclude: set[int] | frozenset[int] = frozenset(),
                          grace: float = 3.0, *, variable: str = MEMBER_RUN_ENV,
                          prefix: bool = False) -> dict:
    """Stop processes left with this execution's marker; report what was found.

    SIGTERM first, SIGKILL after ``grace`` seconds. A process counts as stopped
    once it no longer exists with the marker (exited, zombie or pid reused).

    Windows: the job named after ``marker`` is terminated (module docstring);
    a process counts as stopped once it has left the job, waiting up to
    ``grace`` plus 2 seconds. No job is reported in ``error``.
    """
    from graftling.process_tree import job_name, windows

    if windows() and marker and not prefix:
        try:
            argvs, alive = stop_job(job_name(marker), {os.getpid(), *exclude}, grace + 2.0)
        except OSError as error:
            return {'found': [], 'stopped': [], 'remaining': [], 'error': str(error)}
        entry = lambda pid: {'pid': pid, 'command': _command(argvs[pid])}
        return {'found': [entry(pid) for pid in sorted(argvs)],
                'stopped': [entry(pid) for pid in sorted(argvs) if pid not in alive],
                'remaining': [entry(pid) for pid in sorted(alive)]}
    try:
        found = find_marked_processes(marker, exclude, variable=variable, prefix=prefix)
    except (OSError, subprocess.SubprocessError) as error:
        return {'found': [], 'stopped': [], 'remaining': [], 'error': str(error)}
    alive = found
    if found:
        _signal_marked(found, marker, signal.SIGTERM, variable, prefix)
        alive = _wait_gone(found, marker, grace, variable, prefix)
        if alive:
            _signal_marked(alive, marker, signal.SIGKILL, variable, prefix)
            alive = _wait_gone(alive, marker, 2.0, variable, prefix)
    entry = lambda pid: {'pid': pid, 'command': found[pid]}
    return {'found': [entry(pid) for pid in sorted(found)],
            'stopped': [entry(pid) for pid in sorted(found) if pid not in alive],
            'remaining': [entry(pid) for pid in sorted(alive)]}
