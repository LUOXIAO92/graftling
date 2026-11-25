"""Tool and member processes listed and stopped through the execution's tree (637, W5).

POSIX keeps the environment scan of ``GRAFTLING_MEMBER_RUN``; Windows lists the
Job Object named after the marker (``graftling-<marker>``) and terminates it.
Fake Runtimes are Python scripts started through ``sys.executable``. Each
starts a tool that leaves its process group (a new session on POSIX, a new
process group on Windows) and inherits handles as Node and Rust Runtimes start
tools; the tool starts a grandchild the same way. Every test ends requiring
every PID it recorded to be gone.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from graftling import control
from graftling.member_processes import MEMBER_RUN_ENV, find_marked_processes, new_marker, stop_marked_processes
from graftling.process import process_alive
from graftling.process_tree import OwnedTree, job_name
from graftling.tool_descendants import identity, stop_tool_processes

WINDOWS = sys.platform == 'win32'
QUIET = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}

# argv: out directory, tag, then the tag of the child to start (or nothing). Starts that child outside
# its process group with inherited handles, records its own PID in <out>/<tag>, then sleeps.
PROCESS = r'''
import os, subprocess, sys, time
out, tag, child = sys.argv[1], sys.argv[2], sys.argv[3:]
if child:
    options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == 'nt' else {'start_new_session': True})
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen([sys.executable, '-c', open(os.path.join(out, 'process.py')).read(), out, *child],
                     close_fds=False, **options, **quiet)
with open(os.path.join(out, tag + '.tmp'), 'w') as stream:
    stream.write(str(os.getpid()))
os.replace(os.path.join(out, tag + '.tmp'), os.path.join(out, tag))
if tag == 'runtime-exits':
    while not os.path.exists(os.path.join(out, 'grandchild')):
        time.sleep(0.05)
    sys.exit(0)
time.sleep(120)
'''

# An owner that starts a Runtime in a tree named after the marker, prints its process facts and
# exits without stopping anything, as an owner that died would.
OWNER = r'''
import json, os, subprocess, sys
from graftling.process_tree import OwnedTree, tree_facts
out, marker = sys.argv[1], sys.argv[2]
quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
tree = OwnedTree(marker=marker)
runtime = tree.start([sys.executable, '-c', open(os.path.join(out, 'process.py')).read(), out, 'runtime-exits',
                      'tool', 'grandchild'], env={**os.environ, 'GRAFTLING_MEMBER_RUN': marker}, **quiet)
print(json.dumps({'runtime_pid': runtime.pid, **tree_facts(runtime.pid)}), flush=True)
'''


def wait_for(predicate, seconds: float = 20.0):
    """Return the first truthy value of ``predicate`` within ``seconds``, else its last value."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(0.05)
    return predicate()


def recorded(out: Path, *tags: str) -> dict[str, int]:
    """Wait for each tag's PID file; return the PIDs by tag."""
    assert wait_for(lambda: all((out / tag).is_file() for tag in tags)), sorted(os.listdir(out))
    return {tag: int((out / tag).read_text()) for tag in tags}


def prepared(tmp_path: Path) -> Path:
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'process.py').write_text(PROCESS, encoding='utf-8')
    return out


def start_runtime(out: Path, marker: str) -> tuple[OwnedTree, subprocess.Popen]:
    """Start a fake Runtime as an owner does: in a tree named after its marker, the marker in its environment."""
    tree = OwnedTree(marker=marker)
    runtime = tree.start([sys.executable, '-c', PROCESS, str(out), 'runtime', 'tool', 'grandchild'],
                         env={**os.environ, MEMBER_RUN_ENV: marker}, **QUIET)
    return tree, runtime


def end_all(pids: dict[str, int], starts: dict[int, int | None], tree: OwnedTree | None = None) -> None:
    """Stop what is left of ``pids`` (only a process with its recorded start), then require all gone."""
    if tree is not None:
        try:
            if not tree.gone():
                tree.kill()
        except (ProcessLookupError, OSError):
            pass
        tree.close()
    for pid in pids.values():
        now = identity(pid)
        if now is not None and now.start == starts.get(pid):
            try:
                os.kill(pid, signal.SIGTERM if WINDOWS else signal.SIGKILL)
            except OSError:
                pass
    assert wait_for(lambda: not any(process_alive(pid) for pid in pids.values()), 10), pids


def starts_of(pids: dict[str, int]) -> dict[int, int | None]:
    return {pid: getattr(identity(pid), 'start', None) for pid in pids.values()}


def test_a_marker_lists_and_stops_its_tree_with_grandchildren_that_left_the_group(tmp_path: Path) -> None:
    """find/stop_marked_processes: POSIX by the environment, Windows by the marker's job."""
    out, marker = prepared(tmp_path), new_marker()
    tree, runtime = start_runtime(out, marker)
    pids: dict[str, int] = {}
    try:
        pids = recorded(out, 'runtime', 'tool', 'grandchild')
        starts = starts_of(pids)
        listed = find_marked_processes(marker)
        print('listed:', json.dumps(listed))
        assert set(pids.values()) <= set(listed)

        report = stop_marked_processes(marker)
        print('report:', json.dumps(report))
        assert set(pids.values()) <= {item['pid'] for item in report['stopped']}
        assert report['remaining'] == [] and 'error' not in report
        runtime.wait(10)
        assert wait_for(lambda: not any(process_alive(pid) for pid in pids.values()), 10)
    finally:
        end_all(pids, starts_of(pids) if pids else {}, tree)
        if runtime.poll() is None:
            runtime.kill()
        runtime.wait(10)


def test_stop_tool_processes_ends_what_a_runtime_left_and_spares_the_excluded(tmp_path: Path) -> None:
    """The excluded Runtime keeps running; its tool and the tool's grandchild end (Windows: one by one in the job)."""
    out, marker = prepared(tmp_path), new_marker()
    tree, runtime = start_runtime(out, marker)
    pids: dict[str, int] = {}
    try:
        pids = recorded(out, 'runtime', 'tool', 'grandchild')
        starts = starts_of(pids)
        report = stop_tool_processes(None, marker, {runtime.pid})
        print('report:', json.dumps(report))
        stopped = {item['pid'] for item in report['stopped']}
        assert {pids['tool'], pids['grandchild']} <= stopped and runtime.pid not in stopped
        assert report['remaining'] == [] and 'error' not in report
        assert not process_alive(pids['tool']) and not process_alive(pids['grandchild'])
        assert runtime.poll() is None and identity(runtime.pid).start == starts[runtime.pid]
    finally:
        end_all(pids, starts_of(pids) if pids else {}, tree)
        if runtime.poll() is None:
            runtime.kill()
        runtime.wait(10)


def test_reconciling_an_execution_stops_the_tools_its_dead_owner_and_runtime_left(tmp_path: Path) -> None:
    """control._stop_escaped opens the recorded job by name (Windows) or scans the marker (POSIX)."""
    out, marker = prepared(tmp_path), new_marker()
    owner = subprocess.run([sys.executable, '-c', OWNER, str(out), marker], capture_output=True, text=True,
                           timeout=60)
    assert owner.returncode == 0, owner.stderr
    facts = json.loads(owner.stdout)
    pids = recorded(out, 'tool', 'grandchild')
    starts = starts_of(pids)
    try:
        assert wait_for(lambda: not process_alive(facts['runtime_pid']), 10), 'the Runtime did not exit'
        assert ('runtime_job' in facts) == WINDOWS and facts.get('runtime_job', job_name(marker)) == job_name(marker)
        execution = {'process': {**facts, 'tool_marker': marker}}
        control._stop_escaped(execution)
        report = execution['escaped_processes']
        print('escaped_processes:', json.dumps(report))
        assert set(pids.values()) <= {item['pid'] for item in report['stopped']}
        assert report['remaining'] == [] and 'error' not in report
        assert wait_for(lambda: not any(process_alive(pid) for pid in pids.values()), 10)
    finally:
        end_all(pids, starts)


def test_a_marker_whose_tree_was_not_started_through_owned_tree_has_no_job(tmp_path: Path) -> None:
    """POSIX finds the marked process by its environment; Windows says in error that the marker has no job."""
    out, marker = prepared(tmp_path), new_marker()
    plain = subprocess.Popen([sys.executable, '-c', PROCESS, str(out), 'plain'],
                             env={**os.environ, MEMBER_RUN_ENV: marker}, **QUIET)
    pids: dict[str, int] = {}
    try:
        pids = recorded(out, 'plain')
        starts = starts_of(pids)
        if not WINDOWS:
            assert pids['plain'] in find_marked_processes(marker)
            return
        with pytest.raises(OSError) as raised:
            find_marked_processes(marker)
        text = str(raised.value)
        assert f'No job {job_name(marker)} is open' in text and 'OwnedTree' in text, text
        with pytest.raises(OSError, match='prefix'):
            find_marked_processes(marker, prefix=True)
        assert stop_marked_processes(marker) == {'found': [], 'stopped': [], 'remaining': [], 'error': text}
        report = stop_tool_processes(None, marker, error='earlier failure')
        print('report:', json.dumps(report))
        assert report['found'] == [] and report['error'] == 'earlier failure; job scan: ' + text
        assert identity(plain.pid).start == starts[plain.pid], 'the marked process is untouched'
    finally:
        end_all(pids, starts_of(pids) if pids else {})
        if plain.poll() is None:
            plain.kill()
        plain.wait(10)


def test_the_windows_listing_with_a_stand_in_job_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows branches of every listing and stop, on a stand-in job API (any platform)."""
    from graftling import _win32
    from graftling import member_processes
    from test_process_tree_635 import StandInJobs

    jobs = StandInJobs()
    ended: list[int] = []

    def terminate_in_job(pid: int, job: int, exit_code: int = 1) -> bool:
        members = jobs.jobs[jobs.handles[job]]
        if pid not in members:
            return False
        members.discard(pid)
        ended.append(pid)
        return True

    monkeypatch.setattr('graftling.process_tree.windows', lambda: True)
    for name in ('create_job', 'open_job', 'terminate_job', 'job_process_ids', 'close_handle'):
        monkeypatch.setattr(_win32, name, getattr(jobs, name))
    monkeypatch.setattr(_win32, 'terminate_in_job', terminate_in_job)
    monkeypatch.setattr(member_processes, '_windows_argv', lambda pid: ['tool', str(pid)])
    runtime, tool, grandchild = 4_000_001, 4_000_002, 4_000_003
    marker = 'm637'
    name = job_name(marker)

    with OwnedTree(marker=marker) as tree:
        assert tree.name == name
        jobs.jobs[name] |= {runtime, tool, grandchild}
        assert find_marked_processes(marker) == {pid: f'tool {pid}' for pid in (runtime, tool, grandchild)}
        assert list(find_marked_processes(marker, {runtime})) == [tool, grandchild]
        with pytest.raises(OSError, match='prefix'):
            find_marked_processes(marker, prefix=True)

        # An excluded process in the job: only the others end, one by one; the job is not terminated.
        report = stop_tool_processes([{'pid': 1, 'uid': 0, 'start': 0}], marker, {runtime})
        assert ended == [tool, grandchild] and jobs.terminated == []
        assert report == {'found': [{'pid': tool, 'command': f'tool {tool}'},
                                    {'pid': grandchild, 'command': f'tool {grandchild}'}],
                          'stopped': [{'pid': tool, 'command': f'tool {tool}'},
                                      {'pid': grandchild, 'command': f'tool {grandchild}'}],
                          'remaining': []}

        # Nothing excluded is in the job: the job is terminated whole.
        report = stop_marked_processes(marker)
        assert jobs.terminated == [name] and report['stopped'] == [{'pid': runtime, 'command': f'tool {runtime}'}]
        assert stop_marked_processes(marker) == {'found': [], 'stopped': [], 'remaining': []}

        # A process that does not leave the job is reported under remaining.
        monkeypatch.setattr(_win32, 'terminate_job', lambda job, exit_code=1: None)
        jobs.jobs[name].add(tool)
        assert member_processes.stop_job(name, {os.getpid()}, 0.1) == ({tool: ['tool', str(tool)]}, {tool})

        # control._stop_escaped: the recorded job name first, else the marker's; the caller's ancestors are spared.
        execution = {'process': {'tool_marker': 'not-this-one', 'runtime_job': name,
                                 'tool_descendants': '/nonexistent/sidecar.json'}}
        jobs.jobs[name] = {tool, os.getpid()}
        control._stop_escaped(execution)
        # The caller is in the job, so only the tool is ended, by itself.
        assert execution['escaped_processes'] == {'found': [{'pid': tool, 'command': f'tool {tool}'}],
                                                  'stopped': [{'pid': tool, 'command': f'tool {tool}'}],
                                                  'remaining': []}
        assert ended[-1] == tool and jobs.jobs[name] == {os.getpid()}

    # No job: an honest error everywhere, never a silent empty result.
    missing = job_name('no-tree')
    report = stop_marked_processes('no-tree')
    assert report['found'] == [] and f'No job {missing} is open' in report['error'] and 'OwnedTree' in report['error']
    assert stop_tool_processes(None, 'no-tree')['error'] == 'job scan: ' + report['error']
    execution = {'process': {'tool_marker': 'no-tree'}}
    control._stop_escaped(execution)
    assert execution['escaped_processes']['error'] == 'job scan: ' + report['error']
    # A record with no marker and no job is left as it was.
    execution = {'process': {'tool_descendants': '/nonexistent/sidecar.json'}}
    control._stop_escaped(execution)
    assert 'escaped_processes' not in execution
