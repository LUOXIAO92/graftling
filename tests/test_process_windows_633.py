"""Process facts and liveness on every platform, including Windows (633, W1).

The tests without a fake run the real platform path: Win32 on the Windows
runner, libproc and sysctl on macOS, /proc on Linux. The fakes check the
Windows parent rule (created before the child) on any host.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from graftling import _win32, control, process
from graftling.process import process_alive, process_ancestors, process_arguments, process_executable

WINDOWS = sys.platform == 'win32'
windows_only = pytest.mark.skipif(not WINDOWS, reason='Windows only: calls Win32 directly')
SLEEP = 'import time; time.sleep(60)'
# Python's subprocess quotes these for CommandLineToArgvW; POSIX passes them as they are.
ARGUMENTS = ['two words', 'say "hi"', 'back\\slash\\', 'trail\\"q', 'tab\there']
NOT_A_PROCESS = 2**31 - 4


@pytest.fixture
def sleeper():
    """A real child that runs until the test ends; stopped afterwards."""
    children = []

    def start(*arguments: str) -> subprocess.Popen:
        child = subprocess.Popen([sys.executable, '-c', SLEEP, *arguments])
        children.append(child)
        return child

    yield start
    for child in children:
        child.kill()
        child.wait()


def ended_pid() -> int:
    """The PID of a child that has exited and been reaped."""
    return int(subprocess.run([sys.executable, '-c', 'import os; print(os.getpid())'],
                              capture_output=True, text=True, check=True).stdout)


def test_executable_and_arguments_of_a_real_child(sleeper) -> None:
    """The executable is the started interpreter and every argument keeps its boundaries."""
    child = sleeper(*ARGUMENTS)
    assert process_executable(child.pid).resolve() == Path(sys.executable).resolve()
    assert process_arguments(child.pid) == [sys.executable, '-c', SLEEP, *ARGUMENTS]


def test_the_parent_of_a_real_child_is_this_process(sleeper) -> None:
    """The walk from a child reaches this process next, and goes on above it."""
    child = sleeper()
    chain = process_ancestors(child.pid)
    assert chain[:2] == [child.pid, os.getpid()]
    assert process_ancestors(os.getpid())[0] == os.getpid()


def test_process_alive_for_a_live_an_exited_and_an_invalid_pid(sleeper) -> None:
    """Live is alive; exited (reaped or not yet) and invalid identifiers are not."""
    assert process_alive(sleeper().pid) is True
    assert process_alive(os.getpid()) is True

    exiting = subprocess.Popen([sys.executable, '-c', 'pass'])
    # Not reaped yet: a POSIX zombie, or a Windows process whose handle Popen still holds.
    deadline = time.monotonic() + 30
    while process_alive(exiting.pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert process_alive(exiting.pid) is False
    assert exiting.wait(5) == 0
    assert process_alive(exiting.pid) is False
    assert process_alive(ended_pid()) is False

    for invalid in (0, -1, True, None, '1', 1.5, NOT_A_PROCESS):
        assert process_alive(invalid) is False, invalid


def test_process_alive_never_terminates_its_target(sleeper) -> None:
    """Asking is not signalling: the child runs on after repeated checks (os.kill(pid, 0) ends it on Windows)."""
    child = sleeper()
    for _ in range(5):
        assert process_alive(child.pid) is True
        assert control._alive(child.pid) is True
    time.sleep(0.5)
    assert child.poll() is None
    assert process_alive(child.pid) is True


def test_process_alive_refuses_a_process_created_after_started(sleeper, monkeypatch: pytest.MonkeyPatch) -> None:
    """``started`` marks a reused PID: a process created after it is not the one meant."""
    child = sleeper()
    assert process_alive(child.pid, time.time()) is True
    created = process._process_created(os.getpid())
    assert created is not None and created <= time.time()
    assert process_alive(os.getpid(), time.time()) is True
    assert process_alive(os.getpid(), created - 10) is False
    assert process_alive(ended_pid(), time.time()) is False
    # An unreadable creation time never turns a live process into a dead one.
    monkeypatch.setattr(process, '_process_created', lambda pid: None)
    assert process_alive(os.getpid(), 0.0) is True


def test_control_alive_tells_an_ended_process_from_a_live_one() -> None:
    """control._alive no longer counts every PID as alive on Windows (M12)."""
    assert control._alive(os.getpid()) is True
    assert control._alive(ended_pid()) is False
    assert control._alive(NOT_A_PROCESS) is False
    for invalid in (0, -5, False, None, '12'):
        assert control._alive(invalid) is False


@pytest.mark.parametrize('parent_created, expected', [(99, 20), (100, 0), (101, 0), (None, 0)],
                         ids=['earlier', 'same-time', 'later', 'unreadable'])
def test_a_windows_parent_counts_only_when_created_before_its_child(
        monkeypatch: pytest.MonkeyPatch, parent_created: int | None, expected: int) -> None:
    """A recorded parent PID created at or after its child is a reused PID: no parent."""
    monkeypatch.setattr(_win32, 'parent_and_creation', lambda pid: (20, 100) if pid == 30 else None)
    monkeypatch.setattr(_win32, 'creation_ticks', lambda pid: parent_created if pid == 20 else None)
    assert process._windows_parent(30) == expected


def test_a_windows_walk_ends_where_the_parent_is_not_older(monkeypatch: pytest.MonkeyPatch) -> None:
    """The walk stops at a reused parent PID and when a process cannot be read."""
    # pid: (recorded parent, created); 5 was created after 10, so 10's parent PID was reused.
    table = {30: (20, 300), 20: (10, 200), 10: (5, 100), 5: (1, 150)}
    monkeypatch.setattr(_win32, 'parent_and_creation', table.get)
    monkeypatch.setattr(_win32, 'creation_ticks', lambda pid: table[pid][1] if pid in table else None)
    monkeypatch.setattr(process, '_process_parent', process._windows_parent)
    assert process_ancestors(30) == [30, 20, 10]
    assert process._windows_parent(99) == 0
    monkeypatch.setitem(table, 20, (0, 200))
    assert process_ancestors(30) == [30, 20]


@windows_only
def test_windows_command_lines_split_as_subprocess_quotes_them() -> None:
    """CommandLineToArgvW undoes list2cmdline, and an empty line has no arguments."""
    arguments = ['C:\\Program Files\\x.exe', *ARGUMENTS, '', 'end\\\\']
    assert _win32.split_command_line(subprocess.list2cmdline(arguments)) == arguments
    assert _win32.split_command_line('') == []


@windows_only
def test_windows_records_this_process_as_the_older_parent(sleeper) -> None:
    """The recorded parent and both creation times come from Win32 and order parent before child."""
    child = sleeper()
    parent, created = _win32.parent_and_creation(child.pid)
    assert parent == os.getpid()
    assert _win32.creation_ticks(os.getpid()) < created
    assert _win32.parent_and_creation(NOT_A_PROCESS) is None
    assert _win32.image_path(NOT_A_PROCESS) is None
    with pytest.raises(OSError):
        _win32.command_line(NOT_A_PROCESS)
    assert _win32.process_running(NOT_A_PROCESS) is False
