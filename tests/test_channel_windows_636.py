"""The owner connection's named pipe on real Windows (636, W4).

Positive and negative cases through the public functions of
:mod:`graftling.connection`, with real processes where identity matters:

- another local account cannot connect (a temporary account, started with
  CreateProcessWithLogonW, is refused by the pipe's DACL);
- an owner-started Runtime's caller must be in its job: a same-user process
  outside the job is refused, and one that forges the owner as its parent is
  refused or, if Windows puts it in the job, accepted as README.md states;
  a caller's PID stays pinned while it is authenticated;
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
from graftling.process import authenticate_process, caller_in_tree, process_alive
from graftling.process_tree import OwnedTree
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

# Opens the pipe as a client with no graftling import (another account may not read the checkout).
# CreateFileW itself, not open(): the CRT's OSError carries errno only, and the Win32 error is the
# observable. The exit code is GetLastError(), 0 when it connected.
PROBE = '''
import ctypes, sys
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                 ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
handle = kernel32.CreateFileW(sys.argv[1], 0x80000000 | 0x40000000, 0, None, 3, 0, None)
if handle in (None, ctypes.c_void_p(-1).value):
    sys.exit(ctypes.get_last_error() or 99)
kernel32.CloseHandle(handle)
'''

# Another account's squatter: creates the named pipe with a DACL open to Everyone (two instances,
# the first with FILE_FLAG_FIRST_PIPE_INSTANCE), serves every connection and logs to sys.argv[2]
# whether a client connected and whether it sent anything. No graftling import.
SQUATTER = '''
import ctypes, sys, threading, time
k = ctypes.WinDLL("kernel32", use_last_error=True)
a = ctypes.WinDLL("advapi32", use_last_error=True)
V, D = ctypes.c_void_p, ctypes.c_uint32
class SA(ctypes.Structure):
    _fields_ = [("n", D), ("sd", V), ("inherit", ctypes.c_int)]
a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [ctypes.c_wchar_p, D, ctypes.POINTER(V), V]
k.CreateNamedPipeW.argtypes = [ctypes.c_wchar_p, D, D, D, D, D, D, ctypes.POINTER(SA)]
k.CreateNamedPipeW.restype = V
k.ConnectNamedPipe.argtypes = [V, V]
k.DisconnectNamedPipe.argtypes = [V]
k.PeekNamedPipe.argtypes = [V, V, D, V, ctypes.POINTER(D), V]
name, log = sys.argv[1], sys.argv[2]
lock = threading.Lock()
def note(line):
    with lock, open(log, "a", encoding="utf-8") as stream:
        stream.write(line + "\\n")
sd = V()
assert a.ConvertStringSecurityDescriptorToSecurityDescriptorW("D:(A;;GA;;;WD)", 1, ctypes.byref(sd), None)
sa = SA(ctypes.sizeof(SA), sd, 0)
pipes = []
for first in (True, False):
    pipe = k.CreateNamedPipeW(name, 0x3 | (0x00080000 if first else 0), 0x4 | 0x2, 255, 65536, 65536, 0, ctypes.byref(sa))
    if pipe in (None, V(-1).value):
        note("create failed %d" % ctypes.get_last_error())
        sys.exit(1)
    pipes.append(pipe)
def serve(pipe):
    while True:
        k.ConnectNamedPipe(pipe, None)
        note("connected")
        for _ in range(60):
            waiting = D(0)
            if not k.PeekNamedPipe(pipe, None, 0, None, ctypes.byref(waiting), None):
                break
            if waiting.value:
                note("read %d" % waiting.value)
                break
            time.sleep(0.05)
        k.DisconnectNamedPipe(pipe)
for pipe in pipes:
    threading.Thread(target=serve, args=(pipe,), daemon=True).start()
note("ready")
time.sleep(600)
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


def _kernel32() -> ctypes.WinDLL:
    """kernel32 with the test-only process calls declared."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, argtypes, restype in (
            ('WaitForSingleObject', [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32),
            ('GetExitCodeProcess', [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int),
            ('TerminateProcess', [ctypes.c_void_p, ctypes.c_uint], ctypes.c_int),
            ('CloseHandle', [ctypes.c_void_p], ctypes.c_int)):
        function = getattr(kernel32, name)
        function.argtypes, function.restype = argtypes, restype
    return kernel32


def start_as(user: str, password: str, command: list[str]) -> PROCESS_INFORMATION:
    """Start ``command`` as the local account ``user`` (CreateProcessWithLogonW).

    Test-only Win32 declarations: graftling itself never starts a process as another account.
    The caller closes both handles of the returned PROCESS_INFORMATION.
    """
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi32.CreateProcessWithLogonW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
        ctypes.c_uint32, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION)]
    advapi32.CreateProcessWithLogonW.restype = ctypes.c_int
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
    return information


def run_as(user: str, password: str, command: list[str]) -> int:
    """Run ``command`` as the local account ``user`` to its end; return its exit code."""
    kernel32 = _kernel32()
    information = start_as(user, password, command)
    try:
        assert kernel32.WaitForSingleObject(information.hProcess, 120_000) == 0, 'the other-account probe hung'
        code = ctypes.c_uint32()
        assert kernel32.GetExitCodeProcess(information.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(information.hThread)
        kernel32.CloseHandle(information.hProcess)


@pytest.fixture
def other_account():
    """A temporary local account (name, password), deleted afterwards."""
    user = 'g636' + secrets.token_hex(4)
    # 14 characters: net asks for confirmation above that; complexity: upper, lower, digit, symbol.
    password = 'Aa1!' + secrets.token_hex(5)
    added = subprocess.run(['net', 'user', user, password, '/add'], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
    if added.returncode != 0:
        pytest.skip(f'Requires the right to create a temporary local account: {added.stdout} {added.stderr}'.strip())
    try:
        yield user, password
    finally:
        subprocess.run(['net', 'user', user, '/delete'], capture_output=True, stdin=subprocess.DEVNULL)


def test_another_local_account_cannot_connect(tmp_path: Path, other_account: tuple[str, str]) -> None:
    with worker_connection(tmp_path, lambda request: {}) as address:
        name = _channel_windows.pipe_name(address)
        # The probe works: the same account connects.
        mine = subprocess.run(child(PROBE, name), timeout=60)
        assert mine.returncode == 0
        # Another account is refused by the pipe's DACL: the Win32 error itself.
        assert run_as(*other_account, child(PROBE, name)) == _win32.ERROR_ACCESS_DENIED


def test_a_pipe_another_account_created_for_a_dead_hosts_address_gets_nothing(
        tmp_path: Path, other_account: tuple[str, str]) -> None:
    """After a host died, another account takes its pipe name with an open DACL (Spec review F1)."""
    address = tmp_path / 'control-dead'
    address.mkdir()
    shared = tmp_path / 'squatter-log'
    shared.mkdir()
    # The other account may write its log here (Everyone: full control, inherited by the file).
    granted = subprocess.run(['icacls', str(shared), '/grant', '*S-1-1-0:(OI)(CI)F'], capture_output=True,
                             encoding='utf-8', errors='replace')
    assert granted.returncode == 0, granted.stdout + granted.stderr
    log = shared / 'log.txt'
    kernel32 = _kernel32()
    squatter = start_as(*other_account, child(SQUATTER, _channel_windows.pipe_name(str(address)), str(log)))
    try:
        deadline = time.monotonic() + 120
        while not (log.exists() and 'ready' in log.read_text(encoding='utf-8')):
            assert time.monotonic() < deadline, 'the squatter never became ready'
            assert kernel32.WaitForSingleObject(squatter.hProcess, 0) != 0, \
                f'the squatter ended: {log.read_text(encoding="utf-8") if log.exists() else "no log"}'
            time.sleep(0.1)
        assert not host_alive(str(address))
        with pytest.raises(AgentsError) as error:
            connection_operation(str(address), {'secret': 'request'}, authenticate=True, response_timeout=5)
        assert error.value.code == 'authority-denied', error.value.message
        time.sleep(3.5)  # the squatter checks each connection for 3 s
        lines = log.read_text(encoding='utf-8').splitlines()
        assert 'connected' in lines  # the caller reached the squatter's pipe ...
        assert not [line for line in lines if line.startswith('read')]  # ... and sent it nothing
    finally:
        kernel32.TerminateProcess(squatter.hProcess, 1)
        kernel32.WaitForSingleObject(squatter.hProcess, 30_000)
        kernel32.CloseHandle(squatter.hThread)
        kernel32.CloseHandle(squatter.hProcess)


def test_a_caller_that_never_reads_a_large_reply_does_not_hold_the_close(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reply write is bounded (Spec review F2): close with no close_seconds still ends."""
    monkeypatch.setattr(_channel_windows, 'OPERATION_TIMEOUT_SECONDS', 2.0)
    replied = threading.Event()

    def large(request: dict) -> dict:
        replied.set()
        return {'blob': 'x' * (4 * _win32.PIPE_BUFFER_BYTES)}

    stuck = None
    try:
        with worker_connection(tmp_path, large) as address:
            stuck = _win32.open_pipe(_channel_windows.pipe_name(address))
            _win32.write_message(stuck, b'{"request": {}}', time.monotonic() + 10)
            assert replied.wait(10)
            closing = time.monotonic()
        assert time.monotonic() - closing < 10  # the unread reply's write gave up at its 2 s bound
    finally:
        if stuck is not None:
            _win32.close(stuck)


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [('StartupInfo', STARTUPINFOW), ('lpAttributeList', ctypes.c_void_p)]


def run_with_parent(parent_pid: int, command: list[str]) -> int:
    """Start ``command`` naming ``parent_pid`` as its parent (PROC_THREAD_ATTRIBUTE_PARENT_PROCESS).

    Waits for it to end and returns its PID. Test-only Win32 declarations: this
    is how a same-user process forges its recorded parent.
    """
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle, size_t, dword = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32
    for name, argtypes, restype in (
            ('OpenProcess', [dword, ctypes.c_int, dword], handle),
            ('InitializeProcThreadAttributeList', [ctypes.c_void_p, dword, dword, ctypes.POINTER(size_t)], ctypes.c_int),
            ('UpdateProcThreadAttribute', [ctypes.c_void_p, dword, size_t, ctypes.c_void_p, size_t, ctypes.c_void_p,
                                           ctypes.c_void_p], ctypes.c_int),
            ('DeleteProcThreadAttributeList', [ctypes.c_void_p], None),
            ('CreateProcessW', [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                dword, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p,
                                ctypes.POINTER(PROCESS_INFORMATION)], ctypes.c_int),
            ('WaitForSingleObject', [handle, dword], dword),
            ('CloseHandle', [handle], ctypes.c_int)):
        function = getattr(kernel32, name)
        function.argtypes, function.restype = argtypes, restype
    process_create_process, parent_attribute = 0x0080, 0x00020000
    extended_startupinfo, create_no_window = 0x00080000, 0x08000000
    parent = kernel32.OpenProcess(process_create_process, False, parent_pid)
    assert parent, f'OpenProcess({parent_pid}) failed: {ctypes.get_last_error()}'
    size = size_t(0)
    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    information = PROCESS_INFORMATION()
    try:
        assert kernel32.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)), ctypes.get_last_error()
        parent_value = handle(parent)
        assert kernel32.UpdateProcThreadAttribute(attributes, 0, parent_attribute, ctypes.byref(parent_value),
                                                  ctypes.sizeof(parent_value), None, None), ctypes.get_last_error()
        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        assert kernel32.CreateProcessW(command[0], line, None, None, False, extended_startupinfo | create_no_window,
                                       None, None, ctypes.byref(startup), ctypes.byref(information)), \
            f'CreateProcessW with a forged parent failed: {ctypes.get_last_error()}'
        assert kernel32.WaitForSingleObject(information.hProcess, 120_000) == 0, 'the forged-parent caller hung'
        return information.dwProcessId
    finally:
        kernel32.DeleteProcThreadAttributeList(attributes)
        for opened in (information.hThread, information.hProcess, parent):
            if opened:
                kernel32.CloseHandle(opened)


# CALLER, writing its outcome to the file named by its third argument (a forged child has no stdout).
CALLER_TO_FILE = CALLER.replace('print(json.dumps(', 'open(sys.argv[3], "w", encoding="utf-8").write(json.dumps(')


def test_the_owner_started_rule_is_membership_of_the_runtimes_job(tmp_path: Path, record_property) -> None:
    """An owner-started Runtime's callers are the processes in its job (Phase B), never a PID walk."""
    ran, seen, owners = [], {}, []

    with OwnedTree() as tree:
        def authenticate(pid: int) -> None:
            facts = _win32.parent_and_creation(pid)
            seen[pid] = {'in_job': tree.contains(pid), 'parent': facts[0] if facts else None}
            authenticate_process(owners[0], pid, tree)

        with worker_connection(tmp_path, lambda request: ran.append(request) or {'ran': request},
                               authenticate=authenticate) as address:
            owners.append(owner := tree.start(child(OWNER, address, CALLER), stdin=subprocess.PIPE,
                                              stdout=subprocess.PIPE, encoding='utf-8'))
            try:
                # A sibling of the owner, started by this same user, poses as its caller.
                sibling = run_caller(address, 'sibling')
                assert sibling['code'] == 'authority-denied', sibling
                # A same-user process that names the owner as its parent.
                outcome = tmp_path / 'forged.json'
                forged = run_with_parent(owner.pid, child(CALLER_TO_FILE, address, 'forged', str(outcome)))
                forged_result = json.loads(outcome.read_text(encoding='utf-8'))
                assert seen[forged]['parent'] == owner.pid  # the forgery took effect
                record_property('forged_parent_in_job', seen[forged]['in_job'])
                if seen[forged]['in_job']:
                    # The weakness README.md states: the named parent's job is inherited, so it is accepted.
                    assert forged_result == {'result': {'ran': {'from': 'forged'}}}
                else:
                    assert forged_result['code'] == 'authority-denied', forged_result
                # The owner's own child, in the job, is accepted.
                owner.stdin.write('go\n')
                owner.stdin.flush()
                assert json.loads(owner.stdout.readline()) == {'result': {'ran': {'from': 'descendant'}}}
                assert any(facts['in_job'] for facts in seen.values())
            finally:
                owner.stdin.close()
                owner.wait(30)
                owner.stdout.close()
    assert {'from': 'descendant'} in ran and {'from': 'sibling'} not in ran
    # A closed job handle or no tree never admits anyone.
    assert not caller_in_tree(owner.pid, tree, os.getpid())
    assert not caller_in_tree(owner.pid, None, os.getpid())


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
