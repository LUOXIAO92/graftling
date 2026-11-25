"""Win32 calls graftling makes through ``ctypes``; called on Windows only.

Every foreign function is declared here once, with explicit argument and
return types, so callers never pass an unchecked integer for a handle or a
pointer. ``use_last_error`` keeps each call's error code per thread. The
module imports on every platform; kernel32 is bound only on Windows.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

# Error codes the callers distinguish.
ERROR_ACCESS_DENIED     = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION    = 33

# LockFileEx flags.
LOCKFILE_FAIL_IMMEDIATELY = 0x1
LOCKFILE_EXCLUSIVE_LOCK   = 0x2

# MoveFileExW flags.
MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH    = 0x8

# GetDriveTypeW answers.
DRIVE_REMOTE = 4


class OVERLAPPED(ctypes.Structure):
    """The OVERLAPPED structure; ``Offset`` and ``OffsetHigh`` place a lock.

    The union of the two offsets with ``Pointer`` is spelled as the offsets,
    which have the union's size and alignment on 32- and 64-bit Windows.
    """

    _fields_ = [
        ("Internal",     ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset",       wintypes.DWORD),
        ("OffsetHigh",   wintypes.DWORD),
        ("hEvent",       wintypes.HANDLE),
    ]


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _LockFileEx = _kernel32.LockFileEx
    _LockFileEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(OVERLAPPED),
    ]
    _LockFileEx.restype = wintypes.BOOL

    _UnlockFileEx = _kernel32.UnlockFileEx
    _UnlockFileEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED),
    ]
    _UnlockFileEx.restype = wintypes.BOOL

    _MoveFileExW = _kernel32.MoveFileExW
    _MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _MoveFileExW.restype  = wintypes.BOOL

    _GetVolumePathNameW = _kernel32.GetVolumePathNameW
    _GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _GetVolumePathNameW.restype  = wintypes.BOOL

    _GetVolumeInformationW = _kernel32.GetVolumeInformationW
    _GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPDWORD, wintypes.LPDWORD,
        wintypes.LPDWORD, wintypes.LPWSTR, wintypes.DWORD,
    ]
    _GetVolumeInformationW.restype = wintypes.BOOL

    _GetDriveTypeW = _kernel32.GetDriveTypeW
    _GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _GetDriveTypeW.restype  = wintypes.UINT


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
    if _LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(OVERLAPPED())):
        return True
    if not wait and ctypes.get_last_error() == ERROR_LOCK_VIOLATION:
        return False
    _raise_last_error()


def unlock_file(handle: int) -> None:
    """Release the lock ``lock_file`` took through this handle."""
    if not _UnlockFileEx(handle, 0, 1, 0, ctypes.byref(OVERLAPPED())):
        _raise_last_error()


def replace_file(source: str, target: str) -> int:
    """Rename ``source`` over ``target`` with write-through.

    Returns 0 on success, else the Win32 error code, so the caller decides
    which conflicts to retry; ``raise_error`` turns a code into ``OSError``.
    """
    if _MoveFileExW(source, target, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH):
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
    size = 32768
    root = ctypes.create_unicode_buffer(size)
    if not _GetVolumePathNameW(path, root, size):
        _raise_last_error()

    name = ctypes.create_unicode_buffer(261)
    if not _GetVolumeInformationW(root.value, None, 0, None, None, None, name, len(name)):
        _raise_last_error()

    return root.value, name.value, _GetDriveTypeW(root.value)
