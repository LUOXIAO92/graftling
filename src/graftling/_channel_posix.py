"""The owner connection on POSIX: a control directory of request directories.

Moved unchanged from :mod:`graftling.connection` (636); that module selects
this transport on every platform but Windows and keeps the public functions.

The host holds ``flock`` on its ``control-*`` directory for its lifetime. A
caller publishes ``request.json`` in its own request directory while holding a
POSIX record lock on it; the host reads the lock's owning PID from the kernel,
authenticates it, and unlinks ``request.json`` before running the operation.
That unlink is the admission point: a caller that times out wins by unlinking
first.
"""
from __future__ import annotations

import contextvars
import ctypes
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from graftling.calls import pause
from graftling.connection import (OPERATION_TIMEOUT_SECONDS, SERVE_LOG_SECONDS, _failure_text, _log_failure,
                                  _log_text, _unexpected)
from graftling.tool import AgentsError

if os.name != 'nt':
    import fcntl


@contextmanager
def worker(
    directory: Path,
    operate: Callable[[dict], dict],
    *,
    authenticate: Callable[[int], None] | None = None,
    close_seconds: float | None = None,
) -> Iterator[str]:
    """:func:`graftling.connection.worker_connection` over a control directory."""
    stop = threading.Event()
    closing = threading.Event()
    with tempfile.TemporaryDirectory(prefix='control-', dir=directory) as address:
        # Held for the host's lifetime; the kernel releases it if the host dies.
        host_lock = os.open(address, os.O_RDONLY | os.O_DIRECTORY)
        fcntl.flock(host_lock, fcntl.LOCK_EX)

        accepted: dict[str, threading.Thread] = {}

        def refuse_closed(request: dict) -> dict:
            """Reject late requests without invoking the operation during close."""
            raise AgentsError('request-not-accepted', 'The Session control channel is closing.')

        def answer(name: str, descriptor: int) -> None:
            """Answer one request in its own empty context, then release its slot."""
            try:
                _answer_request(descriptor, refuse_closed if closing.is_set() else operate, authenticate)
            finally:
                os.close(descriptor)
                del accepted[name]

        def take_requests() -> None:
            """Answer each request on its own thread so a slow one cannot block others."""
            for entry in Path(address).iterdir():
                if entry.name in accepted:
                    continue
                try:
                    descriptor = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                except OSError:
                    continue
                try:
                    os.stat('request.json', dir_fd=descriptor, follow_symlinks=False)
                except OSError:
                    os.close(descriptor)  # Not yet sent, or already answered.
                    continue
                try:
                    # A fresh Context keeps caller identity ContextVars per request.
                    accepted[entry.name] = worker = threading.Thread(
                        target=contextvars.Context().run, args=(answer, entry.name, descriptor), daemon=True)
                    worker.start()
                except BaseException:
                    # The request is still unclaimed, so a later pass can take it.
                    accepted.pop(entry.name, None)
                    os.close(descriptor)
                    raise

        def serve() -> None:
            """Keep polling until stopped: one failed pass must not refuse every later call.

            A removed control directory cannot receive another request, so the
            service stops once, with its reason, instead of polling a missing path.
            """
            next_log = 0.0
            while not stop.is_set():
                try:
                    take_requests()
                except BaseException as error:  # noqa: BLE001 - this thread's last line of defence
                    if not os.path.isdir(address):
                        _log_text(f'Agents control directory {address} no longer exists; '
                                  'its connection service stopped.')
                        return
                    if time.monotonic() >= next_log:
                        next_log = time.monotonic() + SERVE_LOG_SECONDS
                        _log_failure(error)
                stop.wait(0.01)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield address
        finally:
            closing.set()
            limit = None if close_seconds is None else time.monotonic() + close_seconds

            def join_accepted() -> None:
                """Let each request taken finish and publish its reply, up to ``limit``."""
                for running in list(accepted.values()):
                    running.join(None if limit is None else max(0.0, limit - time.monotonic()))

            join_accepted()
            # A caller removes its request directory after consuming the reply.
            # Keep acknowledged results available across fast native completion.
            deadline = limit if limit is not None else time.monotonic() + OPERATION_TIMEOUT_SECONDS
            while _has_entries(address) and time.monotonic() < deadline:
                time.sleep(0.01)
            stop.set()
            thread.join()
            join_accepted()
            os.close(host_lock)


def host_alive(address: str) -> bool:
    """Observe the serving host's channel lock without taking it over."""
    try:
        descriptor = os.open(address, os.O_RDONLY | os.O_DIRECTORY)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True  # Unobservable here: never take a live host for a dead one.
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return True  # Unobservable here; the reply or channel removal still ends the wait.
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def _answer_request(
    directory: int,
    operate: Callable[[dict], dict],
    authenticate: Callable[[int], None] | None,
) -> None:
    """Pin the request directory and never follow caller-controlled file links."""
    try:
        descriptor = os.open('request.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError:
        return
    try:
        with os.fdopen(descriptor, encoding='utf-8') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError('A control request must be a regular file with one link.')
            if authenticate is not None:
                authenticate(record_lock_owner(stream.fileno()))
            request = json.load(stream)
        os.unlink('request.json', dir_fd=directory)
        result = operate(request)
    except AgentsError as error:
        result = {'error': {'code': error.code, 'message': error.message}}
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, OSError):
            # The reply keeps its format; the host keeps where the call failed.
            _log_failure(error)
        result = {'error': {'code': 'operation-failed', 'message': _failure_text(error)}}
    except BaseException as error:  # noqa: BLE001 - the caller waits on this reply for as long as the host lives
        result = _unexpected(error)
    try:
        # Serialised first, so a result that cannot be written is a refusal, not silence.
        reply = json.dumps(result)
    except BaseException as error:  # noqa: BLE001 - this reply must still be written
        reply = json.dumps(_unexpected(error))
    try:
        descriptor = os.open('response.tmp', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(reply)
        os.replace('response.tmp', 'response.json', src_dir_fd=directory, dst_dir_fd=directory)
        try:
            os.unlink('request.json', dir_fd=directory)
        except FileNotFoundError:
            pass
    except FileNotFoundError:
        pass  # Caller EOF does not cancel an accepted native operation.
    except OSError as error:
        # The caller cannot be told; the host keeps why it was left waiting.
        _log_failure(error)


def _has_entries(address: str) -> bool:
    """Whether requests remain; a removed directory holds none."""
    try:
        return any(Path(address).iterdir())
    except FileNotFoundError:
        return False


def exchange(
    address: str,
    document: dict,
    *,
    authenticate: bool,
    response_timeout: float,
    await_accepted: bool,
    stoppable: bool,
) -> dict:
    """Publish one request and return the host's reply document as read.

    The caller (:func:`graftling.connection.connection_operation`) checks the
    arguments, raises the reply's error and maps OS and parse failures.
    """
    published = False
    try:
        with tempfile.TemporaryDirectory(dir=address) as directory:
            request = Path(directory) / 'request.tmp'
            with request.open('w', encoding='utf-8') as stream:
                if authenticate:
                    fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                stream.write(json.dumps(document))
                stream.flush()
                os.replace(request, request.with_suffix('.json'))
                published = True
                response_file = Path(directory) / 'response.json'
                accepted_file = request.with_suffix('.json')
                deadline = time.monotonic() + response_timeout
                while not response_file.is_file():
                    if await_accepted and not accepted_file.exists():
                        # The host removes the request when it accepts it. A
                        # long native operation is then awaited until its reply
                        # or until the host closes this channel.
                        if (not Path(directory).is_dir()
                                or time.monotonic() >= deadline and not host_alive(address)):
                            raise TimeoutError('The Session host closed before replying; '
                                               'the accepted operation is unconfirmed.')
                    elif time.monotonic() >= deadline:
                        try:
                            # Removing an unclaimed request wins admission:
                            # the host must unlink it before calling operate.
                            accepted_file.unlink()
                        except FileNotFoundError:
                            raise TimeoutError('Session control timed out; delivery is unconfirmed.')
                        raise AgentsError('request-not-accepted', 'The Session host did not accept the request.')
                    if stoppable:
                        pause(0.01)
                    else:
                        time.sleep(0.01)
                return json.loads(response_file.read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        if not published:
            raise AgentsError('request-not-accepted', 'The Session control channel closed before the request.') from error
        raise


def record_lock_owner(descriptor: int) -> int:
    """Read the POSIX write-lock owner from the kernel, failing closed elsewhere.

    Darwin and Linux use different native ``struct flock`` field orderings.
    The client holds a whole-file lock until it consumes the host response.
    """
    if sys.platform == 'darwin':
        fields = [('start', ctypes.c_int64), ('length', ctypes.c_int64),
                  ('pid', ctypes.c_int), ('type', ctypes.c_short), ('whence', ctypes.c_short)]
    elif sys.platform.startswith('linux'):
        fields = [('type', ctypes.c_short), ('whence', ctypes.c_short),
                  ('start', ctypes.c_int64), ('length', ctypes.c_int64), ('pid', ctypes.c_int)]
    else:
        raise AgentsError('authority-denied', 'This platform cannot verify CLI request ownership.')

    class FileLock(ctypes.Structure):
        """Native ABI record used only for F_GETLK, never caller supplied data."""
        _fields_ = fields

    query = FileLock()
    query.type = fcntl.F_WRLCK
    owner = FileLock.from_buffer_copy(fcntl.fcntl(descriptor, fcntl.F_GETLK, bytes(query)))
    if owner.type != fcntl.F_WRLCK or owner.pid <= 0:
        raise AgentsError('authority-denied', 'The CLI request has no live kernel-verified owner.')
    return owner.pid
