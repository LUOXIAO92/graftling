"""Standard streams of graftling's command entries, as UTF-8 with ``\\n`` line ends."""

from __future__ import annotations

import io
import sys


def use_utf8_stdio() -> None:
    """Read stdin and write stdout as UTF-8, ending written lines with ``\\n``.

    Every entry exchanges JSON lines with a Runtime, a plugin or a caller.
    Windows would otherwise decode a pipe in the ANSI code page and write
    ``\\r\\n``; POSIX already behaves this way. A stream an embedding caller
    replaced with a non-file object is left as it is.
    """
    if isinstance(sys.stdin, io.TextIOWrapper):
        sys.stdin.reconfigure(encoding='utf-8')
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding='utf-8', newline='\n')
