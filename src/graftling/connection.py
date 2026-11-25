"""Authenticated local transport, extracted from Runner's existing pipe.

No Task mappings, Runtime routing, control state or identity records live here.
The host supplies the business callback and kernel-PID authentication rule.
The transport itself is per platform: a control directory on POSIX
(:mod:`graftling._channel_posix`), a named pipe on Windows
(:mod:`graftling._channel_windows`); the functions here are the same on both.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys
import traceback
from contextlib import contextmanager
from typing import Callable, Iterator, Sequence

from graftling.extra_tools import ExtraTool
from graftling.tool import AgentsError, AgentsResult

OPERATION_TIMEOUT_SECONDS = 10.0
# A repeating failure of the channel's polling pass logs at most this often.
SERVE_LOG_SECONDS = 60.0
CONNECTION_ENV = 'GRAFTLING_CONNECTION'


def call_owner(request: dict, *, metadata: dict | None = None) -> AgentsResult:
    """Call the independent host bound to this process, without Task fallback.

    The environment locates the channel; its owner verifies the kernel PID
    and any native Session assertions before invoking the existing callback.
    A missing connection never creates a caller or controller.
    """
    address = os.environ.get(CONNECTION_ENV)
    if not address:
        raise AgentsError('authority-denied', 'The owning Agent connection is unavailable.')
    native = client_native_context(metadata)
    document = {'request': request, 'native': native} if native is not None else request
    result = connection_operation(address, document, authenticate=True, await_accepted=True)
    return AgentsResult(result['document'], failed=result['failed'])


@contextmanager
def worker_connection(
    directory: Path,
    operate: Callable[[dict], dict],
    *,
    authenticate: Callable[[int], None] | None = None,
    close_seconds: float | None = None,
) -> Iterator[str]:
    """Serve local requests without requiring a listening network/socket permission.

    By default closing waits for every accepted request and then for callers to
    take their replies. ``close_seconds`` caps all of that for a host that must
    end: a request still running then keeps its daemon thread until the process
    ends, and its caller is told the operation is unconfirmed.

    The address is a new ``control-*`` directory below ``directory``. POSIX
    serves requests through it (:mod:`graftling._channel_posix`); Windows serves
    a named pipe derived from it (:mod:`graftling._channel_windows`).
    """
    with _channel().worker(directory, operate, authenticate=authenticate, close_seconds=close_seconds) as address:
        yield address


def host_alive(address: str) -> bool:
    """Whether the host serving ``address`` still lives; unobservable counts as alive.

    POSIX observes the host's lock on the control directory; Windows, whether
    the host's pipe exists.
    """
    return _channel().host_alive(address)


def _channel():
    """The transport of this platform: a control directory, or on Windows a named pipe."""
    if os.name == 'nt':
        from graftling import _channel_windows
        return _channel_windows
    from graftling import _channel_posix
    return _channel_posix


def _log_text(text: str) -> None:
    """Write one line to the host log; an unwritable log never stops the channel."""
    try:
        print(text, file=sys.stderr, flush=True)
    except BaseException:  # noqa: BLE001 - diagnostics only
        pass


def _log_failure(error: BaseException) -> None:
    """Keep where a failure happened; an unwritable host log never stops the channel."""
    try:
        traceback.print_exception(error, file=sys.stderr)
    except BaseException:  # noqa: BLE001 - diagnostics only
        pass


def _failure_text(error: BaseException) -> str:
    """Return the failure's own text, or its type name when it has none it can give."""
    try:
        return str(error)
    except BaseException:  # noqa: BLE001 - the reply needs some text
        return type(error).__name__


def _unexpected(error: BaseException) -> dict:
    """Keep where a failure happened in the host's log and name it to the caller."""
    _log_failure(error)
    try:
        code = getattr(error, 'code', None)
    except BaseException:  # noqa: BLE001 - the reply falls back to the general code
        code = None
    name = type(error).__name__
    text = _failure_text(error)
    return {'error': {'code': code if isinstance(code, str) and code else 'operation-failed',
                      'message': name if text == name else f'{name}: {text}'}}


def connection_operation(
    address: str,
    document: dict,
    *,
    authenticate: bool = False,
    response_timeout: float = OPERATION_TIMEOUT_SECONDS,
    await_accepted: bool = False,
    stoppable: bool = False,
) -> dict:
    """Exchange one request, bounding its reply wait without changing ownership.

    Ordinary controls retain the default deadline. A caller hosting a finite
    observation may include that observation's duration in its response wait.
    This timeout neither cancels an accepted operation nor grants execution time.
    ``stoppable`` lets the current call stop waiting; the owner still finishes.
    """
    if (type(response_timeout) not in (int, float)
            or not math.isfinite(response_timeout) or response_timeout <= 0):
        raise AgentsError('invalid-input', 'response_timeout must be finite and positive.')
    try:
        response = _channel().exchange(address, document, authenticate=authenticate,
                                       response_timeout=response_timeout, await_accepted=await_accepted,
                                       stoppable=stoppable)
        if 'error' in response:
            raise AgentsError(response['error']['code'], response['error']['message'])
        return response
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise AgentsError(
            'operation-failed', 'The mapped Session owner did not acknowledge the operation: ' + str(error),
        ) from error


def record_lock_owner(descriptor: int) -> int:
    """Read the POSIX write-lock owner from the kernel, failing closed elsewhere.

    Darwin and Linux use different native ``struct flock`` field orderings.
    The client holds a whole-file lock until it consumes the host response.
    Windows has no such record; there it refuses like any other platform.
    """
    from graftling._channel_posix import record_lock_owner as read_owner
    return read_owner(descriptor)


@contextmanager
def agents_connection(
    directory: Path,
    call: Callable[[dict], AgentsResult],
    authenticate: Callable[[int], None],
    *,
    native_context: Callable[[], dict | None] | None = None,
    close_seconds: float | None = None,
    files: Path | None = None,
    extra_tools: Sequence[ExtraTool] | None = None,
) -> Iterator[str]:
    """Serve one bound caller, optionally checking host-owned native Session facts.

    Native facts only constrain the existing binding. They never select an
    Agent or substitute for kernel-PID authentication. The host obtains its
    expected context from the actual native Session, not child environment.
    ``close_seconds`` is :func:`worker_connection`'s. ``files`` is the
    directory the caller may read an ``arguments_file`` from; by default the
    verified native ``cwd``, and without either an arguments file is refused.
    ``extra_tools`` are the
    host's own tools served beside ``subagents`` on this same authenticated
    channel (see :mod:`graftling.extra_tools`); each callback keeps its own
    authorization.
    """
    from graftling.discovery import handle_request
    from graftling.extra_tools import call_extra_tool, extra_tool_table, requested_tool

    table = extra_tool_table(extra_tools)

    def operate(request: dict) -> dict:
        """Reuse shared discovery and the host's existing business callback."""
        readable = files
        if native_context is not None:
            if not isinstance(request, dict) or set(request) != {'request', 'native'}:
                raise AgentsError('authority-denied', 'The native Session context is required.')
            expected = native_context()
            verify_native_context(request['native'], expected)
            request = request['request']
            readable = files if files is not None else Path(expected['cwd'])
        elif isinstance(request, dict) and 'native' in request:
            raise AgentsError('authority-denied', 'This channel has no matching native Session binding.')
        tool   = requested_tool(request)
        result = (handle_request(request, call, files=readable) if tool is None
                  else call_extra_tool(table, *tool))
        return {'document': result.document, 'failed': result.failed}

    directory.mkdir(parents=True, exist_ok=True)
    with worker_connection(directory, operate, authenticate=authenticate, close_seconds=close_seconds) as address:
        yield address


def client_native_context(metadata: dict | None = None) -> dict | None:
    """Capture native client assertions; the owning host must verify them."""
    runtime = os.environ.get('GRAFTLING_NATIVE_RUNTIME')
    if (runtime == 'codex' or runtime not in {'pi', 'dsh'}
            and 'DSH_SESSION_ID' not in os.environ and 'DSH_AGENT_SOURCE' not in os.environ
            and isinstance(metadata, dict) and 'threadId' in metadata):
        session = metadata.get('threadId') if isinstance(metadata, dict) else None
        return {'runtime': 'codex', 'session': session or os.environ.get('CODEX_THREAD_ID'),
                'cwd': str(Path.cwd())}
    if 'DSH_SESSION_ID' not in os.environ and 'DSH_AGENT_SOURCE' not in os.environ:
        return None
    return {'runtime': 'dsh', 'session': os.environ.get('DSH_SESSION_ID'),
            'source': os.environ.get('DSH_AGENT_SOURCE'), 'cwd': str(Path.cwd())}


def verify_native_context(actual: object, expected: object) -> None:
    """Require exact native facts from the already-authenticated host's Session."""
    if isinstance(expected, dict) and expected.get('runtime') == 'codex':
        if (set(expected) != {'runtime', 'session', 'cwd'} or actual != expected
                or not isinstance(expected['session'], str) or not expected['session']
                or not isinstance(expected['cwd'], str) or not Path(expected['cwd']).is_absolute()):
            raise AgentsError('authority-denied', 'Native Codex Session/cwd do not match this bound caller.')
        return
    if (not isinstance(expected, dict) or set(expected) != {'runtime', 'session', 'source', 'cwd'}
            or expected['runtime'] != 'dsh' or expected['source'] not in {'main', 'child'}
            or not isinstance(expected['session'], str) or not expected['session']
            or not isinstance(expected['cwd'], str) or not Path(expected['cwd']).is_absolute()
            or actual != expected):
        raise AgentsError('authority-denied', 'Native Session/source/cwd do not match this bound caller.')
