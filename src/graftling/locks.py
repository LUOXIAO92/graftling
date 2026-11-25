"""Cross-process exclusive locks on dedicated lock files.

Data files are never locked themselves; they are replaced atomically, so
readers on any platform see either the old or the new document.

A lock belongs to one open handle of the lock file: separate threads of one
process exclude each other just like separate processes, and the kernel
releases the lock when its holder ends. POSIX uses ``flock``; Windows uses
``LockFileEx`` on byte 0, which waits without a time limit.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

if os.name != "nt":
    import fcntl
else:
    import msvcrt

    from graftling import _win32


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` (created if missing) for the block.

    Waits as long as another holder keeps it.
    """
    with _locked(path, wait=True):
        yield


@contextmanager
def try_exclusive(path: Path) -> Iterator[bool]:
    """Take an exclusive lock on ``path`` (created if missing) only if it is free.

    Yields ``True`` while this block holds the lock, ``False`` at once when
    another holder has it (nothing is held then).

    Examples
    --------
    >>> with try_exclusive(directory / 'owner.lock') as owned:
    ...     if not owned:
    ...         raise RuntimeError('another owner is running')
    """
    with _locked(path, wait=False) as held:
        yield held


@contextmanager
def _locked(path: Path, wait: bool) -> Iterator[bool]:
    """Open ``path`` and lock it through this handle; yield whether it is held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        descriptor = stream.fileno()
        if not _acquire(descriptor, wait):
            yield False
            return
        try:
            yield True
        finally:
            _release(descriptor)


def _acquire(descriptor: int, wait: bool) -> bool:
    """Lock the open file; ``False`` only when ``wait`` is off and it is held elsewhere."""
    if os.name == "nt":
        return _win32.lock_file(msvcrt.get_osfhandle(descriptor), wait)

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _release(descriptor: int) -> None:
    """Unlock the open file before it is closed."""
    if os.name == "nt":
        _win32.unlock_file(msvcrt.get_osfhandle(descriptor))
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def ignore_in_git(*directories: Path) -> None:
    """Keep each existing graftling-named runtime directory out of git (526).

    A data root may sit inside a user's git work tree, where a tool listing
    changed files (DSH's per-turn changes card) would show graftling's own
    archives and records as edits. A ``.gitignore`` holding ``*`` inside the
    directory ignores it and itself. Only these subdirectories are touched,
    never the caller's root; an existing ``.gitignore`` is kept, and a failed
    write is ignored so the record itself is never affected.
    """
    for directory in directories:
        try:
            if directory.is_dir():
                with (directory / ".gitignore").open("x", encoding="utf-8") as stream:
                    stream.write("*\n")
        except OSError:
            pass
