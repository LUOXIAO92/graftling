"""Win32 calls graftling makes through ctypes, each declared once (629 advice 3.7).

Importing this module touches no Windows-only name, so it imports on every
platform. The libraries are loaded, and every function's argument and return
types declared, on the first call of :func:`_api`, which only Windows code
paths reach. Types are spelled with fixed sizes (``DWORD`` is 32 bits) because
``ctypes.wintypes`` follows the host's ``long`` on other platforms.

Every function opens the process with limited query rights, closes the handle
on every path, and raises :class:`OSError` or returns ``None`` as documented.
"""
from __future__ import annotations

import ctypes
from typing import Any

DWORD = ctypes.c_uint32
ULONG = ctypes.c_uint32
BOOL = ctypes.c_int
HANDLE = ctypes.c_void_p
NTSTATUS = ctypes.c_int32

# Access rights (processthreadsapi.h) and results (winbase.h, winerror.h).
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
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


class UNICODE_STRING(ctypes.Structure):
    """``UNICODE_STRING``: ``Length`` in bytes, without a terminator; 16 bytes on 64-bit."""

    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


def _api() -> Any:
    """Load kernel32, shell32 and ntdll once and declare the functions used here."""
    if not hasattr(_api, "loaded"):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        declarations = (
            (kernel32.OpenProcess, [DWORD, BOOL, DWORD], HANDLE),
            (kernel32.CloseHandle, [HANDLE], BOOL),
            (kernel32.QueryFullProcessImageNameW,
             [HANDLE, DWORD, ctypes.c_wchar_p, ctypes.POINTER(DWORD)], BOOL),
            (kernel32.GetProcessTimes, [HANDLE] + [ctypes.POINTER(FILETIME)] * 4, BOOL),
            (kernel32.WaitForSingleObject, [HANDLE, DWORD], DWORD),
            (kernel32.LocalFree, [ctypes.c_void_p], ctypes.c_void_p),
            (shell32.CommandLineToArgvW, [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)],
             ctypes.POINTER(ctypes.c_wchar_p)),
            (ntdll.NtQueryInformationProcess,
             [HANDLE, ctypes.c_int, ctypes.c_void_p, ULONG, ctypes.POINTER(ULONG)], NTSTATUS),
        )
        for function, argtypes, restype in declarations:
            function.argtypes, function.restype = argtypes, restype
        _api.loaded = (kernel32, shell32, ntdll)
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
    _, shell32, _ = _api()
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
