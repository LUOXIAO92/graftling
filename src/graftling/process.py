"""Read OS process ancestry without Task mappings or external commands.

Windows (633): the executable, arguments, parent and liveness are read through
Win32 (``graftling._win32``). Ancestry there is weaker than on POSIX: a
process of the same user can start a child that names any process it can open
as that child's parent, so a walk can pass through a forged parent. A parent is
believed only when it was created before its child, which rules out a reused
PID but not a forged one. Callers must not treat Windows ancestry as equivalent
to the POSIX kernel record.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import subprocess

from graftling import _win32

# A control request walks from its own process to the top of the tree. The
# limit only keeps a damaged or cyclic parent chain from looping forever.
ANCESTOR_LIMIT = 64

# On macOS the parent is read through libproc, which needs no helper process
# that a restricted Agent environment might not be able to start. The flavor
# and byte offsets are the documented PROC_PIDT_SHORTBSDINFO record, whose
# first two fields are pbsi_pid and pbsi_ppid.
_LIBPROC_PATH = "/usr/lib/libproc.dylib"
_PROC_PIDT_SHORTBSDINFO = 13
_SHORTBSDINFO_PPID_OFFSET = 4
_SHORTBSDINFO_BUFFER_SIZE = 64


def process_ancestors(pid: int) -> list[int]:
    """Return one process and its ancestors, nearest first.

    Parameters
    ----------
    pid
        Identifier of the process whose tree to walk, usually ``os.getpid()``.

    Returns
    -------
    The supplied identifier followed by its parent, its grandparent and so on
    until the top of the tree, a repeated identifier fails to resolve, or
    ``ANCESTOR_LIMIT`` entries are reached. A platform whose parent record
    cannot be read simply ends the walk there.

    On Windows the parent is the PID recorded when the process was created,
    accepted only when that process was created earlier than the child;
    otherwise (a reused PID, or a parent that cannot be opened) the walk ends.
    A same-user process can forge the recorded parent, so on Windows the
    chain is weaker evidence than on POSIX (module docstring).
    """
    chain: list[int] = []
    seen: set[int] = set()
    while pid and pid not in seen and len(chain) < ANCESTOR_LIMIT:
        seen.add(pid)
        chain.append(pid)
        pid = _process_parent(pid)
    return chain


def _process_parent(pid: int) -> int:
    """Return one process's parent identifier, or 0 when it cannot be read."""

    if sys.platform == "darwin":
        return _darwin_parent(pid)
    if sys.platform.startswith("linux"):
        return _linux_parent(pid)
    if sys.platform == "win32":
        return _windows_parent(pid)
    return 0


def _windows_parent(pid: int) -> int:
    """Return the recorded parent of ``pid`` when it was created before ``pid``, else 0.

    Windows never updates a recorded parent PID, so once the parent ends the
    PID can belong to an unrelated, later process. Equal creation times are
    refused too: the rule is "created before", and the clock cannot tell.
    """
    facts = _win32.parent_and_creation(pid)
    if facts is None or facts[0] <= 0:
        return 0
    parent, created = facts
    parent_created = _win32.creation_ticks(parent)
    if parent_created is None or not parent_created < created:
        return 0
    return parent


def _darwin_parent(pid: int) -> int:
    """Read one parent identifier through libproc, without spawning a helper."""

    library = _libproc()
    if library is None:
        return 0
    buffer = ctypes.create_string_buffer(_SHORTBSDINFO_BUFFER_SIZE)
    written = library.proc_pidinfo(
        pid, _PROC_PIDT_SHORTBSDINFO, 0, buffer, len(buffer)
    )
    if written < _SHORTBSDINFO_PPID_OFFSET + 4:
        return 0
    return int.from_bytes(
        buffer.raw[_SHORTBSDINFO_PPID_OFFSET:_SHORTBSDINFO_PPID_OFFSET + 4],
        sys.byteorder,
    )


def _libproc() -> ctypes.CDLL | None:
    """Return the loaded libproc, loading and configuring it at most once."""

    if not hasattr(_libproc, "library"):
        try:
            library = ctypes.CDLL(_LIBPROC_PATH, use_errno=True)
            library.proc_pidinfo.restype = ctypes.c_int
            library.proc_pidinfo.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
        except (OSError, AttributeError):
            library = None
        _libproc.library = library
    return _libproc.library


def _linux_parent(pid: int) -> int:
    """Read one parent identifier from the kernel's process record."""

    try:
        record = Path("/proc/{0}/stat".format(pid)).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return 0
    # The second field is the command in parentheses and may contain spaces,
    # so the state and parent are read after the last closing parenthesis.
    fields = record.rpartition(")")[2].split()
    if len(fields) < 2 or not fields[1].isdigit():
        return 0
    return int(fields[1])



def authenticate_process(process: subprocess.Popen | None, caller_pid: int) -> None:
    """Require a kernel-identified caller below the host's still-live child.

    ``process`` is the owning host's Popen handle, never request JSON. Keeping
    that handle and checking poll prevents a stale PID from granting authority.
    """
    from graftling.tool import AgentsError

    if process is None or process.poll() is not None or process.pid not in process_ancestors(caller_pid):
        raise AgentsError('authority-denied', 'The request is not owned by this live native process.')


def process_executable(pid: int) -> Path | None:
    """Read the kernel's executable path for a live process, without argv aliases.

    Windows: QueryFullProcessImageNameW (a Win32 path); None when the process
    cannot be opened with limited query rights.
    """
    if sys.platform == "win32":
        path = _win32.image_path(pid)
        return None if path is None else Path(path)
    if sys.platform.startswith("linux"):
        try:
            return Path(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            return None
    if sys.platform == "darwin":
        library = _libproc()
        if library is not None:
            buffer = ctypes.create_string_buffer(4096)
            library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            library.proc_pidpath.restype = ctypes.c_int
            if library.proc_pidpath(pid, buffer, len(buffer)) > 0:
                return Path(os.fsdecode(buffer.value))
    return None
def process_arguments(pid: int) -> list[str]:
    """Read kernel argument boundaries rather than splitting formatted ps text.

    Windows keeps one command-line string in the process's own memory: it is
    read with NtQueryInformationProcess (ProcessCommandLineInformation) and
    split with CommandLineToArgvW, the convention Python's ``subprocess``
    quotes for. The target can rewrite that string, as POSIX processes can
    their argument records, so arguments only constrain an already verified
    executable. Raises OSError when the process cannot be read.
    """
    if sys.platform == 'win32':
        return _win32.split_command_line(_win32.command_line(pid))
    if sys.platform.startswith('linux'):
        return [os.fsdecode(arg) for arg in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if arg]
    if sys.platform != 'darwin':
        raise OSError('Exact process arguments are unavailable on this platform')
    # Darwin KERN_PROCARGS2 contains argc, executable path, padding, then argv.
    library = ctypes.CDLL(None, use_errno=True)
    query = (ctypes.c_int * 3)(1, 49, pid)
    buffer = ctypes.create_string_buffer(os.sysconf('SC_ARG_MAX'))
    size = ctypes.c_size_t(len(buffer))
    if library.sysctl(query, 3, buffer, ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Process arguments are unavailable')
    raw = buffer.raw[:size.value]
    count = int.from_bytes(raw[:4], sys.byteorder)
    start = raw.index(b'\0', 4) + 1
    while start < len(raw) and raw[start] == 0:
        start += 1
    arguments = raw[start:].split(b'\0')[:count]
    if len(arguments) != count or count < 1:
        raise OSError('Process argument record is incomplete')
    return [os.fsdecode(arg) for arg in arguments]


def process_alive(pid: object, started: float | None = None) -> bool:
    """Return whether ``pid`` names a process that runs, without signalling it.

    Parameters
    ----------
    pid
        The process identifier. Anything but a positive ``int`` (``bool``
        included) is not alive.
    started
        Optional time, in seconds since the Unix epoch like ``time.time()``,
        taken at or after the moment the process was known to be started. A
        process with this PID created later than ``started`` is a reused PID
        and is not alive. ``None`` skips that check. When the creation time
        cannot be read, the process is taken as alive.

    Returns
    -------
    POSIX: ``kill(pid, 0)`` finds the process (``EPERM`` counts as found) and
    it is not exiting or a zombie (:func:`graftling.tool_descendants.has_exited`).
    Windows: the process opens and a zero-timeout wait finds it not signalled;
    a process this user may not open counts as alive, and only "no such
    process" as gone. ``os.kill(pid, 0)`` is never used there: on Windows it
    terminates the target.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    running = _win32.process_running(pid) if sys.platform == 'win32' else _posix_running(pid)
    if not running or started is None:
        return running
    created = _process_created(pid)
    return created is None or created <= started


def _posix_running(pid: int) -> bool:
    """``kill(pid, 0)`` finds ``pid`` and it is not exiting or a zombie (496)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    from graftling.tool_descendants import has_exited

    return not has_exited(pid)


def _process_created(pid: int) -> float | None:
    """Return when ``pid`` was created, in seconds since the Unix epoch, or None when unreadable.

    Each platform's value is at or before the true moment (Windows and Linux
    round down to their clock tick; Linux's boot time is whole seconds), so a
    process is never taken as created after a ``started`` read later.
    """
    if sys.platform == 'win32':
        return _win32.creation_seconds(pid)
    try:
        if sys.platform == 'darwin':
            library = _libproc()
            # PROC_PIDTBSDINFO=3: struct proc_bsdinfo (136 bytes); pbi_start_tvsec
            # at 120 and pbi_start_tvusec at 128, as tool_descendants reads them.
            buffer = ctypes.create_string_buffer(136)
            if library is None or library.proc_pidinfo(pid, 3, 0, buffer, 136) < 136:
                return None
            seconds = int.from_bytes(buffer.raw[120:128], sys.byteorder)
            return seconds + int.from_bytes(buffer.raw[128:136], sys.byteorder) / 1_000_000
        if sys.platform.startswith('linux'):
            record = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8')
            ticks = int(record.rpartition(')')[2].split()[19])
            boot = next(int(line.split()[1]) for line in Path('/proc/stat').read_text(encoding='utf-8').splitlines()
                        if line.startswith('btime '))
            return boot + ticks / os.sysconf('SC_CLK_TCK')
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    return None
