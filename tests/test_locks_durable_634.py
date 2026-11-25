"""Locks without a time limit, non-blocking locks and durable replacement (634).

The Windows-only tests check what POSIX already guarantees: a lock wait has
no time limit, a replacement survives a reader holding the destination for a
moment, and a Session archive is refused off local NTFS or ReFS. The others
run everywhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from graftling.durable import REPLACE_RETRY_SECONDS, replace_durably
from graftling.locks import exclusive, try_exclusive
from graftling.sessions.archive import JsonlSessionStore

windows_only = pytest.mark.skipif(os.name != 'nt', reason='Windows-only: checks the Win32 lock, rename and volume paths')

HOLDER = '''
import sys, time
from pathlib import Path
from graftling.locks import exclusive
with exclusive(Path(sys.argv[1])):
    print('held', flush=True)
    time.sleep(600)
'''


def header(session: str = 's-634') -> dict:
    """Return a plain DSH Session header."""
    return {'version': 5, 'id': session, 'createdAt': 100, 'isSeeded': False}


def hold_in_child(path: Path) -> subprocess.Popen:
    """Start a process that holds ``exclusive(path)`` until it is killed."""
    child = subprocess.Popen([sys.executable, '-c', HOLDER, str(path)], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == 'held'
    return child


def end(child: subprocess.Popen) -> None:
    """Kill a holder process and reap it."""
    child.kill()
    child.wait(timeout=30)
    child.stdout.close()


def eventually_free(path: Path, seconds: float = 10.0) -> bool:
    """Whether ``try_exclusive`` takes the lock within ``seconds``.

    The kernel releases a dead holder's lock promptly, but Windows does not
    promise when.
    """
    deadline = time.monotonic() + seconds
    while True:
        with try_exclusive(path) as owned:
            if owned:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def test_try_exclusive_takes_only_a_free_lock(tmp_path: Path) -> None:
    """A held lock is refused at once, also to another handle in the same thread."""
    path = tmp_path / 'locks' / 'owner.lock'

    with try_exclusive(path) as owned:
        assert owned
        with try_exclusive(path) as second:
            assert not second

    with exclusive(path):
        started = time.monotonic()
        with try_exclusive(path) as owned:
            assert not owned
        assert time.monotonic() - started < 5

    with try_exclusive(path) as owned:
        assert owned


def test_try_exclusive_sees_another_process_and_its_death(tmp_path: Path) -> None:
    """Another process's lock is refused; once that process is killed, it is free."""
    path = tmp_path / 'owner.lock'
    child = hold_in_child(path)
    try:
        with try_exclusive(path) as owned:
            assert not owned
    finally:
        end(child)

    assert eventually_free(path)


def test_replace_durably_publishes_whole_content_without_leftovers(tmp_path: Path) -> None:
    """The destination holds exactly the new bytes; no temporary file remains."""
    path = tmp_path / 'record.json'
    path.write_bytes(b'old')

    replace_durably(path, b'new\n', sync_file=True, sync_directory=True)
    replace_durably(path, b'newer\n', sync_file=False)

    assert path.read_bytes() == b'newer\n'
    assert sorted(item.name for item in tmp_path.iterdir()) == ['record.json']


def test_session_archive_written_by_two_stores_of_one_file(tmp_path: Path) -> None:
    """A second store replaces the archive while the first is open; both see each change."""
    path = tmp_path / 'sessions' / 'p' / 's-634.jsonl'
    first = JsonlSessionStore.create(path, header())
    second = JsonlSessionStore(path)

    second.mark_boundary('second', 1)
    first.mark_boundary('first', 2)

    assert second.position('first') == -1
    second.refresh()
    assert (second.position('first'), second.position('second')) == (2, 1)
    assert JsonlSessionStore(path).revision == 2


@windows_only
def test_exclusive_waits_past_ten_seconds_and_takes_a_dead_holders_lock(tmp_path: Path) -> None:
    """The wait has no time limit; killing the holder process releases the lock."""
    path = tmp_path / 'agent.lock'
    child = hold_in_child(path)
    outcome: dict = {}

    def wait() -> None:
        try:
            with exclusive(path):
                outcome['acquired'] = time.monotonic()
        except BaseException as error:  # reported by the assertions below
            outcome['error'] = error

    waiter = threading.Thread(target=wait, daemon=True)
    try:
        waiter.start()
        waiter.join(11)
        assert waiter.is_alive() and outcome == {}, outcome
    finally:
        killed = time.monotonic()
        end(child)

    waiter.join(15)
    assert 'error' not in outcome, outcome
    assert outcome['acquired'] >= killed


@windows_only
def test_replace_durably_retries_while_a_reader_holds_the_destination(tmp_path: Path) -> None:
    """A reader's handle delays the rename; the replacement lands once it closes."""
    path = tmp_path / 'record.json'
    path.write_bytes(b'old')
    outcome: dict = {}

    def write() -> None:
        try:
            replace_durably(path, b'new')
            outcome['done'] = True
        except BaseException as error:  # reported by the assertions below
            outcome['error'] = error

    writer = threading.Thread(target=write, daemon=True)
    with path.open('rb') as reader:
        writer.start()
        time.sleep(0.3)
        assert writer.is_alive(), outcome
        assert reader.read() == b'old'

    writer.join(REPLACE_RETRY_SECONDS + 10)
    assert outcome == {'done': True}
    assert path.read_bytes() == b'new'


@windows_only
def test_replace_durably_gives_up_after_its_retry_time(tmp_path: Path) -> None:
    """A destination held open throughout fails with PermissionError and stays intact."""
    path = tmp_path / 'record.json'
    path.write_bytes(b'old')

    with path.open('rb'):
        started = time.monotonic()
        with pytest.raises(PermissionError):
            replace_durably(path, b'new')
        elapsed = time.monotonic() - started

    assert REPLACE_RETRY_SECONDS <= elapsed < REPLACE_RETRY_SECONDS + 5
    assert path.read_bytes() == b'old'
    assert sorted(item.name for item in tmp_path.iterdir()) == ['record.json']


@windows_only
@pytest.mark.parametrize('volume', [
    ('E:\\', 'FAT32', 2),
    ('\\\\server\\share\\', 'NTFS', 4),
])
def test_session_archive_refuses_a_volume_without_change_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    volume: tuple,
) -> None:
    """Only local NTFS or ReFS holds an archive; the error says what was found."""
    from graftling import _win32

    path = tmp_path / 'sessions' / 'p' / 's-634.jsonl'
    JsonlSessionStore.create(path, header())

    monkeypatch.setattr(_win32, 'volume_facts', lambda directory: volume)
    with pytest.raises(ValueError, match='needs a local NTFS or ReFS volume') as refused:
        JsonlSessionStore(path)
    assert volume[1] in str(refused.value)
    with pytest.raises(ValueError, match='needs a local NTFS or ReFS volume'):
        JsonlSessionStore.create(tmp_path / 'sessions' / 'p' / 'other.jsonl', header('other'))
    assert not (tmp_path / 'sessions' / 'p' / 'other.jsonl').exists()
