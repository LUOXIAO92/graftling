"""The Windows owner connection's protocol over an in-process stand-in pipe (636).

The stand-in replaces only ``graftling._channel_windows._win32``: pipe names,
instances, message order, a closed end and the pinned client PID. The real
Win32 calls are checked on Windows by ``test_channel_windows_636.py``. These
run everywhere, through the public functions of :mod:`graftling.connection`.
A closed end is strict here: the other side's next pipe call fails even when
messages are still queued, so nothing relies on reading after the peer left.
"""

from __future__ import annotations

import collections
import shutil
import threading
import time
from pathlib import Path

import pytest

from graftling import _channel_windows, _win32, connection
from graftling.connection import connection_operation, host_alive, worker_connection
from graftling.tool import AgentsError


class PipeError(OSError):
    """An OSError carrying a Windows error code on any platform."""

    def __init__(self, code: int) -> None:
        super().__init__(f'[WinError {code}]')
        self.winerror = code


class End:
    """One end of one pipe instance."""

    def __init__(self, instance: 'Instance', server: bool) -> None:
        self.instance, self.server, self.closed = instance, server, False

    def peer(self) -> 'End | None':
        return self.instance.client if self.server else self.instance.server

    def inbox(self) -> collections.deque:
        return self.instance.to_server if self.server else self.instance.to_client


class Instance:
    def __init__(self, name: str) -> None:
        self.name = name
        self.server = End(self, True)
        self.client: End | None = None
        self.to_server: collections.deque = collections.deque()
        self.to_client: collections.deque = collections.deque()


class FakePipes:
    """The pipe functions ``_channel_windows`` calls, over Python queues."""

    ERROR_ACCESS_DENIED = _win32.ERROR_ACCESS_DENIED
    ERROR_FILE_NOT_FOUND = _win32.ERROR_FILE_NOT_FOUND
    ERROR_PIPE_BUSY = _win32.ERROR_PIPE_BUSY
    PIPE_GONE = _win32.PIPE_GONE

    def __init__(self) -> None:
        self.lock = threading.Condition()
        self.handles: dict[int, object] = {}
        self.numbers = iter(range(1000, 10**9))
        self.client_pid = 4242
        self.pinned: list[int] = []
        self.client_alive = True

    def _new(self, thing: object) -> int:
        handle = next(self.numbers)
        self.handles[handle] = thing
        return handle

    def _instances(self, name: str) -> list[Instance]:
        return [end.instance for end in self.handles.values()
                if isinstance(end, End) and end.server and not end.closed and end.instance.name == name]

    def create_pipe(self, name: str, first: bool) -> int:
        with self.lock:
            if first and self._instances(name):
                raise PipeError(_win32.ERROR_ACCESS_DENIED)
            return self._new(Instance(name).server)

    def connect_pipe(self, handle: int, stopped) -> bool:
        end = self.handles[handle]
        with self.lock:
            while end.instance.client is None:
                if stopped():
                    return False
                self.lock.wait(0.01)
            return True

    def disconnect_pipe(self, handle: int) -> None:
        with self.lock:
            instance = self.handles[handle].instance
            if instance.client is not None:
                instance.client.closed = True
            instance.client = None

    def now_ticks(self) -> int:
        return time.time_ns() // 100

    def pin_pipe_client(self, handle: int, connected: int) -> tuple[int, int]:
        if not self.client_alive:
            raise PipeError(_win32.ERROR_INVALID_PARAMETER)
        process = self._new('process')
        self.pinned.append(process)
        return self.client_pid, process

    def close(self, handle: int) -> None:
        with self.lock:
            thing = self.handles.pop(handle)
            if isinstance(thing, End):
                thing.closed = True
            self.lock.notify_all()

    def open_pipe(self, name: str) -> int:
        with self.lock:
            instances = self._instances(name)
            if not instances:
                raise PipeError(_win32.ERROR_FILE_NOT_FOUND)
            free = [instance for instance in instances if instance.client is None]
            if not free:
                raise PipeError(_win32.ERROR_PIPE_BUSY)
            free[0].client = End(free[0], False)
            self.lock.notify_all()
            return self._new(free[0].client)

    def wait_pipe(self, name: str, milliseconds: int) -> int:
        with self.lock:
            if not self._instances(name):
                return _win32.ERROR_FILE_NOT_FOUND
        time.sleep(milliseconds / 1000)
        return 0

    def _live(self, handle: int) -> End:
        end = self.handles[handle]
        peer = end.peer()
        if peer is None or peer.closed:
            raise PipeError(_win32.ERROR_BROKEN_PIPE)
        return end

    def peek_pipe(self, handle: int) -> int:
        with self.lock:
            inbox = self._live(handle).inbox()
            return len(inbox[0]) if inbox else 0

    def read_message(self, handle: int, overlapped_io: bool) -> bytes:
        with self.lock:
            return self._live(handle).inbox().popleft()

    def write_message(self, handle: int, data: bytes, overlapped_io: bool) -> None:
        with self.lock:
            end = self.handles[handle]
            peer = end.peer()
            if peer is None or peer.closed:
                raise PipeError(_win32.ERROR_NO_DATA)
            peer.inbox().append(bytes(data))

    def kill_host(self, address: str) -> None:
        """What the host's death does to its pipe: every server end closes."""
        with self.lock:
            for handle, thing in list(self.handles.items()):
                if isinstance(thing, End) and thing.server and thing.instance.name == _channel_windows.pipe_name(address):
                    thing.closed = True
            self.lock.notify_all()


@pytest.fixture
def pipes(monkeypatch: pytest.MonkeyPatch) -> FakePipes:
    fake = FakePipes()
    monkeypatch.setattr(_channel_windows, '_win32', fake)
    monkeypatch.setattr(connection, '_channel', lambda: _channel_windows)
    return fake


def call_in_thread(*arguments, **options) -> dict:
    """Run connection_operation on a thread; the dict gets 'result' or 'error' and 'seconds'."""
    outcome: dict = {}

    def run() -> None:
        started = time.monotonic()
        try:
            outcome['result'] = connection_operation(*arguments, **options)
        except AgentsError as error:
            outcome['error'] = error
        outcome['seconds'] = time.monotonic() - started

    outcome['thread'] = thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return outcome


def test_a_request_is_answered_with_its_reply_and_the_pinned_pid(pipes: FakePipes, tmp_path: Path) -> None:
    seen = []
    with worker_connection(tmp_path, lambda request: {'echo': request},
                           authenticate=seen.append) as address:
        assert host_alive(address)
        assert connection_operation(address, {'x': 1}, authenticate=True) == {'echo': {'x': 1}}
    assert seen == [pipes.client_pid]
    assert not host_alive(address)
    assert [handle for handle in pipes.pinned if handle in pipes.handles] == []  # every pin released


def test_an_error_reply_and_a_refused_caller_keep_their_codes(pipes: FakePipes, tmp_path: Path) -> None:
    ran = []

    def operate(request: dict) -> dict:
        ran.append(request)
        raise AgentsError('not-found', 'nothing here')

    def refuse(pid: int) -> None:
        if pid != 7:
            raise AgentsError('authority-denied', 'not this caller')

    with worker_connection(tmp_path, operate) as address:
        with pytest.raises(AgentsError) as error:
            connection_operation(address, {})
        assert error.value.code == 'not-found'
    with worker_connection(tmp_path, operate, authenticate=refuse) as address:
        with pytest.raises(AgentsError) as error:
            connection_operation(address, {'second': True}, authenticate=True)
        assert error.value.code == 'authority-denied'
    assert ran == [{}]


def test_a_client_that_cannot_be_pinned_is_refused(pipes: FakePipes, tmp_path: Path) -> None:
    pipes.client_alive = False
    with worker_connection(tmp_path, lambda request: {}, authenticate=lambda pid: None) as address:
        with pytest.raises(AgentsError) as error:
            connection_operation(address, {}, authenticate=True)
    assert error.value.code == 'authority-denied'


def test_a_withdraw_before_acceptance_means_the_operation_never_runs(pipes: FakePipes, tmp_path: Path) -> None:
    ran, release = [], threading.Event()
    with worker_connection(tmp_path, ran.append, authenticate=lambda pid: release.wait(10)) as address:
        call = call_in_thread(address, {'late': True}, authenticate=True, response_timeout=0.3)
        time.sleep(0.6)  # the caller has withdrawn by now; the host is still authenticating
        release.set()
        call['thread'].join(10)
    assert call['error'].code == 'request-not-accepted'
    assert ran == []


def test_without_a_verdict_in_time_delivery_is_unconfirmed_and_nothing_runs(
        pipes: FakePipes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_channel_windows, 'WITHDRAW_SECONDS', 0.2)
    ran, release = [], threading.Event()
    with worker_connection(tmp_path, ran.append, authenticate=lambda pid: release.wait(10)) as address:
        call = call_in_thread(address, {}, authenticate=True, response_timeout=0.2)
        call['thread'].join(10)
        release.set()
    assert call['error'].code == 'operation-failed'
    assert 'delivery is unconfirmed' in call['error'].message
    assert ran == []


def test_an_accepted_request_is_awaited_past_the_deadline(pipes: FakePipes, tmp_path: Path) -> None:
    def slow(request: dict) -> dict:
        time.sleep(0.6)
        return {'done': True}

    with worker_connection(tmp_path, slow) as address:
        assert connection_operation(address, {}, response_timeout=0.2, await_accepted=True) == {'done': True}
        with pytest.raises(AgentsError) as error:
            connection_operation(address, {}, response_timeout=0.2)
    assert error.value.code == 'operation-failed'
    assert 'Session control timed out; delivery is unconfirmed.' in error.value.message


@pytest.mark.parametrize('await_accepted', [True, False])
def test_a_withdraw_that_crosses_the_acceptance_keeps_the_accepted_behaviour(
        pipes: FakePipes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, await_accepted: bool) -> None:
    """The host accepted just before the caller's withdraw arrived: the caller reads ``accepted``."""
    ran = threading.Event()

    def operate(request: dict) -> dict:
        ran.set()
        time.sleep(0.3)
        return {'ran': True}

    def admit_before_the_withdraw(handle: int) -> bool:
        while not pipes.peek_pipe(handle):  # the withdraw is on its way; the host's check came first
            time.sleep(0.01)
        pipes.write_message(handle, b'{"accepted": true}', True)
        return True

    monkeypatch.setattr(_channel_windows, '_admit', admit_before_the_withdraw)
    with worker_connection(tmp_path, operate) as address:
        if await_accepted:
            assert connection_operation(address, {}, response_timeout=0.1, await_accepted=True) == {'ran': True}
        else:
            with pytest.raises(AgentsError) as error:
                connection_operation(address, {}, response_timeout=0.1)
            assert 'Session control timed out; delivery is unconfirmed.' in error.value.message
    assert ran.is_set()


def test_the_hosts_death_ends_an_accepted_wait_at_once(pipes: FakePipes, tmp_path: Path) -> None:
    running, release = threading.Event(), threading.Event()

    def operate(request: dict) -> dict:
        running.set()
        release.wait(30)
        return {}

    with worker_connection(tmp_path, operate, close_seconds=0.5) as address:
        call = call_in_thread(address, {}, response_timeout=60, await_accepted=True)
        assert running.wait(10)
        time.sleep(0.3)  # the caller has read ``accepted`` and waits for the reply
        killed = time.monotonic()
        pipes.kill_host(address)
        call['thread'].join(10)
        assert time.monotonic() - killed < 2
        assert not host_alive(address)
    release.set()
    assert call['error'].code == 'operation-failed'
    assert 'the accepted operation is unconfirmed' in call['error'].message


def test_a_request_during_close_is_refused_and_a_running_one_finishes(pipes: FakePipes, tmp_path: Path) -> None:
    running, release, results = threading.Event(), threading.Event(), []

    def operate(request: dict) -> dict:
        running.set()
        release.wait(10)
        return {'finished': request}

    with worker_connection(tmp_path, operate) as address:
        first = call_in_thread(address, {'n': 1}, await_accepted=True)
        assert running.wait(10)
        closer = threading.Thread(target=lambda: time.sleep(0.3) or results.append(
            connection_operation_safe(address)), daemon=True)
        closer.start()
        threading.Timer(0.8, release.set).start()
    closer.join(10)
    first['thread'].join(10)
    assert first['result'] == {'finished': {'n': 1}}
    assert results == ['request-not-accepted']


def connection_operation_safe(address: str) -> str:
    try:
        connection_operation(address, {'n': 2})
    except AgentsError as error:
        return error.code
    return 'answered'


def test_after_close_the_channel_is_gone(pipes: FakePipes, tmp_path: Path) -> None:
    with worker_connection(tmp_path, lambda request: {}) as address:
        pass
    with pytest.raises(AgentsError) as error:
        connection_operation(address, {})
    assert error.value.code == 'request-not-accepted'


def test_a_second_host_on_one_address_is_refused(pipes: FakePipes, tmp_path: Path) -> None:
    with _channel_windows.channel(str(tmp_path), lambda request: {}):
        with pytest.raises(AgentsError) as error:
            with _channel_windows.channel(str(tmp_path), lambda request: {}):
                pass
    assert error.value.code == 'host-busy'


def test_concurrent_callers_are_answered_independently(pipes: FakePipes, tmp_path: Path) -> None:
    def operate(request: dict) -> dict:
        time.sleep(0.2)
        return request

    with worker_connection(tmp_path, operate) as address:
        calls = [call_in_thread(address, {'n': n}) for n in range(5)]
        for call in calls:
            call['thread'].join(10)
    assert sorted(call['result']['n'] for call in calls) == [0, 1, 2, 3, 4]


def test_a_removed_control_directory_stops_the_pipe_service(pipes: FakePipes, tmp_path: Path, capfd) -> None:
    with worker_connection(tmp_path, lambda request: {}) as address:
        shutil.rmtree(address)
        deadline = time.monotonic() + 5
        while pipes._instances(_channel_windows.pipe_name(address)) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not pipes._instances(_channel_windows.pipe_name(address))
        assert not host_alive(address)
    err = capfd.readouterr().err
    assert err.count('no longer exists; its connection service stopped.') == 1


class Tree:
    """A stand-in OwnedTree whose job holds ``members``, or cannot be read."""

    def __init__(self, members: set[int] | None) -> None:
        self.members = members

    def contains(self, pid: int) -> bool:
        if self.members is None:
            raise OSError('the job cannot be read')
        return pid in self.members


def test_on_windows_an_owner_started_caller_is_checked_by_job_membership_only(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from graftling import process
    from graftling.process import authenticate_process, caller_in_tree

    monkeypatch.setattr(process.sys, 'platform', 'win32')
    monkeypatch.setattr(process, 'process_ancestors', lambda pid: pytest.fail('Windows walks no PIDs'))
    assert caller_in_tree(10, Tree({20}), 20)
    assert not caller_in_tree(10, Tree({20}), 30)
    assert not caller_in_tree(10, Tree(None), 20)  # unreadable job: refused
    assert not caller_in_tree(10, None, 20)  # no tree: refused

    class Live:
        pid = 10

        @staticmethod
        def poll() -> None:
            return None

    authenticate_process(Live(), 20, Tree({20}))
    for tree in (Tree({30}), Tree(None), None):
        with pytest.raises(AgentsError) as error:
            authenticate_process(Live(), 20, tree)
        assert error.value.code == 'authority-denied'
