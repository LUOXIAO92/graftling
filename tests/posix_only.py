"""Mark tests that exercise a POSIX mechanism by design, so Windows skips them by name (632).

A test is POSIX-only when what it checks, or the stand-in it needs, is a POSIX
mechanism: a process group (``start_new_session``, ``os.killpg``), a POSIX
signal, ``fcntl``, permission bits, or a fake executable that is a ``#!``
script. A behaviour that is platform-neutral but fails on Windows because of
graftling's own code is not POSIX-only: it runs on Windows and fails, and the
Windows workflow's expected list names it.

Every Windows skip reason starts with ``PREFIX``, which the Windows workflow's
step script checks; any other skip reason on Windows is reported.

- ``pytestmark = posix_only(reason)`` or ``@posix_only(reason)`` skips a module
  or a test before it runs.
- ``require_posix(reason)`` skips from the point where a test or a helper first
  needs the mechanism, so the platform-neutral part before it still runs.
"""

from __future__ import annotations

import sys

import pytest

PREFIX = 'POSIX-only: '
WINDOWS = sys.platform == 'win32'


def posix_only(reason: str) -> pytest.MarkDecorator:
    """Skip on Windows with ``PREFIX + reason``."""
    return pytest.mark.skipif(WINDOWS, reason=PREFIX + reason)


def require_posix(reason: str) -> None:
    """Skip the running test on Windows with ``PREFIX + reason``."""
    if WINDOWS:
        pytest.skip(PREFIX + reason)


# Reasons shared by several modules.
SHEBANG = "the stand-in executable is a '#!' script run directly; Windows needs a .exe or .cmd stand-in"
SH_SCRIPT = "the stand-in executable is a '#!/bin/sh' script"
