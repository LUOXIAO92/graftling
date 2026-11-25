"""The owner connection's named pipe on real Windows (636, W4).

Positive and negative cases through the public functions of
:mod:`graftling.connection`, with real processes where identity matters:

- another local account cannot connect (a temporary account, started with
  CreateProcessWithLogonW, is refused by the pipe's DACL);
- a same-user process that is not below the owning process is refused, and a
  caller's PID stays pinned while it is authenticated;
- the host's death is seen at once by a caller waiting on an accepted request;
- a withdraw before acceptance means the operation never runs; after
  acceptance the caller keeps POSIX's behaviour; without a verdict delivery
  is unconfirmed;
- a second host on one address fails (first pipe instance only).

The protocol itself also runs everywhere over a stand-in pipe
(``test_channel_windows_emulated_636.py``).
"""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from graftling import _channel_windows, _win32
from graftling.connection import connection_operation, host_alive, worker_connection
from graftling.process import authenticate_process, process_alive
from graftling.tool import AgentsError

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows-only: the named-pipe owner connection (636)')

# Prints the outcome of one authenticated request as JSON.
CALLER = '''
import json, sys
from graftling.connection import connection_operation
from graftling.tool import AgentsError
try:
    print(json.dumps({"result": connection_operation(sys.argv[1], {"from": sys.argv[2]}, authenticate=True)}))
except AgentsError as error:
    print(json.dumps({"code": error.code, "message": error.message}))
'''

# Starts CALLER as its own child when told to, prints that child's outcome, then waits to be ended.
OWNER = '''
import subprocess, sys
sys.stdin.readline()
done = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", sys.argv[2], sys.argv[1], "descendant"],
                      capture_output=True, encoding="utf-8")
print(done.stdout.strip() or done.stderr.strip().replace("\\n", " | "), flush=True)
sys.stdin.readline()
'''

# Starts CALLER detached from any handle of this test, then ends at once.
LAUNCHER = '''
import subprocess, sys
subprocess.Popen([sys.executable, "-I", "-X", "utf8", "-c", sys.argv[2], sys.argv[1], "orphan"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''

# A host in its own process whose operation never ends.
HOST = '''
import sys, time
from pathlib import Path
from graftling.connection import worker_connection
def operate(request):
    print("running", flush=True)
    time.sleep(600)
    return {}
with worker_connection(Path(sys.argv[1]), operate) as address:
    print(address, flush=True)
    time.sleep(600)
'''

# Opens the pipe as a client with no graftling import (another account may not read the checkout);
# the exit code is the Windows error, 0 when it connected.
PROBE = '''
import sys
try:
    open(sys.argv[1], "r+b", buffering=0).close()
except OSError as error:
    sys.exit(getattr(error, "winerror", None) or 99)
'''


def child(script: str, *arguments: str) -> list[str]:
    """A Python child running ``script``: isolated, and UTF-8 whatever the code page or TEMP path."""
    return [sys.executable, '-I', '-X', 'utf8', '-c', script, *arguments]


def run_caller(address: str, label: str) -> dict:
    done = subprocess.run(child(CALLER, address, label), capture_output=True, encoding='utf-8', timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def call_in_thread(*arguments, **options) -> dict:
    """Run connection_operation on a thread; the dict gets 'result' or 'error', and 'ended'."""
    outcome: dict = {}

    def run() -> None:
        try:
            outcome['result'] = connection_operation(*arguments, **options)
        except AgentsError as error:
            outcome['error'] = error
        outcome['ended'] = time.monotonic()

    outcome['thread'] = thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return outcome


def test_requests_from_this_and_another_process_are_answered(tmp_path: Path) -> None:
    callers = []

    def operate(request: dict) -> dict:
        time.sleep(0.2)
        return {'echo': request}

    with worker_connection(tmp_path, operate, authenticate=callers.append) as address:
        assert host_alive(address)
        assert _channel_windows.pipe_name(address).startswith('\\\\.\\pipe\\graftling-')
        assert connection_operation(address, {'n': 0}, authenticate=True) == {'echo': {'n': 0}}
        assert run_caller(address, 'child') == {'result': {'echo': {'from': 'child'}}}
        calls = [call_in_thread(address, {'n': n}, authenticate=True) for n in range(1, 6)]
        for call in calls:
            call['thread'].join(30)
        assert sorted(call['result']['echo']['n'] for call in calls) == [1, 2, 3, 4, 5]
    assert callers[0] == os.getpid() and callers[1] != os.getpid() and len(callers) == 7
    assert not host_alive(address)
    with pytest.raises(AgentsError) as error:
        connection_operation(address, {})
    assert error.value.code == 'request-not-accepted'


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_uint32), ('lpReserved', ctypes.c_wchar_p), ('lpDesktop', ctypes.c_wchar_p),
                ('lpTitle', ctypes.c_wchar_p), ('dwX', ctypes.c_uint32), ('dwY', ctypes.c_uint32),
                ('dwXSize', ctypes.c_uint32), ('dwYSize', ctypes.c_uint32), ('dwXCountChars', ctypes.c_uint32),
                ('dwYCountChars', ctypes.c_uint32), ('dwFillAttribute', ctypes.c_uint32),
                ('dwFlags', ctypes.c_uint32), ('wShowWindow', ctypes.c_uint16), ('cbReserved2', ctypes.c_uint16),
                ('lpReserved2', ctypes.c_void_p), ('hStdInput', ctypes.c_void_p), ('hStdOutput', ctypes.c_void_p),
                ('hStdError', ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', ctypes.c_void_p), ('hThread', ctypes.c_void_p),
                ('dwProcessId', ctypes.c_uint32), ('dwThreadId', ctypes.c_uint32)]


def run_as(user: str, password: str, command: list[str]) -> int:
    """Run ``command`` as the local account ``user`` (CreateProcessWithLogonW); return its exit code.

    Test-only Win32 declarations: graftling itself never starts a process as another account.
    """
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi32.CreateProcessWithLogonW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
        ctypes.c_uint32, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION)]
    advapi32.CreateProcessWithLogonW.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    startup = STARTUPINFOW(cb=ctypes.sizeof(STARTUPINFOW))
    information = PROCESS_INFORMATION()
    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
    logon_with_profile, create_no_window = 0x1, 0x08000000
    if not advapi32.CreateProcessWithLogonW(user, '.', password, logon_with_profile, command[0], line,
                                            create_no_window, None, str(Path(command[0]).parent),
                                            ctypes.byref(startup), ctypes.byref(information)):
        error = ctypes.get_last_error()
        pytest.fail(f'CreateProcessWithLogonW as {user} failed on this runner: {ctypes.FormatError(error)} '
                    f'({error}); the other-account case did not run')
    try:
        assert kernel32.WaitForSingleObject(information.hProcess, 120_000) == 0, 'the other-account probe hung'
        code = ctypes.c_uint32()
        assert kernel32.GetExitCodeProcess(information.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(information.hThread)
        kernel32.CloseHandle(information.hProcess)


def test_another_local_account_cannot_connect(tmp_path: Path) -> None:
    user = 'g636' + secrets.token_hex(4)
    # 14 characters: net asks for confirmation above that; complexity: upper, lower, digit, symbol.
    password = 'Aa1!' + secrets.token_hex(5)
    added = subprocess.run(['net', 'user', user, password, '/add'], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
    if added.returncode != 0:
        pytest.skip(f'Requires the right to create a temporary local account: {added.stdout} {added.stderr}'.strip())
    try:
        with worker_connection(tmp_path, lambda request: {}) as address:
            name = _channel_windows.pipe_name(address)
            # The probe works: the same account connects.
            mine = subprocess.run(child(PROBE, name), timeout=60)
            assert mine.returncode == 0
            # Another account is refused by the pipe's DACL.
            assert run_as(user, password, child(PROBE, name)) == _win32.ERROR_ACCESS_DENIED
    finally:
        subprocess.run(['net', 'user', user, '/delete'], capture_output=True, stdin=subprocess.DEVNULL)


def test_a_same_user_process_outside_the_owner_is_refused_and_its_descendant_accepted(tmp_path: Path) -> None:
    ran, owners = [], []
    with worker_connection(tmp_path, lambda request: ran.append(request) or {'ran': request},
                           authenticate=lambda pid: authenticate_process(owners[0], pid)) as address:
        owners.append(owner := subprocess.Popen(child(OWNER, address, CALLER), stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, encoding='utf-8'))
        try:
            # A sibling of the owner, started by this same user, poses as its caller.
            sibling = run_caller(address, 'sibling')
            assert sibling['code'] == 'authority-denied', sibling
            # The owner's own child is accepted.
            owner.stdin.write('go\n')
            owner.stdin.flush()
            assert json.loads(owner.stdout.readline()) == {'result': {'ran': {'from': 'descendant'}}}
        finally:
            owner.stdin.close()
            owner.wait(30)
            owner.stdout.close()
    assert ran == [{'from': 'descendant'}]


def test_the_callers_pid_stays_pinned_while_it_is_authenticated(tmp_path: Path) -> None:
    entered, go, facts = threading.Event(), threading.Event(), {}

    def authenticate(pid: int) -> None:
        facts['pid'], facts['created'] = pid, _win32.creation_ticks(pid)
        entered.set()
        go.wait(60)
        facts['alive_after'], facts['created_after'] = process_alive(pid), _win32.creation_ticks(pid)
        raise AgentsError('authority-denied', 'checked')

    with worker_connection(tmp_path, lambda request: {}, authenticate=authenticate) as address:
        launcher = subprocess.run(child(LAUNCHER, address, CALLER), timeout=60)
        assert launcher.returncode == 0
        assert entered.wait(60)
        # No handle of this test or of the ended launcher keeps the caller; end it.
        os.kill(facts['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 30
        while process_alive(facts['pid']) and time.monotonic() < deadline:
            time.sleep(0.05)
        go.set()
    assert facts['created'] is not None
    assert facts['alive_after'] is False
    # The PID still names the same, ended process: it cannot have been reused meanwhile.
    assert facts['created_after'] == facts['created']


def test_the_hosts_death_is_seen_at_once_by_a_caller_waiting_on_an_accepted_request(tmp_path: Path) -> None:
    host = subprocess.Popen(child(HOST, str(tmp_path)), stdout=subprocess.PIPE, encoding='utf-8')
    try:
        address = host.stdout.readline().strip()
        assert address and host_alive(address)
        call = call_in_thread(address, {}, response_timeout=120, await_accepted=True)
        assert host.stdout.readline().strip() == 'running'
        time.sleep(0.5)  # the caller has read ``accepted`` and waits for the reply
        killed = time.monotonic()
        host.kill()
        call['thread'].join(30)
        assert call['ended'] - killed < 5
        assert call['error'].code == 'operation-failed'
        assert 'the accepted operation is unconfirmed' in call['error'].message
        deadline = time.monotonic() + 10
        while host_alive(address) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not host_alive(address)
    finally:
        host.kill()
        host.wait(30)
        host.stdout.close()


def test_a_withdraw_before_acceptance_means_the_operation_never_runs(tmp_path: Path) -> None:
    ran, release = [], threading.Event()
    with worker_connection(tmp_path, ran.append, authenticate=lambda pid: release.wait(30)) as address:
        call = call_in_thread(address, {}, authenticate=True, response_timeout=0.5)
        time.sleep(1.2)  # the caller has withdrawn; the host is still authenticating
        release.set()
        call['thread'].join(30)
    assert call['error'].code == 'request-not-accepted'
    assert ran == []


def test_after_acceptance_the_caller_keeps_its_behaviour(tmp_path: Path) -> None:
    finished = []

    def slow(request: dict) -> dict:
        time.sleep(1.5)
        finished.append(request)
        return {'done': request}

    with worker_connection(tmp_path, slow) as address:
        assert connection_operation(address, {'n': 1}, response_timeout=0.3, await_accepted=True) == {'done': {'n': 1}}
        with pytest.raises(AgentsError) as error:
            connection_operation(address, {'n': 2}, response_timeout=0.3)
        assert error.value.code == 'operation-failed'
        assert 'Session control timed out; delivery is unconfirmed.' in error.value.message
    assert finished == [{'n': 1}, {'n': 2}]  # the close waited for the accepted operation


def test_without_a_verdict_delivery_is_unconfirmed_and_nothing_runs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_channel_windows, 'WITHDRAW_SECONDS', 0.3)
    ran, release = [], threading.Event()
    with worker_connection(tmp_path, ran.append, authenticate=lambda pid: release.wait(30)) as address:
        call = call_in_thread(address, {}, authenticate=True, response_timeout=0.3)
        call['thread'].join(30)
        release.set()
    assert call['error'].code == 'operation-failed'
    assert 'delivery is unconfirmed' in call['error'].message
    assert ran == []


def test_a_second_host_on_one_address_fails(tmp_path: Path) -> None:
    address = str(tmp_path)
    with _channel_windows.channel(address, lambda request: {}):
        with pytest.raises(AgentsError) as error:
            with _channel_windows.channel(address, lambda request: {}):
                pass
        assert error.value.code == 'host-busy'
        assert connection_operation(address, {'still': 'served'}) == {}
    # A squatter that created the pipe first keeps the host from serving behind it.
    squatter = _win32.create_pipe(_channel_windows.pipe_name(address), first=True)
    try:
        with pytest.raises(AgentsError) as error:
            with _channel_windows.channel(address, lambda request: {}):
                pass
        assert error.value.code == 'host-busy'
    finally:
        _win32.close(squatter)
