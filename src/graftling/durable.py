"""Whole-file replacement that readers see as either the old or the new document.

Every graftling record, archive, configuration and sidecar file is published
through ``replace_durably``: the content goes to a temporary file in the same
directory, which is then renamed over the destination.

POSIX keeps each caller's durability as it was: the caller says whether the
temporary file is flushed to disk and whether the directory is flushed after
the rename. Windows cannot open a directory to flush it; the rename is made
with write-through instead, and a rename refused because another handle
holds the destination open (a reader, a virus scanner) is retried briefly.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

# How long a Windows rename refused by another open handle is retried, in seconds.
REPLACE_RETRY_SECONDS = 2.0

# File systems whose file identifiers are not reused while a reader may still
# hold an older identity, so (volume, file id, size, mtime) detects a change.
CHANGE_DETECTING_FILE_SYSTEMS = ("NTFS", "ReFS")


def replace_durably(
    path: Path,
    data: bytes,
    *,
    sync_file: bool = True,
    sync_directory: bool = False,
) -> None:
    """Atomically replace ``path`` with ``data``.

    Parameters
    ----------
    path
        The destination; its directory must exist. The temporary file is
        ``.<name>.<random>.tmp`` beside it, created with mode 0600 and removed
        on any failure.
    data
        The complete new content.
    sync_file
        Flush the temporary file to disk before the rename.
    sync_directory
        POSIX only: flush the directory after the rename, so the new name
        survives a crash. On Windows the rename is always written through.

    Raises
    ------
    OSError
        The write or rename failed. On Windows a sharing or access-denied
        conflict is raised once it persists for ``REPLACE_RETRY_SECONDS``.
    """
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            if sync_file:
                os.fsync(stream.fileno())

        if os.name == "nt":
            _replace_windows(temporary, str(path))
            return

        os.replace(temporary, path)
        if sync_directory:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _replace_windows(source: str, target: str) -> None:
    """Rename with write-through, retrying open-handle conflicts for a bounded time."""
    from graftling import _win32

    retried = (_win32.ERROR_ACCESS_DENIED, _win32.ERROR_SHARING_VIOLATION)
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    delay = 0.005

    while True:
        code = _win32.replace_file(source, target)
        if code == 0:
            return
        if code not in retried or time.monotonic() >= deadline:
            _win32.raise_error(code, source, target)

        # Back off up to 0.1 s; a reader usually closes within milliseconds.
        time.sleep(delay)
        delay = min(delay * 2, 0.1)


def require_change_detection(directory: Path) -> None:
    """Refuse, on Windows, a directory not on a local NTFS or ReFS volume.

    A Session archive recognizes another writer's replacement by the file's
    volume, identifier, size and modification time. That is sound only where
    identifiers are not reused, which graftling relies on for local NTFS and
    ReFS volumes. POSIX hosts are not checked.

    Raises
    ------
    ValueError
        The volume is remote or has another file system; the message names the
        directory, the volume and what it found.
    """
    if os.name != "nt":
        return

    from graftling import _win32

    root, file_system, drive_type = _win32.volume_facts(str(directory))
    if drive_type == _win32.DRIVE_REMOTE or file_system not in CHANGE_DETECTING_FILE_SYSTEMS:
        where = "a network volume" if drive_type == _win32.DRIVE_REMOTE else "a local volume"
        raise ValueError(
            f"graftling data on Windows needs a local NTFS or ReFS volume: {directory} is on "
            f"{where} {root} with file system {file_system or 'unknown'}. "
            "Set GRAFTLING_DATA_ROOT to a directory on a local NTFS or ReFS drive."
        )
