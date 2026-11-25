"""Owned process trees on every platform, including Windows jobs (635, W3).

Fake Runtimes are Python scripts started through ``sys.executable``, never
``#!`` scripts, so every test here runs on the Windows runner too. Each fake
starts grandchildren that write their own PID to a file; a test stops the tree
and then requires every recorded PID to be gone (``process_alive``).
POSIX runs the process-group implementation; Windows runs the job.
"""
from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import graftling
from graftling import _win32, control
from graftling.calls import Call, CallCancelled, current_call, run_stoppable
from graftling.claude.execution import read_init_tools
from graftling.dsh.service import DshService
from graftling.process import process_alive
from graftling.process_tree import (INTERRUPT, KILL, TERMINATE, OwnedTree, tree_facts, windows)
from graftling.review import _Unavailable, _ask_process
from posix_only import posix_only

WINDOWS = sys.platform == 'win32'
windows_only = pytest.mark.skipif(not WINDOWS, reason='Windows only: Job Objects')
QUIET = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
SOURCE = Path(graftling.__file__).parent

# Writes its PID to argv[1] (atomically), then sleeps.
SLEEPER = r'''
import os, sys, time
path = sys.argv[1]
with open(path + '.tmp', 'w') as stream:
    stream.write(str(os.getpid()))
os.replace(path + '.tmp', path)
time.sleep(120)
'''

# A stand-in Runtime: starts grandchildren the way argv[1] says, records its own PID, sleeps.
FAKE_RUNTIME = r'''
import json, os, subprocess, sys, time

mode, out = sys.argv[1], sys.argv[2]
quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def child(tag):
    return [sys.executable, os.path.join(out, 'sleeper.py'), os.path.join(out, tag)]


def record(name, value):
    with open(os.path.join(out, name + '.tmp'), 'w') as stream:
        stream.write(value)
    os.replace(os.path.join(out, name + '.tmp'), os.path.join(out, name))


if mode in ('plain', 'probe'):
    subprocess.Popen(child('child-1'), **quiet)
    subprocess.Popen(child('child-2'), **quiet)
elif mode == 'start-b':
    # cmd starts the grandchild in the background and exits at once.
    subprocess.Popen(['cmd', '/c', 'start', '', '/b'] + child('start-b'), **quiet)
elif mode == 'start-process':
    # Windows PowerShell joins -ArgumentList with spaces; these paths have none.
    arguments = ','.join("'" + part.replace("'", "''") + "'" for part in child('start-process')[1:])
    subprocess.Popen(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                      "Start-Process -WindowStyle Hidden -FilePath '" + sys.executable.replace("'", "''")
                      + "' -ArgumentList " + arguments], **quiet)
elif mode == 'breakaway':
    try:
        escaped = subprocess.Popen(child('escaped'), creationflags=0x01000000, **quiet)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError as error:
        outcome = {'breakaway': 'refused', 'winerror': getattr(error, 'winerror', None)}
        subprocess.Popen(child('child-1'), **quiet)
    else:
        outcome = {'breakaway': 'escaped', 'pid': escaped.pid}
    record('breakaway.json', json.dumps(outcome))
record('root', str(os.getpid()))
if mode == 'probe':
    # Init only once the grandchildren run, so the probe's stop must end them.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not all(
            os.path.exists(os.path.join(out, tag)) for tag in ('child-1', 'child-2')):
        time.sleep(0.05)
    sys.stdin.readline()
    print(json.dumps({'type': 'system', 'subtype': 'init', 'tools': ['Bash', 'Read', 'mcp__a__b']}), flush=True)
time.sleep(120)
'''

# An owner that starts a fake Runtime's tree, waits for its PIDs, prints the job name and exits
# without stopping or closing anything.
OWNER = r'''
import json, os, subprocess, sys, time
from graftling.process_tree import OwnedTree

fake, out, lifetime = sys.argv[1], sys.argv[2], sys.argv[3]
tree = OwnedTree(kill_on_close=lifetime == 'kill-on-close')
quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
root = tree.start([sys.executable, fake, 'plain', out], **quiet)
deadline = time.monotonic() + 60
while time.monotonic() < deadline and not all(
        os.path.exists(os.path.join(out, tag)) for tag in ('root', 'child-1', 'child-2')):
    time.sleep(0.05)
print(json.dumps({'name': tree.name, 'pid': root.pid}), flush=True)
'''


@pytest.fixture
def runtime(tmp_path: Path):
    """Write the fake Runtime; yield (script, out directory); kill every recorded PID left afterwards."""
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'sleeper.py').write_text(SLEEPER)
    script = tmp_path / 'fake_runtime.py'
    script.write_text(FAKE_RUNTIME)
    yield script, out
    for path in out.iterdir():
        if path.suffix or path.name == 'sleeper.py':
            continue
        pid = int(path.read_text())
        if process_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def recorded(out: Path, tags: tuple[str, ...], seconds: float = 60) -> dict[str, int]:
    """Wait for each tag's PID file and return the PIDs."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if all((out / tag).exists() for tag in tags):
            return {tag: int((out / tag).read_text()) for tag in tags}
        time.sleep(0.05)
    raise AssertionError(f'not recorded within {seconds} s: {[tag for tag in tags if not (out / tag).exists()]}')


def eventually(check, seconds: float = 15) -> bool:
    """Poll ``check`` until it is true or ``seconds`` pass; return its last answer."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return check()


def all_gone(pids) -> bool:
    return not any(process_alive(pid) for pid in pids)


# -- The contract on every platform --------------------------------------------------------------


def test_a_tree_holds_its_descendants_and_stops_with_them(runtime) -> None:
    """Start, list, contains, the recorded fact, the group or job probe, stop, and nothing left."""
    script, out = runtime
    with OwnedTree() as tree:
        root = tree.start([sys.executable, str(script), 'plain', str(out)], **QUIET)
        pids = recorded(out, ('root', 'child-1', 'child-2'))
        assert pids['root'] == root.pid == tree.pid
        assert all(tree.contains(pid) for pid in pids.values())
        assert not tree.contains(os.getpid())
        assert set(pids.values()) <= set(tree.pids())
        assert not tree.gone()
        facts = {'runtime_pid': root.pid, **tree_facts(root.pid)}
        if WINDOWS:
            assert facts['runtime_job'] == tree.name and tree.name.startswith('graftling-')
        else:
            assert facts == {'runtime_pid': root.pid} and tree.name is None
        assert control._group_alive(facts) and control._runtime_alive(facts)

        tree.kill()
        root.wait(10)
        assert eventually(tree.gone)
        assert eventually(lambda: all_gone(pids.values()))
        assert not control._group_alive(facts) and not control._runtime_alive(facts)
        with pytest.raises(ProcessLookupError):
            tree.signal(TERMINATE)
    assert tree_facts(root.pid) == {}


def test_start_options_are_a_new_session_or_a_suspended_windowless_process() -> None:
    """POSIX: start_new_session. Windows: CREATE_SUSPENDED | CREATE_NO_WINDOW, added to the caller's flags."""
    with OwnedTree() as tree:
        if WINDOWS:
            flags = tree.popen_options(0x200)['creationflags']
            assert flags & _win32.CREATE_NO_WINDOW and flags & _win32.CREATE_SUSPENDED and flags & 0x200
            assert set(tree.popen_options()) == {'creationflags'}
        else:
            assert tree.popen_options() == {'start_new_session': True}
    with pytest.raises(ValueError):
        OwnedTree().signal('hangup')


def test_utf8_text_crosses_a_tree_pipe_whatever_the_code_page(tmp_path: Path) -> None:
    """Text pipes name UTF-8, so the system code page (PYTHONUTF8=0) does not decide what is read."""
    text = '日本語 中文 Grüße ✓\n'
    echo = 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'
    environment = {**os.environ, 'PYTHONUTF8': '0'}
    environment.pop('PYTHONIOENCODING', None)
    with OwnedTree() as tree:
        process = tree.start([sys.executable, '-c', echo], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, encoding='utf-8', env=environment)
        answer, _ = process.communicate(text, timeout=30)
    assert answer.replace('\r\n', '\n') == text


def test_the_runtime_sites_use_the_tree_and_utf8_text_pipes() -> None:
    """629 advice 3.3 sites: no process-group call, session flag or POSIX signal name remains (M14);
    every text pipe names UTF-8 (M4); the Claude tool probe has no select (M2)."""
    sites = ['calls.py', 'review.py', 'claude/execution.py', 'pi/execution.py', 'codex/app_server.py',
             'dsh/service.py', 'control.py']
    found = []
    for site in sites:
        tree = ast.parse((SOURCE / site).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and (
                    node.attr in {'killpg', 'SIGKILL', 'SIGTERM', 'SIGINT'}
                    or isinstance(node.value, ast.Name) and node.value.id == 'select'):
                found.append(f'{site}:{node.lineno} {ast.unparse(node)}')
            if isinstance(node, ast.Call):
                keywords = {keyword.arg: keyword.value for keyword in node.keywords}
                if 'start_new_session' in keywords:
                    found.append(f'{site}:{node.lineno} start_new_session')
                text = keywords.get('text')
                if isinstance(text, ast.Constant) and text.value is True:
                    encoding = keywords.get('encoding')
                    if not (isinstance(encoding, ast.Constant) and encoding.value == 'utf-8'):
                        found.append(f'{site}:{node.lineno} text pipe without encoding="utf-8"')
    assert found == []


def test_a_tree_left_by_an_owner_that_exited_is_probed_and_stopped_by_a_later_process(runtime, tmp_path) -> None:
    """Decision 3: no kill-on-close, so the tree outlives its owner; a later process opens it
    (Windows: by the recorded job name; POSIX: by the group's leader) and stops all of it."""
    script, out = runtime
    owner = tmp_path / 'owner.py'
    owner.write_text(OWNER)
    started = subprocess.run([sys.executable, str(owner), str(script), str(out), 'outlive'],
                             capture_output=True, text=True, timeout=120)
    assert started.returncode == 0, started.stderr
    left = json.loads(started.stdout)
    pids = recorded(out, ('root', 'child-1', 'child-2'))
    assert pids['root'] == left['pid']
    time.sleep(1)
    assert all(process_alive(pid) for pid in pids.values()), 'the tree ended with its owner'

    facts = {'runtime_pid': left['pid'], **({'runtime_job': left['name']} if WINDOWS else {})}
    assert control._group_alive(facts) and control._runtime_alive(facts)
    with OwnedTree.open(pid=left['pid'], name=left['name']) as tree:
        assert not tree.gone()
        assert set(pids.values()) <= set(tree.pids())
        if WINDOWS:
            # A live process outside the job is not this Runtime (a reused PID).
            assert not control._runtime_alive({'runtime_pid': os.getpid(), 'runtime_job': left['name']})
        tree.signal(TERMINATE)
        assert eventually(tree.gone)
    assert eventually(lambda: all_gone(pids.values()))
    assert not control._group_alive(facts)


def test_run_stoppable_ends_the_whole_tree_when_the_call_stops(runtime) -> None:
    """calls.py: a stopped call ends the command and its grandchildren."""
    script, out = runtime
    call = Call()
    threading.Thread(target=lambda: (recorded(out, ('root', 'child-1', 'child-2')), call.stop.set()),
                     daemon=True).start()
    with current_call(call), pytest.raises(CallCancelled):
        run_stoppable([sys.executable, str(script), 'plain', str(out)], **QUIET)
    pids = recorded(out, ('root', 'child-1', 'child-2'), seconds=1)
    assert eventually(lambda: all_gone(pids.values()))


def test_a_reviewer_over_its_time_is_killed_with_its_tree(runtime) -> None:
    """review.py: a reviewer that does not answer in time is killed with everything it started."""
    script, out = runtime
    reviewer = {'command': [sys.executable, str(script), 'plain', str(out)], 'timeout_seconds': 1}
    with pytest.raises(_Unavailable, match='the process was killed'):
        _ask_process(reviewer, {'tool': 'x'})
    # The fake may not have started its grandchildren within the second; only what it recorded counts.
    pids = [int(path.read_text()) for path in out.iterdir() if not path.suffix and path.name != 'sleeper.py']
    assert eventually(lambda: all_gone(pids))
    time.sleep(1)
    late = [int(path.read_text()) for path in out.iterdir() if not path.suffix and path.name != 'sleeper.py']
    assert all_gone(late)


def test_the_claude_tool_probe_reads_init_through_threads_and_stops_the_tree(runtime) -> None:
    """claude/execution.py tool probe (M2): pipes are read by threads, and the tree is stopped at init."""
    script, out = runtime
    tools, cause = read_init_tools([sys.executable, str(script), 'probe', str(out)], dict(os.environ),
                                   out, timeout=60)
    assert (tools, cause) == (['Bash', 'Read'], 'ok')
    pids = recorded(out, ('root', 'child-1', 'child-2'), seconds=1)
    assert eventually(lambda: all_gone(pids.values()))
    tools, cause = read_init_tools([sys.executable, '-c', 'import sys; sys.exit(3)'], dict(os.environ), out,
                                   timeout=30)
    assert tools is None and cause.startswith('no system/init (exit 3')


def test_the_dsh_service_tree_is_killed_on_close_and_has_no_watcher_on_windows(tmp_path: Path) -> None:
    """dsh/service.py: the service's tree ends with its owner (kill-on-close, decision 3); on Windows
    no watcher process is started (M15), on POSIX the watcher is as before."""
    # ``python web --no-open --port 0`` runs this file: a "DSH" that exits before authentication.
    (tmp_path / 'web').write_text('print("no launch link")\n')
    service = DshService(sys.executable, tmp_path, dict(os.environ))
    with pytest.raises(service.error_type) as failed:
        service.start()
    assert failed.value.code == 'RUNTIME_START_FAILED'
    try:
        assert service.tree.kill_on_close and service.tree.pid == service.process.pid
        if WINDOWS:
            assert service.watcher is None and service.tree.name.startswith('graftling-')
        else:
            assert service.watcher is not None
    finally:
        service.close()


# -- POSIX: the process group ---------------------------------------------------------------------


@posix_only('checks the process group (os.getpgid) and its POSIX signals')
def test_posix_steps_are_the_group_signals(tmp_path: Path) -> None:
    """INTERRUPT, TERMINATE and KILL are SIGINT, SIGTERM and SIGKILL to the whole group."""
    log = tmp_path / 'signals'
    handler = (f'import signal, sys, time\n'
               f'def note(number, frame):\n'
               f'    open({str(log)!r}, "a").write(signal.Signals(number).name + "\\n")\n'
               f'signal.signal(signal.SIGINT, note)\n'
               f'signal.signal(signal.SIGTERM, note)\n'
               f'open({str(log)!r}, "a").write("ready\\n")\n'
               f'time.sleep(60)\n')
    with OwnedTree() as tree:
        process = tree.start([sys.executable, '-c', handler], **QUIET)
        assert eventually(lambda: log.exists() and 'ready' in log.read_text())
        assert os.getpgid(process.pid) == process.pid and tree.contains(process.pid)
        tree.signal(INTERRUPT)
        assert eventually(lambda: 'SIGINT' in log.read_text())
        tree.signal(TERMINATE)
        assert eventually(lambda: 'SIGTERM' in log.read_text())
        tree.signal(KILL)
        assert process.wait(10) == -signal.SIGKILL
        assert eventually(tree.gone)
    assert OwnedTree.open(pid=process.pid).gone()
    for invalid in (None, 0, -1, True):
        with pytest.raises(ValueError):
            OwnedTree.open(pid=invalid)


# -- Windows: the job -----------------------------------------------------------------------------


@windows_only
@pytest.mark.parametrize('mode', ['start-b', 'start-process'])
def test_windows_grandchildren_started_by_cmd_start_or_start_process_stop_with_the_job(runtime, mode) -> None:
    """``cmd /c start /b`` and PowerShell ``Start-Process`` children stay in the job and end with it."""
    script, out = runtime
    with OwnedTree() as tree:
        root = tree.start([sys.executable, str(script), mode, str(out)], **QUIET)
        pids = recorded(out, ('root', mode))
        assert tree.contains(pids[mode]) and pids[mode] in tree.pids()
        tree.signal(INTERRUPT)  # every step terminates the job
        root.wait(10)
        assert eventually(tree.gone)
        assert _win32.job_active_processes(tree._job) == 0
        assert eventually(lambda: all_gone(pids.values()))


@windows_only
def test_windows_a_breakaway_attempt_stays_in_the_job(runtime) -> None:
    """The job allows no breakaway: CREATE_BREAKAWAY_FROM_JOB is refused and every child is in the job."""
    script, out = runtime
    with OwnedTree() as tree:
        tree.start([sys.executable, str(script), 'breakaway', str(out)], **QUIET)
        pids = recorded(out, ('root', 'breakaway.json'))
        outcome = json.loads((out / 'breakaway.json').read_text())
        if outcome['breakaway'] == 'escaped':
            os.kill(outcome['pid'], signal.SIGTERM)
        assert outcome == {'breakaway': 'refused', 'winerror': _win32.ERROR_ACCESS_DENIED}
        child = recorded(out, ('child-1',))['child-1']
        assert tree.contains(child)
        tree.kill()
        assert eventually(tree.gone)
        assert eventually(lambda: all_gone([pids['root'], child]))


@windows_only
def test_windows_the_kill_on_close_tree_ends_when_its_owner_exits(runtime, tmp_path) -> None:
    """The DSH service's lifetime: the owner exits without closing, and the kernel ends the tree."""
    script, out = runtime
    owner = tmp_path / 'owner.py'
    owner.write_text(OWNER)
    started = subprocess.run([sys.executable, str(owner), str(script), str(out), 'kill-on-close'],
                             capture_output=True, text=True, timeout=120)
    assert started.returncode == 0, started.stderr
    pids = recorded(out, ('root', 'child-1', 'child-2'), seconds=1)
    assert eventually(lambda: all_gone(pids.values()))
    with pytest.raises(OSError):
        OwnedTree.open(name=json.loads(started.stdout)['name'])


@windows_only
def test_windows_job_names_are_not_shared_and_a_missing_job_is_an_error() -> None:
    """A job name already in use is refused, so no other process can pre-create an owner's job."""
    with OwnedTree() as first:
        with pytest.raises(FileExistsError):
            OwnedTree(marker=first.name[len('graftling-'):])
        assert first.gone() and first.pids() == []
    with pytest.raises(OSError):
        OwnedTree.open(name='graftling-no-such-job-635')
    with pytest.raises(ValueError):
        OwnedTree.open(pid=os.getpid())


# -- The Windows branch with a stand-in job API (any host) ----------------------------------------


class StandInJobs:
    """Named jobs in memory, with the call shapes of ``graftling._win32``'s job functions."""

    def __init__(self) -> None:
        self.jobs: dict[str, set[int]] = {}
        self.handles: dict[int, str] = {}
        self.shared: dict[int, bool] = {}
        self.terminated: list[str] = []

    def _handle(self, name: str) -> int:
        handle = 1000 + len(self.handles) + 1
        self.handles[handle] = name
        return handle

    def create_job(self, name: str, kill_on_close: bool) -> int:
        if name in self.jobs:
            raise FileExistsError(_win32.ERROR_ALREADY_EXISTS, name)
        self.jobs[name] = set()
        return self._handle(name)

    def open_job(self, name: str) -> int:
        if name not in self.jobs:
            raise FileNotFoundError(_win32.ERROR_FILE_NOT_FOUND, name)
        return self._handle(name)

    def adopt_suspended(self, job: int, pid: int, share: bool) -> None:
        self.jobs[self.handles[job]].add(pid)
        self.shared[pid] = share

    def terminate_job(self, job: int, exit_code: int = 1) -> None:
        self.terminated.append(self.handles[job])
        self.jobs[self.handles[job]].clear()

    def job_active_processes(self, job: int) -> int:
        return len(self.jobs[self.handles[job]])

    def job_process_ids(self, job: int) -> list[int]:
        return sorted(self.jobs[self.handles[job]])

    def process_in_job(self, pid: int, job: int) -> bool:
        return pid in self.jobs[self.handles[job]]

    def close_handle(self, handle: int) -> None:
        del self.handles[handle]


def test_the_windows_branch_with_a_stand_in_job_api(monkeypatch) -> None:
    """OwnedTree's job calls, the recorded fact and control's probes by job name, without Win32."""
    jobs = StandInJobs()
    monkeypatch.setattr('graftling.process_tree.windows', lambda: True)
    for name in ('create_job', 'open_job', 'adopt_suspended', 'terminate_job', 'job_active_processes',
                 'job_process_ids', 'process_in_job', 'close_handle'):
        monkeypatch.setattr(_win32, name, getattr(jobs, name))
    me = os.getpid()

    tree = OwnedTree()
    assert tree.name.startswith('graftling-') and len(tree.name) == len('graftling-') + 32
    flags = tree.popen_options()['creationflags']
    assert flags == _win32.CREATE_SUSPENDED | _win32.CREATE_NO_WINDOW
    tree.adopt(me)
    assert jobs.shared[me] is True and tree.pid == me
    assert tree_facts(me) == {'runtime_job': tree.name}
    assert not tree.gone() and tree.pids() == [me] and tree.contains(me) and not tree.contains(me + 1)
    facts = {'runtime_pid': me, 'runtime_job': tree.name}
    assert control._group_alive(facts) and control._runtime_alive(facts)

    other = OwnedTree()
    # A live PID outside the recorded job is a reused PID, not the Runtime.
    assert not control._runtime_alive({'runtime_pid': me, 'runtime_job': other.name})
    # No job under the name: the recorded Runtime itself decides.
    assert control._runtime_alive({'runtime_pid': me, 'runtime_job': 'graftling-gone'})
    assert control._group_alive({'runtime_pid': me, 'runtime_job': 'graftling-gone'})
    assert control._group_alive({'runtime_pid': me})

    tree.signal(INTERRUPT)
    assert jobs.terminated == [tree.name] and tree.gone()
    with pytest.raises(ProcessLookupError):
        tree.signal(KILL)
    assert not control._group_alive(facts)
    with OwnedTree.open(name=tree.name) as opened:
        assert opened.gone() and opened.name == tree.name
    with pytest.raises(OSError):
        OwnedTree.open(name='graftling-gone')
    with pytest.raises(FileExistsError):
        OwnedTree(marker=tree.name[len('graftling-'):])

    tree.close()
    other.close()
    assert tree_facts(me) == {} and jobs.handles == {}
    service = OwnedTree(kill_on_close=True)
    service.adopt(me)
    assert jobs.shared[me] is False
    service.close()


def test_the_codex_service_is_killed_when_its_tree_cannot_adopt_it(tmp_path: Path, monkeypatch) -> None:
    """codex/app_server.py: a failed adopt kills the started service before waiting for it, so the
    start fails at once instead of waiting on a suspended (Windows) or running (POSIX) process."""
    import asyncio

    from graftling.codex.app_server import CodexAppServer
    from graftling.codex.context import CodexAdapterError

    def refuse(self, pid: int) -> None:
        raise OSError('stand-in: the process cannot be opened')

    monkeypatch.setattr(OwnedTree, 'adopt', refuse)
    service = CodexAppServer(cwd=tmp_path, command=[sys.executable, '-c', 'import time; time.sleep(60)'],
                             environment={'CODEX_HOME': str(tmp_path / 'codex-home')})
    started = time.monotonic()
    with pytest.raises(CodexAdapterError) as failed:
        asyncio.run(asyncio.wait_for(service.__aenter__(), 30))
    assert failed.value.code == 'RUNTIME_START_FAILED' and 'cannot be opened' in str(failed.value)
    assert time.monotonic() - started < 20
