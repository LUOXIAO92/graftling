"""473: a Codex child ends normally after its owning process's main thread has returned.

The Codex owner runs on a non-daemon thread, so a member Worker's interpreter
waits for it. But when the main thread ends, ``threading._shutdown`` first sets
``concurrent.futures``' global shutdown flag, and from then on every executor,
including ``asyncio.to_thread``, refuses work with "cannot schedule new futures
after interpreter shutdown". The owner's end processing used it to close its
connections, so the child ended ``RUNTIME_WORKER_FAILED`` and the rest of its
end processing (observer cleanup, ``stderr.log``) never ran.

Here a real owner process returns from its main thread while its child's turn
still runs (real interpreter shutdown); the controlled app-server peer then
completes the turn. Real ``/bin/sh`` and ``/bin/sleep`` tools; every process
this test stops is one it started, checked by pid and start time.

Windows (637): the tool is a Python program whose sleeping grandchild leaves
its process group (``reclaim.WINDOWS_TOOL``); both are found through the
service's job (``find_marked_processes``) and the owner's reclaim stops them
through it. A process the service starts is in its job, so another
execution's process is one the test starts itself, outside the job.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

from graftling.member_processes import find_marked_processes
from graftling.process import process_arguments
from graftling.tool_descendants import Identity, identity
import test_codex_tool_reclaim_462 as reclaim

WINDOWS = sys.platform == 'win32'

OWNER = r'''
import json, sys
from pathlib import Path
from graftling import AgentControl, Caller, bind
from graftling.codex.driver import CodexDriver
control = AgentControl(Path(sys.argv[1]), {'codex': CodexDriver(json.loads(sys.argv[2]))})
call = bind(control, Caller(*json.loads(sys.argv[3])))
print(json.dumps(call({'action': 'create', 'runtime': 'codex', 'instruction': 'tool-end'}).document), flush=True)
# The main thread returns while the child's turn still runs.
'''

native_settings = reclaim.native_settings


def windows_tools(root: Path, agent: str, other: subprocess.Popen) -> dict[str, Identity]:
    """Windows: the tool and its grandchild in the service's job, by their arguments, and ``other``."""
    marker = reclaim.record(root, agent)['process'].get('tool_marker')
    try:
        listed = find_marked_processes(marker) if marker else {}
    except OSError:
        return {}  # the job is not open yet
    named = {reclaim.WINDOWS_TOOL: 'shell', reclaim.WINDOWS_SLEEP: 'sleep'}
    found: dict[str, Identity | None] = {'other': identity(other.pid)}
    for pid in listed:
        try:
            last = process_arguments(pid)[-1:]
        except OSError:
            continue
        if last and last[0] in named:
            found[named[last[0]]] = identity(pid)
    return {key: item for key, item in found.items() if item is not None}


def child_evidence(owner: subprocess.Popen, root: Path, agent: str) -> str:
    """The owner's exit and stderr and the Agent's last execution, so a child that never started its tool names why."""
    try:
        _, stderr = owner.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        owner.kill()
        _, stderr = owner.communicate(timeout=10)
    try:
        execution = reclaim.record(root, agent)
    except (OSError, ValueError, KeyError, IndexError) as error:
        execution = {'unreadable': f'{type(error).__name__}: {error}'}
    shown = {key: execution.get(key) for key in ('state', 'outcome', 'error', 'diagnostic', 'unreadable')}
    return (f'owner exit {owner.returncode}; last execution {json.dumps(shown)}; '
            f'owner stderr (tail): {(stderr or "")[-3000:]}')


def stop_own_windows(items: list[Identity]) -> None:
    """Terminate only processes this test started, after checking pid and start time."""
    for item in items:
        if reclaim.is_alive(item):
            os.kill(item.pid, 9)  # TerminateProcess on Windows
    reclaim.wait_for(lambda: not any(reclaim.is_alive(item) for item in items), 5)


def test_child_ends_completed_after_the_owner_main_thread_returns(tmp_path: Path, native_settings: dict) -> None:
    """The turn completes during interpreter shutdown; the end processing runs in full."""
    root  = tmp_path / 'agents'
    since = identity(os.getpid()).start
    quiet = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(303)'], **quiet) if WINDOWS else None
    owner = subprocess.Popen(
        [sys.executable, '-c', OWNER, str(root), json.dumps(native_settings), json.dumps(reclaim.CALLER)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    found: dict = {}
    try:
        created = json.loads(owner.stdout.readline())
        tools   = ((lambda: windows_tools(root, created['agent'], other)) if WINDOWS
                   else (lambda: reclaim.tools(since)))
        found   = reclaim.wait_for(lambda: len(tools()) == 3, 10) and tools()
        assert found, 'the peer did not start the tool; ' + child_evidence(owner, root, created['agent'])

        # The process exits only after the non-daemon owner has finished the end processing.
        stdout, stderr = owner.communicate(timeout=30)
        print('owner exit:', owner.returncode, 'stderr:', stderr[-2000:])
        assert owner.returncode == 0, stderr

        execution = reclaim.record(root, created['agent'])
        print('end record:', json.dumps({key: execution.get(key) for key in ('state', 'outcome', 'error')}))
        assert execution['state'] == 'ended'
        assert execution['outcome'] == 'completed' and execution.get('error') is None, execution

        directory   = Path(execution['process']['tool_descendants']).parent
        termination = next(directory.glob('codex-termination-*.yml'), None)
        assert termination is not None, 'the owner-side reclaim did not record its end'
        report = yaml.safe_load(termination.read_text(encoding='utf-8'))['escaped_processes']
        print('escaped_processes:', json.dumps(report))
        stopped  = sorted(item['pid'] for item in report['stopped'])
        expected = sorted([found['shell'].pid, found['sleep'].pid])
        if WINDOWS:
            # The job may also hold the console host of the service's windowless console (inference).
            extra = [item for item in report['stopped'] if item['pid'] not in expected]
            assert set(expected) <= set(stopped), report
            assert all('conhost' in item['command'].lower() for item in extra), report
        else:
            assert stopped == expected
        assert not reclaim.is_alive(found['shell']) and not reclaim.is_alive(found['sleep'])
        assert reclaim.is_alive(found['other']), "another execution's process is untouched"
        assert (directory / 'stderr.log').is_file(), 'the end processing stopped before stderr.log'
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(5)
        if WINDOWS:
            stop_own_windows(list((found or {}).values()))
            other.kill()
            other.wait(5)
        else:
            reclaim.stop_own(list((found or {}).values()) + [
                item for items in reclaim.ours(since).values() for item in items
                if reclaim.argv(item.pid).startswith(('/bin/sleep 303', sys.executable + ' -c'))
            ])
    # Every process this test recorded is gone.
    assert not any(reclaim.is_alive(item) for item in (found or {}).values()), found
