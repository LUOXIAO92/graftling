"""Win32 calls graftling makes through ctypes, each declared once (629 advice 3.7).

Importing this module touches no Windows-only name, so it imports on every
platform. The libraries are loaded, and every function's argument and return
types declared, on the first call of :func:`_api`, which only Windows code
paths reach. Types are spelled with fixed sizes (``DWORD`` is 32 bits) because
``ctypes.wintypes`` follows the host's ``long`` on other platforms.

The process functions (633, W1) open the process with limited query rights,
close the handle on every path, and raise :class:`OSError` or return ``None``
as documented. The file functions (634, W2) lock a lock file through an open
handle, rename with write-through, and describe a volume. The job functions
(635, W3) create, open, fill, list, probe and terminate the Job Objects behind
``graftling.process_tree``. The pipe functions (636, W4) create, connect, read
and write the owner connection's named pipe and pin its client process; they
raise :class:`OSError` carrying the Windows error.
"""
from __future__ import annotations

import ctypes
from typing import Any, Callable

DWORD = ctypes.c_uint32
ULONG = ctypes.c_uint32
BOOL = ctypes.c_int
HANDLE = ctypes.c_void_p
NTSTATUS = ctypes.c_int32
UINT = ctypes.c_uint32

# Access rights (processthreadsapi.h) and results (winbase.h, winerror.h).
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_INVALID_PARAMETER = 87
# LockFileEx flags (fileapi.h).
LOCKFILE_FAIL_IMMEDIATELY = 0x1
LOCKFILE_EXCLUSIVE_LOCK = 0x2
# MoveFileExW flags (winbase.h).
MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH = 0x8
# GetDriveTypeW answer for a network volume (fileapi.h).
DRIVE_REMOTE = 4
# Named pipes (winbase.h, fileapi.h, winnt.h) and their errors (winerror.h), 636.
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
PIPE_ACCESS_DUPLEX = 0x3
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
FILE_FLAG_OVERLAPPED = 0x40000000
PIPE_TYPE_MESSAGE = 0x4
PIPE_READMODE_MESSAGE = 0x2
PIPE_REJECT_REMOTE_CLIENTS = 0x8
PIPE_UNLIMITED_INSTANCES = 255
PIPE_BUFFER_BYTES = 65536
# A client lets the server identify it, never impersonate it.
SECURITY_SQOS_PRESENT = 0x00100000
SECURITY_IDENTIFICATION = 0x00010000
ERROR_FILE_NOT_FOUND = 2
ERROR_BROKEN_PIPE = 109
ERROR_PIPE_BUSY = 231
ERROR_NO_DATA = 232
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_MORE_DATA = 234
ERROR_PIPE_CONNECTED = 535
ERROR_OPERATION_ABORTED = 995
ERROR_IO_PENDING = 997
# The pipe's other end is closed, or was never connected.
PIPE_GONE = (ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED)
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# The calling process's token and its user (winnt.h TOKEN_QUERY, TOKEN_INFORMATION_CLASS TokenUser).
TOKEN_QUERY = 0x8
TOKEN_USER_CLASS = 1
SDDL_REVISION_1 = 1
# PROCESSINFOCLASS values (winternl.h; 60 is available since Windows 8.1).
PROCESS_BASIC_INFORMATION_CLASS = 0
PROCESS_COMMAND_LINE_INFORMATION = 60
# FILETIME counts 100 ns ticks since 1601-01-01 UTC.
FILETIME_TICKS_PER_SECOND = 10_000_000
FILETIME_UNIX_EPOCH = 116_444_736_000_000_000
LARGEST_PID = 0xFFFFFFFF


class FILETIME(ctypes.Structure):
    """``FILETIME``: two DWORDs, low part first."""

    _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    """``PROCESS_BASIC_INFORMATION``; 48 bytes on 64-bit Windows.

    winternl.h names the last field ``Reserved3``; it holds the parent PID
    recorded at creation (``InheritedFromUniqueProcessId``), never updated.
    """

    _fields_ = [
        ("ExitStatus", NTSTATUS),
        ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_size_t),
        ("BasePriority", ctypes.c_int32),
        ("UniqueProcessId", ctypes.c_size_t),
        ("InheritedFromUniqueProcessId", ctypes.c_size_t),
    ]


class OVERLAPPED(ctypes.Structure):
    """``OVERLAPPED``; ``Offset`` and ``OffsetHigh`` place a byte-range lock.

    The union of the two offsets with ``Pointer`` is spelled as the offsets,
    which have the union's size and alignment on 32- and 64-bit Windows.
    """

    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", DWORD),
        ("OffsetHigh", DWORD),
        ("hEvent", HANDLE),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    """``SECURITY_ATTRIBUTES``: the pipe's security descriptor, not inheritable."""

    _fields_ = [("nLength", DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", BOOL)]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    """``SID_AND_ATTRIBUTES``; ``TOKEN_USER`` is this one structure."""

    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", DWORD)]


class UNICODE_STRING(ctypes.Structure):
    """``UNICODE_STRING``: ``Length`` in bytes, without a terminator; 16 bytes on 64-bit."""

    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


# Job objects (635, 629 advice 3.3): access rights (winnt.h), limits, information
# classes (JOBOBJECTINFOCLASS) and process creation flags (winbase.h).
JOB_OBJECT_ASSIGN_PROCESS = 0x0001
JOB_OBJECT_SET_ATTRIBUTES = 0x0002
JOB_OBJECT_QUERY = 0x0004
JOB_OBJECT_TERMINATE = 0x0008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_DUP_HANDLE = 0x0040
PROCESS_SET_QUOTA = 0x0100
PROCESS_SUSPEND_RESUME = 0x0800
CREATE_SUSPENDED = 0x00000004
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
ERROR_FILE_NOT_FOUND = 2
ERROR_ALREADY_EXISTS = 183
ERROR_MORE_DATA = 234


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    """``JOBOBJECT_BASIC_LIMIT_INFORMATION``; 64 bytes on 64-bit Windows."""

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    """``IO_COUNTERS``: six 64-bit counters."""

    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION``; 144 bytes on 64-bit Windows."""

    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    """``JOBOBJECT_BASIC_ACCOUNTING_INFORMATION``; 48 bytes."""

    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", DWORD),
        ("TotalProcesses", DWORD),
        ("ActiveProcesses", DWORD),
        ("TotalTerminatedProcesses", DWORD),
    ]


def _api() -> Any:
    """Load kernel32, shell32, ntdll and advapi32 once and declare the functions used here."""
    if not hasattr(_api, "loaded"):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        overlapped = ctypes.POINTER(OVERLAPPED)
        declarations = (
            (kernel32.OpenProcess, [DWORD, BOOL, DWORD], HANDLE),
            (kernel32.CloseHandle, [HANDLE], BOOL),
            (kernel32.QueryFullProcessImageNameW,
             [HANDLE, DWORD, ctypes.c_wchar_p, ctypes.POINTER(DWORD)], BOOL),
            (kernel32.GetProcessTimes, [HANDLE] + [ctypes.POINTER(FILETIME)] * 4, BOOL),
            (kernel32.WaitForSingleObject, [HANDLE, DWORD], DWORD),
            (kernel32.LocalFree, [ctypes.c_void_p], ctypes.c_void_p),
            (kernel32.LockFileEx,
             [HANDLE, DWORD, DWORD, DWORD, DWORD, ctypes.POINTER(OVERLAPPED)], BOOL),
            (kernel32.UnlockFileEx, [HANDLE, DWORD, DWORD, DWORD, ctypes.POINTER(OVERLAPPED)], BOOL),
            (kernel32.MoveFileExW, [ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD], BOOL),
            (kernel32.GetVolumePathNameW, [ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD], BOOL),
            (kernel32.GetVolumeInformationW,
             [ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD, ctypes.POINTER(DWORD), ctypes.POINTER(DWORD),
              ctypes.POINTER(DWORD), ctypes.c_wchar_p, DWORD], BOOL),
            (kernel32.GetDriveTypeW, [ctypes.c_wchar_p], UINT),
            (shell32.CommandLineToArgvW, [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)],
             ctypes.POINTER(ctypes.c_wchar_p)),
            (ntdll.NtQueryInformationProcess,
             [HANDLE, ctypes.c_int, ctypes.c_void_p, ULONG, ctypes.POINTER(ULONG)], NTSTATUS),
            # Named pipes, overlapped I/O and the pipe's security descriptor (636).
            (kernel32.CreateNamedPipeW,
             [ctypes.c_wchar_p, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES)],
             HANDLE),
            (kernel32.ConnectNamedPipe, [HANDLE, overlapped], BOOL),
            (kernel32.DisconnectNamedPipe, [HANDLE], BOOL),
            (kernel32.CreateFileW, [ctypes.c_wchar_p, DWORD, DWORD, ctypes.c_void_p, DWORD, DWORD, HANDLE], HANDLE),
            (kernel32.ReadFile, [HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD), overlapped], BOOL),
            (kernel32.WriteFile, [HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD), overlapped], BOOL),
            (kernel32.PeekNamedPipe,
             [HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD), ctypes.POINTER(DWORD), ctypes.POINTER(DWORD)],
             BOOL),
            (kernel32.SetNamedPipeHandleState,
             [HANDLE, ctypes.POINTER(DWORD), ctypes.POINTER(DWORD), ctypes.POINTER(DWORD)], BOOL),
            (kernel32.WaitNamedPipeW, [ctypes.c_wchar_p, DWORD], BOOL),
            (kernel32.GetNamedPipeClientProcessId, [HANDLE, ctypes.POINTER(ULONG)], BOOL),
            (kernel32.CreateEventW, [ctypes.c_void_p, BOOL, BOOL, ctypes.c_wchar_p], HANDLE),
            (kernel32.GetOverlappedResult, [HANDLE, overlapped, ctypes.POINTER(DWORD), BOOL], BOOL),
            (kernel32.CancelIoEx, [HANDLE, overlapped], BOOL),
            (kernel32.GetSystemTimePreciseAsFileTime, [ctypes.POINTER(FILETIME)], None),
            (advapi32.OpenProcessToken, [HANDLE, DWORD, ctypes.POINTER(HANDLE)], BOOL),
            (advapi32.GetTokenInformation,
             [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)], BOOL),
            (advapi32.ConvertSidToStringSidW, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], BOOL),
            (advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW,
             [ctypes.c_wchar_p, DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ULONG)], BOOL),
            # Job objects (635).
            (kernel32.CreateJobObjectW, [ctypes.c_void_p, ctypes.c_wchar_p], HANDLE),
            (kernel32.OpenJobObjectW, [DWORD, BOOL, ctypes.c_wchar_p], HANDLE),
            (kernel32.SetInformationJobObject, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD], BOOL),
            (kernel32.QueryInformationJobObject,
             [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)], BOOL),
            (kernel32.AssignProcessToJobObject, [HANDLE, HANDLE], BOOL),
            (kernel32.TerminateJobObject, [HANDLE, ctypes.c_uint], BOOL),
            (kernel32.IsProcessInJob, [HANDLE, HANDLE, ctypes.POINTER(BOOL)], BOOL),
            (kernel32.TerminateProcess, [HANDLE, ctypes.c_uint], BOOL),
            (kernel32.GetCurrentProcess, [], HANDLE),
            (kernel32.DuplicateHandle,
             [HANDLE, HANDLE, HANDLE, ctypes.POINTER(HANDLE), DWORD, BOOL, DWORD], BOOL),
            (ntdll.NtResumeProcess, [HANDLE], NTSTATUS),
        )
        for function, argtypes, restype in declarations:
            function.argtypes, function.restype = argtypes, restype
        _api.loaded = (kernel32, shell32, ntdll, advapi32)
    return _api.loaded


def _open(pid: int, access: int = PROCESS_QUERY_LIMITED_INFORMATION) -> int:
    """Open ``pid``; raise OSError carrying the Windows error code when that fails."""
    if not 0 < pid <= LARGEST_PID:
        raise ctypes.WinError(ERROR_INVALID_PARAMETER)
    handle = _api()[0].OpenProcess(access, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _close(handle: int) -> None:
    _api()[0].CloseHandle(handle)


def _created(handle: int) -> int | None:
    """Return the process's creation time in FILETIME ticks, or None when unreadable."""
    times = [FILETIME() for _ in range(4)]
    if not _api()[0].GetProcessTimes(handle, *(ctypes.byref(time) for time in times)):
        return None
    return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime


def creation_ticks(pid: int) -> int | None:
    """Return ``pid``'s creation time in FILETIME ticks, or None when it cannot be opened or read."""
    try:
        handle = _open(pid)
    except OSError:
        return None
    try:
        return _created(handle)
    finally:
        _close(handle)


def creation_seconds(pid: int) -> float | None:
    """Return ``pid``'s creation time as seconds since the Unix epoch, or None when unreadable."""
    ticks = creation_ticks(pid)
    return None if ticks is None else (ticks - FILETIME_UNIX_EPOCH) / FILETIME_TICKS_PER_SECOND


def parent_and_creation(pid: int) -> tuple[int, int] | None:
    """Return ``pid``'s recorded parent PID and its own creation ticks, read through one handle.

    None when the process cannot be opened or either fact cannot be read.
    """
    try:
        handle = _open(pid)
    except OSError:
        return None
    try:
        information = PROCESS_BASIC_INFORMATION()
        status = _api()[2].NtQueryInformationProcess(
            handle, PROCESS_BASIC_INFORMATION_CLASS, ctypes.byref(information),
            ctypes.sizeof(information), None)
        created = _created(handle)
        if status < 0 or created is None:
            return None
        return int(information.InheritedFromUniqueProcessId), created
    finally:
        _close(handle)


def image_path(pid: int) -> str | None:
    """Return ``pid``'s executable as a Win32 path (QueryFullProcessImageNameW), or None."""
    try:
        handle = _open(pid)
    except OSError:
        return None
    try:
        size = DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _api()[0].QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer[:size.value]
    finally:
        _close(handle)


def command_line(pid: int) -> str:
    """Return ``pid``'s command line as the process holds it (ProcessCommandLineInformation).

    Raises OSError when the process cannot be opened or the line cannot be read.
    """
    handle = _open(pid)
    try:
        query = _api()[2].NtQueryInformationProcess
        size = ULONG(0)
        status = 0
        # The first call reports the size; a line that grows in between is asked again.
        for _ in range(3):
            # 8-byte words keep the UNICODE_STRING at the start of the buffer aligned.
            words = max(size.value, ctypes.sizeof(UNICODE_STRING)) // 8 + 1
            buffer = (ctypes.c_uint64 * words)()
            status = query(handle, PROCESS_COMMAND_LINE_INFORMATION, buffer, ctypes.sizeof(buffer),
                           ctypes.byref(size))
            if status >= 0:
                # Buffer points just after the structure, inside this same buffer.
                text = UNICODE_STRING.from_buffer(buffer)
                return ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Length else ""
            if size.value <= ctypes.sizeof(buffer):
                break
        raise OSError(f"Process arguments are unavailable (NTSTATUS 0x{status & 0xFFFFFFFF:08X})")
    finally:
        _close(handle)


def split_command_line(line: str) -> list[str]:
    """Split a command line with CommandLineToArgvW; an empty line has no arguments.

    CommandLineToArgvW returns the calling program's own path for an empty
    line, so that case never reaches it.
    """
    if not line:
        return []
    shell32 = _api()[1]
    count = ctypes.c_int(0)
    arguments = shell32.CommandLineToArgvW(line, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        _api()[0].LocalFree(ctypes.cast(arguments, ctypes.c_void_p))


def process_running(pid: int) -> bool:
    """Return whether ``pid`` names a process that has not exited (zero-timeout wait).

    A process this user may not open exists, so it counts as running, like
    POSIX ``kill`` answering ``EPERM``. Only "no such process" counts as gone;
    any other failure counts as running, so a live process is never taken for
    a dead one. Nothing is signalled or terminated.
    """
    try:
        handle = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE)
    except OSError as error:
        return getattr(error, "winerror", None) != ERROR_INVALID_PARAMETER
    try:
        return _api()[0].WaitForSingleObject(handle, 0) != WAIT_OBJECT_0
    finally:
        _close(handle)


# -- Job objects (635) --------------------------------------------------------
#
# graftling's owned process trees on Windows (graftling.process_tree). Handles
# returned here are the caller's to close with close_handle.


def close_handle(handle: int) -> None:
    """Close a handle returned by this module."""
    _api()[0].CloseHandle(handle)


def create_job(name: str, kill_on_close: bool) -> int:
    """Create the job ``name`` and return its handle; refuse a name that already exists.

    No limit allows breakaway, so ``CREATE_BREAKAWAY_FROM_JOB`` fails inside it
    and no process leaves it silently. ``kill_on_close`` ends every process in
    the job when its last handle closes. The handle is not inheritable.
    """
    kernel32 = _api()[0]
    ctypes.set_last_error(0)
    handle = kernel32.CreateJobObjectW(None, name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    try:
        if error == ERROR_ALREADY_EXISTS:
            # Another process made this name first; it is not ours to fill.
            raise FileExistsError(error, f"A job named {name!r} already exists")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE if kill_on_close else 0
        if not kernel32.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        close_handle(handle)
        raise
    return handle


def open_job(name: str) -> int:
    """Open the existing job ``name`` to query and terminate it; OSError when there is none."""
    handle = _api()[0].OpenJobObjectW(JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def adopt_suspended(job: int, pid: int, share: bool) -> None:
    """Assign the suspended process ``pid`` to ``job``, then resume it.

    The process was created with ``CREATE_SUSPENDED``, so it has run no
    instruction and started no child before it is in the job. ``share`` gives
    the process an inheritable query-only handle to the job: a named object
    keeps its name only while some handle to it is open, so the name outlives
    the owner while the Runtime lives (inference from the object manager's
    rules; checked by the Windows tests). On any failure the process is
    terminated and OSError raised.
    """
    kernel32, ntdll = _api()[0], _api()[2]
    handle = _open(pid, PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME
                   | PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION)
    try:
        try:
            if not kernel32.AssignProcessToJobObject(job, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            if share:
                shared = HANDLE()
                if not kernel32.DuplicateHandle(kernel32.GetCurrentProcess(), job, handle,
                                                ctypes.byref(shared), JOB_OBJECT_QUERY, True, 0):
                    raise ctypes.WinError(ctypes.get_last_error())
            status = ntdll.NtResumeProcess(handle)
            if status < 0:
                raise OSError(f"Cannot resume process {pid} (NTSTATUS 0x{status & 0xFFFFFFFF:08X})")
        except BaseException:
            kernel32.TerminateProcess(handle, 1)
            raise
    finally:
        close_handle(handle)


def terminate_job(job: int, exit_code: int = 1) -> None:
    """End every process in ``job`` (TerminateJobObject); OSError on failure."""
    if not _api()[0].TerminateJobObject(job, exit_code):
        raise ctypes.WinError(ctypes.get_last_error())


def job_active_processes(job: int) -> int:
    """Return the number of processes in ``job`` that have not ended."""
    information = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    if not _api()[0].QueryInformationJobObject(job, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                                               ctypes.byref(information), ctypes.sizeof(information), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(information.ActiveProcesses)


def job_process_ids(job: int) -> list[int]:
    """Return the PIDs of the processes in ``job`` (JobObjectBasicProcessIdList).

    The list is ``{DWORD assigned; DWORD listed; ULONG_PTR ids[]}``; the ids
    start at byte 8 on 32-bit and 64-bit Windows alike.
    """
    word = ctypes.sizeof(ctypes.c_size_t)
    header = 8 // word
    wanted = 64
    for _ in range(4):
        buffer = (ctypes.c_size_t * (header + wanted))()
        ok = _api()[0].QueryInformationJobObject(job, JOB_OBJECT_BASIC_PROCESS_ID_LIST, buffer,
                                                 ctypes.sizeof(buffer), None)
        counts = ctypes.cast(buffer, ctypes.POINTER(DWORD))
        # A short buffer either fails with ERROR_MORE_DATA or lists fewer than assigned.
        if ok and counts[1] >= counts[0]:
            return [int(buffer[header + index]) for index in range(counts[1])]
        error = ERROR_MORE_DATA if ok else ctypes.get_last_error()
        if error != ERROR_MORE_DATA:
            raise ctypes.WinError(error)
        wanted = max(wanted * 2, int(counts[0]) + 16)
    raise OSError("The job's process list kept growing")


def process_in_job(pid: int, job: int) -> bool:
    """Return whether ``pid`` is in ``job`` or one of its child jobs (IsProcessInJob).

    Raises OSError when the process cannot be opened; ``winerror`` 87 means
    there is no such process.
    """
    handle = _open(pid)
    try:
        result = BOOL(0)
        if not _api()[0].IsProcessInJob(handle, job, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(result.value)
    finally:
        close_handle(handle)


# --- Lock files, replacement and volumes (634, W2) ---


def _raise_last_error() -> None:
    """Raise the calling thread's last Win32 error as the matching ``OSError``."""
    raise ctypes.WinError(ctypes.get_last_error())


def lock_file(handle: int, wait: bool) -> bool:
    """Lock byte 0 of an open file exclusively through this handle.

    The lock belongs to the handle: other handles, in this process or
    another, are excluded, and the kernel releases it when the handle is
    closed or its process ends. With ``wait`` the call blocks without a time
    limit (the handle is synchronous); without it, return ``False`` at once
    when another handle holds the lock.
    """
    flags = LOCKFILE_EXCLUSIVE_LOCK | (0 if wait else LOCKFILE_FAIL_IMMEDIATELY)
    if _api()[0].LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(OVERLAPPED())):
        return True
    if not wait and ctypes.get_last_error() == ERROR_LOCK_VIOLATION:
        return False
    _raise_last_error()


def unlock_file(handle: int) -> None:
    """Release the lock ``lock_file`` took through this handle."""
    if not _api()[0].UnlockFileEx(handle, 0, 1, 0, ctypes.byref(OVERLAPPED())):
        _raise_last_error()


def replace_file(source: str, target: str) -> int:
    """Rename ``source`` over ``target`` with write-through.

    Returns 0 on success, else the Win32 error code, so the caller decides
    which conflicts to retry; ``raise_error`` turns a code into ``OSError``.
    """
    if _api()[0].MoveFileExW(source, target, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH):
        return 0
    return ctypes.get_last_error()


def raise_error(code: int, source: str, target: str) -> None:
    """Raise Win32 error ``code`` as ``OSError`` naming both paths."""
    error = ctypes.WinError(code)
    raise type(error)(error.errno, error.strerror, source, code, target)


def volume_facts(path: str) -> tuple[str, str, int]:
    """Return the volume root holding ``path``, its file system name and drive type.

    Examples
    --------
    ``volume_facts('C:\\\\data')`` gives ``('C:\\\\', 'NTFS', 3)`` on a local NTFS disk;
    a mapped network share answers drive type ``DRIVE_REMOTE``.
    """
    kernel32 = _api()[0]
    size = 32768
    root = ctypes.create_unicode_buffer(size)
    if not kernel32.GetVolumePathNameW(path, root, size):
        _raise_last_error()

    name = ctypes.create_unicode_buffer(261)
    if not kernel32.GetVolumeInformationW(root.value, None, 0, None, None, None, name, len(name)):
        _raise_last_error()

    return root.value, name.value, kernel32.GetDriveTypeW(root.value)


# --- The owner connection's named pipe (636, W4) ---


def close(handle: int) -> None:
    """Close a handle these functions returned."""
    _close(handle)


def now_ticks() -> int:
    """Return the system time in FILETIME ticks, as precisely as Windows keeps it."""
    now = FILETIME()
    _api()[0].GetSystemTimePreciseAsFileTime(ctypes.byref(now))
    return now.dwHighDateTime << 32 | now.dwLowDateTime


def current_user_sid() -> str:
    """Return the SID of this process's user, as text (``S-1-5-21-...``)."""
    if not hasattr(current_user_sid, "text"):
        kernel32, _, _, advapi32 = _api()
        token = HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            _raise_last_error()
        try:
            size = DWORD(0)
            advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(max(size.value, ctypes.sizeof(SID_AND_ATTRIBUTES)))
            if not advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, buffer, len(buffer), ctypes.byref(size)):
                _raise_last_error()
            text = ctypes.c_void_p()
            if not advapi32.ConvertSidToStringSidW(SID_AND_ATTRIBUTES.from_buffer(buffer).Sid, ctypes.byref(text)):
                _raise_last_error()
            try:
                current_user_sid.text = ctypes.wstring_at(text.value)
            finally:
                kernel32.LocalFree(text)
        finally:
            _close(token)
    return current_user_sid.text


def create_pipe(name: str, first: bool) -> int:
    """Create one instance of the pipe ``name`` and return its handle.

    Duplex, message mode, overlapped, remote clients rejected, and a protected
    DACL whose one entry admits this process's user: no other account can
    connect. With ``first`` the call fails (ERROR_ACCESS_DENIED) when a pipe of
    this name already exists in any process, so nobody can pre-empt the host.
    """
    kernel32, _, _, advapi32 = _api()
    descriptor = ctypes.c_void_p()
    # D:P: a protected DACL (nothing inherited); (A;;GA;;;SID): all access for this user only.
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;;GA;;;{current_user_sid()})", SDDL_REVISION_1, ctypes.byref(descriptor), None):
        _raise_last_error()
    try:
        attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor.value, False)
        handle = kernel32.CreateNamedPipeW(
            name, PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | (FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0),
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS,
            PIPE_UNLIMITED_INSTANCES, PIPE_BUFFER_BYTES, PIPE_BUFFER_BYTES, 0, ctypes.byref(attributes))
        if handle in (None, INVALID_HANDLE_VALUE):
            _raise_last_error()
        return handle
    finally:
        kernel32.LocalFree(descriptor)


def _event() -> int:
    """A new manual-reset event, not signalled, for one overlapped operation."""
    event = _api()[0].CreateEventW(None, True, False, None)
    if not event:
        _raise_last_error()
    return event


def connect_pipe(handle: int, stopped: Callable[[], bool]) -> bool:
    """Wait on the listening instance ``handle`` until a client connects.

    Returns True once one has (a client that already left counts; reading
    from it then fails), False when ``stopped()`` turned true first and the
    wait was cancelled. ``stopped`` is asked every 50 ms.
    """
    kernel32 = _api()[0]
    overlapped = OVERLAPPED(hEvent=_event())
    try:
        if kernel32.ConnectNamedPipe(handle, ctypes.byref(overlapped)):
            return True
        error = ctypes.get_last_error()
        if error in (ERROR_PIPE_CONNECTED, ERROR_NO_DATA):
            return True
        if error != ERROR_IO_PENDING:
            raise ctypes.WinError(error)
        while True:
            waited = kernel32.WaitForSingleObject(overlapped.hEvent, 50)
            if waited == WAIT_OBJECT_0:
                break
            if waited != WAIT_TIMEOUT or stopped():
                kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
                break
        done = DWORD(0)
        # Waiting here also after a cancel: the OVERLAPPED must outlive the operation.
        if kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(done), True):
            return True
        error = ctypes.get_last_error()
        if error == ERROR_OPERATION_ABORTED:
            return False
        raise ctypes.WinError(error)
    finally:
        _close(overlapped.hEvent)


def disconnect_pipe(handle: int) -> None:
    """Drop the client of the instance ``handle`` so it can listen again."""
    if not _api()[0].DisconnectNamedPipe(handle):
        _raise_last_error()


def open_pipe(name: str) -> int:
    """Connect to the pipe ``name`` as a client in message read mode; return the handle.

    The server may identify this client, never impersonate it. The OSError's
    ``winerror`` is ERROR_FILE_NOT_FOUND when there is no such pipe,
    ERROR_PIPE_BUSY when no instance is free and ERROR_ACCESS_DENIED when the
    pipe's DACL refuses this account.
    """
    kernel32 = _api()[0]
    handle = kernel32.CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING,
                                  SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, None)
    if handle in (None, INVALID_HANDLE_VALUE):
        _raise_last_error()
    mode = DWORD(PIPE_READMODE_MESSAGE)
    if not kernel32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
        error = ctypes.get_last_error()
        _close(handle)
        raise ctypes.WinError(error)
    return handle


def wait_pipe(name: str, milliseconds: int) -> int:
    """Wait up to ``milliseconds`` (at least 1) for a free instance of the pipe ``name``.

    Returns 0 when one is free, else the Windows error: ERROR_FILE_NOT_FOUND
    when no pipe of this name exists. Nothing connects.
    """
    if _api()[0].WaitNamedPipeW(name, max(1, milliseconds)):
        return 0
    return ctypes.get_last_error()


def peek_pipe(handle: int) -> int:
    """Return the size of the message waiting in ``handle``, 0 when none.

    Raises OSError (``winerror`` in ``PIPE_GONE``) when the other end is closed.
    """
    available, left = DWORD(0), DWORD(0)
    if not _api()[0].PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), ctypes.byref(left)):
        _raise_last_error()
    return left.value or available.value


def _transfer(function: Any, handle: int, buffer: Any, size: int, overlapped_io: bool) -> tuple[int, bool]:
    """Run ReadFile or WriteFile on ``handle`` to its end: (bytes moved, whether the message has more)."""
    done = DWORD(0)
    if not overlapped_io:
        if function(handle, buffer, size, ctypes.byref(done), None):
            return done.value, False
        error = ctypes.get_last_error()
        if error == ERROR_MORE_DATA:
            return done.value, True
        raise ctypes.WinError(error)
    overlapped = OVERLAPPED(hEvent=_event())
    try:
        if not function(handle, buffer, size, None, ctypes.byref(overlapped)):
            error = ctypes.get_last_error()
            if error not in (ERROR_IO_PENDING, ERROR_MORE_DATA):
                raise ctypes.WinError(error)
        if _api()[0].GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(done), True):
            return done.value, False
        error = ctypes.get_last_error()
        if error == ERROR_MORE_DATA:
            return done.value, True
        raise ctypes.WinError(error)
    finally:
        _close(overlapped.hEvent)


def read_message(handle: int, overlapped_io: bool) -> bytes:
    """Read one whole message from ``handle``; ``overlapped_io`` for a server instance."""
    parts = []
    size = max(peek_pipe(handle), 4096)
    while True:
        buffer = ctypes.create_string_buffer(size)
        count, more = _transfer(_api()[0].ReadFile, handle, buffer, size, overlapped_io)
        parts.append(buffer.raw[:count])
        if not more:
            return b"".join(parts)


def write_message(handle: int, data: bytes, overlapped_io: bool) -> None:
    """Write ``data`` to ``handle`` as one message; ``overlapped_io`` for a server instance."""
    count, _ = _transfer(_api()[0].WriteFile, handle, ctypes.create_string_buffer(data, len(data)), len(data),
                         overlapped_io)
    if count != len(data):
        raise OSError(f"Wrote {count} of {len(data)} bytes of a pipe message")


def pin_pipe_client(handle: int, connected: int) -> tuple[int, int]:
    """Open the client process of the connected instance ``handle``: (its PID, a handle to it).

    The process must still run and must have been created no later than
    ``connected`` (FILETIME ticks read once the connection was made), so a
    PID reused after the client ended is refused. While the returned handle
    is open the PID cannot name another process. Raises OSError otherwise.
    """
    pid = ULONG(0)
    if not _api()[0].GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
        _raise_last_error()
    process = _open(pid.value, PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE)
    try:
        created = _created(process)
        if _api()[0].WaitForSingleObject(process, 0) != WAIT_TIMEOUT:
            raise OSError(f"The pipe client process {pid.value} has ended")
        if created is None or created > connected:
            raise OSError(f"The pipe client process {pid.value} was created after the connection")
    except BaseException:
        _close(process)
        raise
    return pid.value, process
