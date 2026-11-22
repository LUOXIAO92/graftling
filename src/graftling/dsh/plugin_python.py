"""The Python the graftling-dsh plugin runs when ``GRAFTLING_PYTHON`` is not set.

When DSH installs the whole graftling repository as its plugin (``dsh plugin add
git+https://github.com/LUOXIAO92/graftling.git#<commit>``), the plugin's own
package holds graftling's Python sources. ``plugin.mjs`` then runs this file with
``python3`` from ``PATH`` (any Python, so that an old one can be named)::

    python3 -I <this file> <DSH home>

It prints the absolute path of a Python that imports this copy of graftling and
exits 0, or prints one line naming the fix on stderr and exits 1.

The environment is ``<DSH home>/graftling-python/<version>-<key>``: a stdlib venv
with ``--system-site-packages`` and a plain copy of this ``graftling`` directory in
its ``site-packages``. Nothing is downloaded and no package index is used.
``<key>`` is a hash of the copied files and of the base Python, so different or
upgraded sources, or another base Python, get their own environment; the same
sources in several profiles share one. PyYAML and websockets come from the base
Python; when it lacks them the error names the one command that installs them into
the environment.

A build happens under an exclusive lock on ``<version>-<key>.lock``: a second start
waits for it and then uses the result. It is built in ``<version>-<key>.partial``
and renamed into place only once complete, so a killed or failed build is never
used; the next build removes the leftover first.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path

SOURCE   = Path(__file__).resolve().parents[1]    # the graftling package directory
MINIMUM  = (3, 12)
# pyproject.toml's dependencies as (distribution, lowest major, first excluded major).
REQUIRES = (('PyYAML', 6, 7), ('websockets', 15, 17))
REQUIREMENTS = ' '.join(shlex.quote(f'{name}>={low},<{high}') for name, low, high in REQUIRES)
# Run by the environment's Python: names of the required distributions it lacks.
MISSING = f'''
import importlib.metadata as metadata
for name, low, high in {REQUIRES!r}:
    try:
        major = int(metadata.version(name).split('.')[0])
    except Exception:
        major = None
    if major is None or not low <= major < high:
        print(name)
'''
# Shown with every refusal: the other way, which this file never overrides.
INSTEAD = 'Or set GRAFTLING_PYTHON to a Python that has graftling installed (docs/install/dsh.md).'


class Refusal(Exception):
    """A start this file refuses, with a message that names the fix."""


def source_files() -> list[Path]:
    """Return the files copied into the environment, sorted, without bytecode caches.

    Links (``install``, ``PUBLIC_API.md``) are followed, as ``shutil.copytree`` follows them.
    """
    found = []
    for directory, names, files in os.walk(SOURCE, followlinks=True):
        names[:] = sorted(name for name in names if name != '__pycache__')
        found += [Path(directory, name) for name in files if Path(directory, name).is_file()]
    return sorted(found)


def environment_name() -> str:
    """Return ``<version>-<key>``: the key hashes the copied files and the base Python.

    The base Python is its path and minor version, not its patch release, so a patch
    update of the same interpreter keeps the environment.
    """
    digest = hashlib.sha256(f'{sys.executable}\0{sys.version_info[:2]}\0'.encode())
    for path in source_files():
        digest.update(path.relative_to(SOURCE).as_posix().encode() + b'\0')
        digest.update(path.read_bytes() + b'\0')
    version = re.search(r'''^__version__ = ["']([^"']+)''',
                        (SOURCE / '__init__.py').read_text(encoding='utf-8'), re.M)
    return f'{version.group(1) if version else "unknown"}-{digest.hexdigest()[:16]}'


def build(target: Path) -> None:
    """Create the venv at ``target`` and copy this graftling package into it."""
    venv.EnvBuilder(system_site_packages=True, symlinks=os.name != 'nt').create(target)
    purelib = Path(sysconfig.get_path('purelib', vars={'base': str(target), 'platbase': str(target)}))
    shutil.copytree(SOURCE, purelib / 'graftling', ignore=shutil.ignore_patterns('__pycache__'))


def lock(path: Path):
    """Open and exclusively lock ``path``; the lock ends when the file closes or the process exits."""
    stream = path.open('a+b')
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    return stream


def environment(home: Path) -> Path:
    """Return the environment for these sources under ``home``, building it once."""
    base  = home / 'graftling-python'
    name  = environment_name()
    final = base / name
    if final.is_dir():
        return final

    base.mkdir(parents=True, exist_ok=True)
    with lock(base / f'{name}.lock'):
        # Another start may have finished it while this one waited.
        if final.is_dir():
            return final
        partial = base / f'{name}.partial'
        shutil.rmtree(partial, ignore_errors=True)    # left by a killed or failed build
        try:
            build(partial)
        except Exception as error:
            raise Refusal(f'building the Python environment {final} failed: {error}. Fix that and '
                          f'start DSH again; the unfinished copy {partial} is never used. {INSTEAD}') from error
        os.rename(partial, final)
    return final


def python_of(final: Path) -> Path:
    """Return the environment's Python, refusing while it lacks PyYAML or websockets."""
    python  = final / 'bin' / 'python'
    missing = subprocess.run([str(python), '-I', '-c', MISSING],
                             capture_output=True, text=True, check=True).stdout.split()
    if missing:
        quoted = shlex.quote(str(python))
        raise Refusal(f'the Python environment {final} lacks {" and ".join(missing)}, and {sys.executable} '
                      f'does not provide a suitable version. Install them into the environment once (pip downloads '
                      f'them from PyPI), then start DSH again: {quoted} -m ensurepip && {quoted} -m pip install '
                      f'{REQUIREMENTS}. {INSTEAD}')
    return python


def main(arguments: list[str]) -> int:
    """Print the Python for ``arguments[0]`` (the DSH home) and return 0, or explain and return 1."""
    try:
        if sys.version_info < MINIMUM:
            raise Refusal(f'python3 on PATH is {sys.executable}, Python {sys.version.split()[0]}; graftling '
                          f'needs Python 3.12 or later. Put a Python 3.12+ first on PATH as python3 and start '
                          f'DSH again. {INSTEAD}')
        print(python_of(environment(Path(arguments[0]))))
        return 0
    except Refusal as refusal:
        print(f'graftling-dsh: {refusal}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
