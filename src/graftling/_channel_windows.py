r"""The owner connection on Windows: one named pipe per host (636; 629 advice 3.1).

:mod:`graftling.connection` selects this transport on Windows and keeps the
public functions. The address is still the host's ``control-*`` directory,
created and removed as on POSIX; it holds nothing. The pipe is named after it,
``\\.\pipe\graftling-`` and the SHA-256 of the resolved, case-folded address.
The host creates the first instance with FILE_FLAG_FIRST_PIPE_INSTANCE, so a
second creator of that name fails instead of serving silently. Every instance
is message mode, rejects remote clients and has a DACL admitting only the
host's user (:func:`graftling._win32.create_pipe`).

One exchange per connection; each message is one JSON object:

- caller to host ``{"request": document}``;
- host to caller ``{"accepted": true}``, just before the operation runs;
- host to caller ``{"reply": result}``, the result or ``{"error": ...}``;
- caller to host ``{"withdraw": true}`` at its deadline, while nothing was accepted;
- host to caller ``{"withdrawn": true}``: the operation will never run.

The host decides admission once, just before ``accepted``: a withdraw already
waiting, or a caller already gone, means the operation never runs. A caller
that withdrew reads the verdict: ``withdrawn`` is ``request-not-accepted``,
``accepted`` keeps POSIX's behaviour, and no answer within
``WITHDRAW_SECONDS`` leaves delivery unconfirmed. When the host dies the
kernel closes its pipe, so a caller waiting on an accepted request learns it at
once (broken pipe) instead of polling whether the host lives.

Host identity: the host's pipe is owned by its user. A caller reads the
owner of the pipe it opened and sends nothing unless it is its own user, and
:func:`host_alive` counts only such a pipe, so another account that creates
the name after a host died cannot read or answer requests.

Bounds: every read and write on a pipe waits at most OPERATION_TIMEOUT_SECONDS
(a caller's request write: its response deadline; the host also stops at
close), so a peer that never reads cannot hold a thread.

Caller identity: the host reads the client's PID from the pipe
(GetNamedPipeClientProcessId), opens that process and keeps it open until the
exchange ends, so the PID cannot name another process while ``authenticate``
runs. The process must be running and created before the connection. What the
PID is worth is the authentication rule's business: on Windows a same-user
process can forge its recorded parent (:mod:`graftling.process`).
"""
from __future__ import annotations

import contextvars
import hashlib
import itertools
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from graftling import _win32
from graftling.calls import pause
from graftling.connection import (OPERATION_TIMEOUT_SECONDS, SERVE_LOG_SECONDS, _failure_text, _log_failure,
                                  _log_text, _unexpected)
from graftling.tool import AgentsError

PIPE_PREFIX = '\\\\.\\pipe\\graftling-'
# How long a caller that withdrew waits for the host's verdict.
WITHDRAW_SECONDS = 2.0
POLL_SECONDS = 0.01


def pipe_name(address: str) -> str:
    """Return the pipe that serves the control directory ``address``."""
    resolved = os.path.normcase(os.path.realpath(address))
    return PIPE_PREFIX + hashlib.sha256(resolved.encode('utf-8', 'surrogatepass')).hexdigest()


def _gone(error: OSError) -> bool:
    """Whether a pipe call failed because the other end is closed."""
    return getattr(error, 'winerror', None) in _win32.PIPE_GONE


@contextmanager
def worker(
    directory: Path,
    operate: Callable[[dict], dict],
    *,
    authenticate: Callable[[int], None] | None = None,
    close_seconds: float | None = None,
) -> Iterator[str]:
    """:func:`graftling.connection.worker_connection` over a named pipe."""
    with tempfile.TemporaryDirectory(prefix='control-', dir=directory) as address:
        with channel(address, operate, authenticate=authenticate, close_seconds=close_seconds):
            yield address


@contextmanager
def channel(
    address: str,
    operate: Callable[[dict], dict],
    *,
    authenticate: Callable[[int], None] | None = None,
    close_seconds: float | None = None,
) -> Iterator[None]:
    """Serve the pipe of the existing control directory ``address`` for the block.

    Raises ``host-busy`` when a pipe of this name already exists in any
    process: the host must not serve beside or behind another one.
    """
    stop = threading.Event()
    closing = threading.Event()
    name = pipe_name(address)
    try:
        listening = [_win32.create_pipe(name, first=True)]
    except OSError as error:
        if getattr(error, 'winerror', None) == _win32.ERROR_ACCESS_DENIED:
            raise AgentsError('host-busy', f'Another process already has the control pipe of {address}.') from error
        raise
    connections: dict[int, threading.Thread] = {}
    numbers = itertools.count()

    def refuse_closed(request: dict) -> dict:
        """Reject late requests without invoking the operation during close."""
        raise AgentsError('request-not-accepted', 'The Session control channel is closing.')

    def answer(number: int, handle: int, connected: int) -> None:
        """Answer one connection in its own empty context, then release it."""
        try:
            _answer_request(handle, connected, refuse_closed if closing.is_set() else operate, authenticate, stop)
        finally:
            _win32.close(handle)
            del connections[number]

    def removed() -> bool:
        return not os.path.isdir(address)

    def take_request() -> None:
        """Wait for one client and answer it on its own thread so a slow one cannot block others."""
        if not _win32.connect_pipe(listening[0], lambda: stop.is_set() or removed()):
            return
        connected = _win32.now_ticks()
        try:
            # The next instance listens before this one is handed over, so the name never lapses.
            successor = _win32.create_pipe(name, first=False)
        except BaseException:
            _win32.disconnect_pipe(listening[0])
            raise
        handle, listening[0] = listening[0], successor
        number = next(numbers)
        try:
            # A fresh Context keeps caller identity ContextVars per request.
            connections[number] = thread = threading.Thread(
                target=contextvars.Context().run, args=(answer, number, handle, connected), daemon=True)
            thread.start()
        except BaseException:
            connections.pop(number, None)
            _win32.close(handle)
            raise

    def serve() -> None:
        """Keep listening until stopped: one failed pass must not refuse every later call.

        A removed control directory names no channel any more, so the
        service stops once, with its reason, and its pipe goes away.
        """
        next_log = 0.0
        try:
            while not stop.is_set():
                if removed():
                    _log_text(f'Agents control directory {address} no longer exists; '
                              'its connection service stopped.')
                    return
                try:
                    take_request()
                except BaseException as error:  # noqa: BLE001 - this thread's last line of defence
                    if time.monotonic() >= next_log:
                        next_log = time.monotonic() + SERVE_LOG_SECONDS
                        _log_failure(error)
                    stop.wait(POLL_SECONDS)
        finally:
            _win32.close(listening[0])

    thread = threading.Thread(target=serve, daemon=True)
    try:
        thread.start()
    except BaseException:
        _win32.close(listening[0])
        raise
    try:
        yield
    finally:
        closing.set()
        limit = None if close_seconds is None else time.monotonic() + close_seconds

        def join_connections() -> None:
            """Let each connection taken finish, its caller take the reply and leave, up to ``limit``."""
            for running in list(connections.values()):
                running.join(None if limit is None else max(0.0, limit - time.monotonic()))

        join_connections()
        # Callers arriving during the close are refused; let them take that answer.
        deadline = limit if limit is not None else time.monotonic() + OPERATION_TIMEOUT_SECONDS
        while connections and time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
        stop.set()
        thread.join()
        join_connections()


def _answer_request(
    handle: int,
    connected: int,
    operate: Callable[[dict], dict],
    authenticate: Callable[[int], None] | None,
    stop: threading.Event,
) -> None:
    """Authenticate the pinned client, admit its request once, run it and reply."""
    process = None
    try:
        try:
            if authenticate is not None:
                try:
                    pid, process = _win32.pin_pipe_client(handle, connected)
                except OSError as error:
                    raise AgentsError('authority-denied',
                                      'The CLI request has no live kernel-verified owner.') from error
            body = _receive(handle, stop)
            if body is None:
                return  # The caller left, or sent nothing in time.
            if set(body) != {'request'}:
                raise ValueError('A control connection must start with one request.')
            if authenticate is not None:
                authenticate(pid)
            if not _admit(handle, stop):
                _await_disconnect(handle, stop)  # Let a caller that withdrew read the verdict.
                return
            result = operate(body['request'])
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
            reply = json.dumps({'reply': result})
        except BaseException as error:  # noqa: BLE001 - this reply must still be written
            reply = json.dumps({'reply': _unexpected(error)})
        try:
            # Bounded: a caller that never reads a reply larger than the pipe's buffer cannot hold this thread.
            _win32.write_message(handle, reply.encode('utf-8'), _bound(), stop.is_set)
        except OSError as error:
            if not _gone(error):
                # The caller cannot be told; the host keeps why it was left waiting.
                _log_failure(error)
            return  # Caller EOF does not cancel an accepted native operation.
        _await_disconnect(handle, stop)
    finally:
        if process is not None:
            _win32.close(process)


def _bound() -> float:
    """How long one host read or write may wait for its caller: OPERATION_TIMEOUT_SECONDS from now."""
    return time.monotonic() + OPERATION_TIMEOUT_SECONDS


def _receive(handle: int, stop: threading.Event) -> dict | None:
    """Return the caller's first message, or None when it leaves or sends nothing in time."""
    deadline = _bound()
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            if _win32.peek_pipe(handle):
                return _decode(_win32.read_message(handle, _bound(), stop.is_set))
        except OSError as error:
            if _gone(error):
                return None
            raise
        stop.wait(POLL_SECONDS)
    return None


def _admit(handle: int, stop: threading.Event) -> bool:
    """The admission point: accept unless the caller withdrew or left, and tell it which."""
    try:
        if _win32.peek_pipe(handle):
            # Only a withdraw may follow the request; whatever it is, nothing runs.
            _win32.read_message(handle, _bound(), stop.is_set)
            _win32.write_message(handle, json.dumps({'withdrawn': True}).encode('utf-8'), _bound(), stop.is_set)
            return False
        _win32.write_message(handle, json.dumps({'accepted': True}).encode('utf-8'), _bound(), stop.is_set)
        return True
    except OSError as error:
        if _gone(error):
            return False
        raise


def _await_disconnect(handle: int, stop: threading.Event) -> None:
    """Keep the instance until the caller has taken the reply and closed its end, bounded."""
    deadline = _bound()
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            if _win32.peek_pipe(handle):
                # A late withdraw: the verdict was already sent.
                _win32.read_message(handle, _bound(), stop.is_set)
        except OSError:
            return
        stop.wait(POLL_SECONDS)


def _decode(message: bytes) -> dict:
    """One protocol message: a JSON object."""
    body = json.loads(message.decode('utf-8'))
    if not isinstance(body, dict):
        raise ValueError('A control message must be a JSON object.')
    return body


def host_alive(address: str) -> bool:
    """Whether a pipe this user owns serves ``address``; unobservable counts as alive.

    A pipe of the name that another account created, or whose DACL refuses
    this user, is no host of this user's. Every instance busy, or an owner
    that cannot be read, is unobservable.
    """
    try:
        if not stat.S_ISDIR(os.stat(address).st_mode):
            return False
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True  # Unobservable here: never take a live host for a dead one.
    try:
        handle = _win32.open_pipe(pipe_name(address), exchange=False)
    except OSError as error:
        return getattr(error, 'winerror', None) not in (_win32.ERROR_FILE_NOT_FOUND, _win32.ERROR_ACCESS_DENIED)
    try:
        return _win32.pipe_owner_sid(handle) == _win32.current_user_sid()
    except OSError:
        return True  # Unobservable owner: never take a live host for a dead one.
    finally:
        _win32.close(handle)


def _require_own_pipe(handle: int, address: str) -> None:
    """Refuse a pipe this user does not own, or whose owner cannot be read, before sending anything."""
    try:
        owner = _win32.pipe_owner_sid(handle)
    except OSError as error:
        raise AgentsError('authority-denied',
                          f'The control pipe of {address} cannot be verified as this user\'s.') from error
    if owner != _win32.current_user_sid():
        raise AgentsError('authority-denied', f'The control pipe of {address} belongs to another account.')


def _connect(address: str, deadline: float) -> int:
    """Open the host's pipe, waiting for a free instance until ``deadline``."""
    name = pipe_name(address)
    while True:
        if not os.path.isdir(address):
            raise AgentsError('request-not-accepted', 'The Session control channel closed before the request.')
        try:
            return _win32.open_pipe(name)
        except OSError as error:
            if getattr(error, 'winerror', None) == _win32.ERROR_FILE_NOT_FOUND:
                raise AgentsError('request-not-accepted',
                                  'The Session control channel closed before the request.') from error
            if getattr(error, 'winerror', None) != _win32.ERROR_PIPE_BUSY:
                raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentsError('request-not-accepted', 'The Session host did not accept the request.')
        _win32.wait_pipe(name, int(min(remaining, 0.05) * 1000))


def exchange(
    address: str,
    document: dict,
    *,
    authenticate: bool,
    response_timeout: float,
    await_accepted: bool,
    stoppable: bool,
) -> dict:
    """Send one request over the host's pipe and return the reply document as read.

    ``authenticate`` asks nothing more here: the host always reads the
    caller's PID from the pipe. The caller
    (:func:`graftling.connection.connection_operation`) checks the arguments,
    raises the reply's error and maps OS and parse failures.
    """
    deadline = time.monotonic() + response_timeout
    handle = _connect(address, deadline)
    try:
        # Only this user's host may read the request: after a host died, or in the
        # moment its pipe closes, another account could have created the name.
        _require_own_pipe(handle, address)
        try:
            _win32.write_message(handle, json.dumps({'request': document}).encode('utf-8'), deadline)
        except OSError as error:
            if getattr(error, 'winerror', None) == _win32.ERROR_OPERATION_ABORTED:
                raise AgentsError('request-not-accepted', 'The Session host did not accept the request.') from error
            if _gone(error):
                raise AgentsError('request-not-accepted',
                                  'The Session control channel closed before the request.') from error
            raise
        accepted = False
        withdraw_by = None
        while True:
            try:
                body = (_decode(_win32.read_message(handle, time.monotonic() + OPERATION_TIMEOUT_SECONDS))
                        if _win32.peek_pipe(handle) else None)
            except OSError as error:
                if not _gone(error):
                    raise
                if accepted:
                    raise TimeoutError('The Session host closed before replying; '
                                       'the accepted operation is unconfirmed.') from error
                raise TimeoutError('The Session host closed before answering; delivery is unconfirmed.') from error
            if body is not None:
                if 'reply' in body:
                    return body['reply']
                if body.get('withdrawn') is True:
                    raise AgentsError('request-not-accepted', 'The Session host did not accept the request.')
                if body.get('accepted') is not True:
                    raise ValueError('Unexpected control message from the Session host.')
                accepted = True
                continue
            now = time.monotonic()
            if now >= deadline:
                if accepted:
                    if not await_accepted:
                        raise TimeoutError('Session control timed out; delivery is unconfirmed.')
                elif withdraw_by is None:
                    try:
                        _win32.write_message(handle, json.dumps({'withdraw': True}).encode('utf-8'),
                                             now + WITHDRAW_SECONDS)
                    except OSError as error:
                        if not _gone(error):
                            raise
                        raise TimeoutError('The Session host closed before answering; '
                                           'delivery is unconfirmed.') from error
                    withdraw_by = now + WITHDRAW_SECONDS
                elif now >= withdraw_by:
                    raise TimeoutError('Session control timed out; delivery is unconfirmed.')
            if stoppable:
                pause(POLL_SECONDS)
            else:
                time.sleep(POLL_SECONDS)
    finally:
        _win32.close(handle)
