"""Refuse a Runtime that needs symbolic links where this process cannot create them.

Every Runtime's Trace entry is a symbolic link to its native Session file, and
Pi's private agent directory links the native login and model files. On
Windows, creating a symbolic link needs Developer Mode (or administrator
rights). A copy or a hard link is not equivalent where the Runtime rewrites
the target, so such a Runtime refuses with the reason instead of falling back
(Windows support decision 4). POSIX can always create them.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from graftling.runtime import RuntimeAdapterError


def symbolic_links_available() -> bool:
    """Return whether this process can create a symbolic link.

    Windows is probed by creating one in a temporary directory, which is what
    Developer Mode or administrator rights change.
    """
    if os.name != 'nt':
        return True
    with tempfile.TemporaryDirectory(prefix='graftling-link-') as directory:
        try:
            os.symlink('target', Path(directory) / 'link')
        except OSError:
            return False
    return True


def require_symbolic_links(runtime: str) -> None:
    """Raise ``runtime-unavailable`` naming ``runtime`` when links cannot be created."""
    if not symbolic_links_available():
        raise RuntimeAdapterError(
            'runtime-unavailable',
            f'{runtime} needs symbolic links, which this Windows account cannot create. '
            'Turn on Windows Developer Mode (Settings > System > For developers) and start again.',
        )
