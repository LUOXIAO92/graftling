"""Owned process trees: a Runtime and every process it starts (629 advice 3.3, 635).

One type, :class:`OwnedTree`, with two implementations.

POSIX: the tree is a process group, as before. :meth:`OwnedTree.start` puts the
process in a new session (``start_new_session``), :meth:`OwnedTree.signal` is
``os.killpg``, the forced end is ``SIGKILL``, and :meth:`OwnedTree.gone` is
``killpg(pid, 0)`` answering ESRCH. A tool that calls ``setsid`` leaves the
group; ``tool_descendants`` exists for that.

Windows: the tree is a named Job Object, ``graftling-<random marker>``, with no
limit that allows breakaway. The process is created suspended and without a
console window (``CREATE_SUSPENDED | CREATE_NO_WINDOW``), assigned to the job,
then resumed, so no descendant can exist outside the job. There is no
counterpart to SIGINT or SIGTERM: console control events need a shared
console, which a host under a desktop app does not have. Each site's native
stop (closing stdin, Pi's interrupt, Codex's connection close) therefore comes
first, and every :meth:`OwnedTree.signal` step terminates the job. "Gone" is
the job's active process count reaching zero.

Lifetime (decision 3 of the Windows support spec): a Runtime outlives its
owner, as on POSIX. Its job has no kill-on-close limit, and the Runtime holds a
query-only handle to it, so a later host can open the job by its recorded name
(:meth:`OwnedTree.open`, the ``runtime_job`` process fact) to probe or stop it.
The DSH service is the exception: its job ends with its owner
(``kill_on_close``), which replaces ``dsh/watcher.py`` on Windows.

Escapes that remain on Windows: processes started on the tree's behalf by a
system service (Task Scheduler, WMI, COM activation) are not in the job. POSIX
has the same class (cron, launchd).
"""
from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
from typing import Any

from graftling import _win32

# Stop steps, mildest first; POSIX maps them to signals, Windows terminates the job at each.
INTERRUPT = "interrupt"
TERMINATE = "terminate"
KILL = "kill"
_POSIX_SIGNALS = {INTERRUPT: "SIGINT", TERMINATE: "SIGTERM", KILL: "SIGKILL"}
JOB_PREFIX = "graftling-"
# The exit code a terminated job's processes report on Windows.
TERMINATED_EXIT_CODE = 1

# Job name of each live tree this process started, by its root PID (tree_facts).
_named: dict[int, str] = {}


def windows() -> bool:
    """Whether trees are Job Objects here (Windows) rather than process groups."""
    return sys.platform == "win32"


def job_name(marker: str) -> str:
    """Return the name of the Windows job of a tree started with ``marker`` (``graftling-<marker>``).

    A Runtime execution names its tree after its member marker (637), so the
    marker alone finds the job: :mod:`graftling.member_processes` and
    :func:`graftling.tool_descendants.stop_tool_processes` list and stop a
    marker's processes through it on Windows.
    """
    return JOB_PREFIX + marker


def tree_facts(pid: object) -> dict[str, str]:
    """Return the process fact naming the job of the tree this process started at ``pid``.

    ``{"runtime_job": name}`` on Windows for a tree still open here; ``{}`` on
    POSIX, where the group is named by ``runtime_pid`` itself, and for a PID
    this process did not start through :class:`OwnedTree`. Recording the field
    only when a job exists keeps POSIX process facts as they were.
    """
    name = _named.get(pid) if isinstance(pid, int) else None
    return {"runtime_job": name} if name else {}


class OwnedTree:
    """The process tree one owner started: a POSIX process group or a Windows job.

    Create one per start, pass :meth:`popen_options` to ``Popen`` (or use
    :meth:`start`), then :meth:`adopt` the new PID. :meth:`close` releases the
    owner's job handle; it never stops the tree.
    """

    def __init__(self, *, kill_on_close: bool = False, marker: str | None = None) -> None:
        """Prepare a tree; on Windows create its job now.

        ``kill_on_close`` (Windows only) ends the whole tree when the owner's
        handle closes, including when the owner dies. ``marker`` names the job
        ``graftling-<marker>``; by default a random one.
        """
        self.pid: int | None = None
        self.name: str | None = None
        self.kill_on_close = kill_on_close
        self._job: int | None = None
        if windows():
            self.name = JOB_PREFIX + (marker or secrets.token_hex(16))
            self._job = _win32.create_job(self.name, kill_on_close)

    @classmethod
    def open(cls, *, pid: int | None = None, name: str | None = None) -> OwnedTree:
        """Return a tree another owner started, to probe, list or stop it.

        POSIX: the group led by ``pid``. Windows: the job ``name`` (OSError when
        no such job is open anywhere); ``pid`` is kept as its root.
        """
        tree = cls.__new__(cls)
        tree.pid, tree.name, tree.kill_on_close, tree._job = pid, name, False, None
        if windows():
            if not name:
                raise ValueError("A Windows process tree is opened by its job name.")
            tree._job = _win32.open_job(name)
        elif not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("A POSIX process tree is opened by its leader's PID.")
        return tree

    def __enter__(self) -> OwnedTree:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def popen_options(self, creationflags: int = 0) -> dict[str, Any]:
        """Return the ``Popen`` options that start the tree's root.

        POSIX: a new session. Windows: suspended and without a console window,
        added to ``creationflags``; :meth:`adopt` must follow.
        """
        if not windows():
            return {"start_new_session": True}
        return {"creationflags": creationflags | _win32.CREATE_SUSPENDED | _win32.CREATE_NO_WINDOW}

    def adopt(self, pid: int) -> None:
        """Make the process just started with :meth:`popen_options` this tree's root.

        Windows: assign it to the job and resume it; on failure it is
        terminated and OSError raised.
        """
        if windows():
            assert self._job is not None, "an opened tree cannot adopt"
            _win32.adopt_suspended(self._job, pid, share=not self.kill_on_close)
            _named[pid] = self.name
        self.pid = pid

    def start(self, argv: Any, **options: Any) -> subprocess.Popen:
        """Start ``argv`` with ``options`` as this tree's root and return its ``Popen``."""
        options.update(self.popen_options(options.pop("creationflags", 0)))
        process = subprocess.Popen(argv, **options)
        try:
            self.adopt(process.pid)
        except BaseException:
            process.kill()
            process.wait()
            raise
        return process

    def signal(self, step: str) -> None:
        """Send one stop step (:data:`INTERRUPT`, :data:`TERMINATE` or :data:`KILL`) to the tree.

        POSIX: ``os.killpg`` with SIGINT, SIGTERM or SIGKILL, raising as it
        does (ProcessLookupError when the group is gone). Windows: terminate
        the job; ProcessLookupError when it has no process left.
        """
        if step not in _POSIX_SIGNALS:
            raise ValueError(f"Unknown stop step {step!r}.")
        if not windows():
            os.killpg(self.pid, getattr(signal, _POSIX_SIGNALS[step]))
            return
        if self.gone():
            raise ProcessLookupError(f"Process tree {self.name} has no process left.")
        _win32.terminate_job(self._job, TERMINATED_EXIT_CODE)

    def kill(self) -> None:
        """End the whole tree now (SIGKILL to the group, or terminate the job)."""
        self.signal(KILL)

    def gone(self) -> bool:
        """Return whether no process of the tree is left.

        POSIX: ``killpg(pid, 0)`` raised ProcessLookupError; any other error
        (such as PermissionError for a darwin group of zombies) is raised, as
        ``killpg`` raises it. Windows: the job's active process count is 0.
        """
        if windows():
            return _win32.job_active_processes(self._job) == 0
        try:
            os.killpg(self.pid, 0)
        except ProcessLookupError:
            return True
        return False

    def pids(self) -> list[int]:
        """Return the PIDs in the tree now (the job's list, or the group's members)."""
        if windows():
            return _win32.job_process_ids(self._job)
        from graftling.member_processes import _user_pids

        members = []
        for pid in _user_pids():
            try:
                if self.contains(pid):
                    members.append(pid)
            except OSError:
                continue  # Not readable here, so not shown as a member.
        return members

    def terminate(self, pids: list[int] | None = None) -> None:
        """Windows only: end the whole job, or only ``pids`` while each is still in it (637).

        ``None`` terminates the job (TerminateJobObject), which also ends a
        process started in it meanwhile. A list ends each process through one
        handle that first checks it is in the job, so a reused PID outside the
        job is never touched; the rest of the job keeps running.
        """
        assert windows() and self._job is not None, "terminate is the Windows job's stop"
        if pids is None:
            _win32.terminate_job(self._job, TERMINATED_EXIT_CODE)
            return
        for pid in pids:
            _win32.terminate_in_job(pid, self._job, TERMINATED_EXIT_CODE)

    def contains(self, pid: int) -> bool:
        """Return whether ``pid`` is in the tree (IsProcessInJob, or the same process group).

        A PID with no process is not. Windows raises OSError when the process
        exists but cannot be opened, and when this tree's job handle is closed:
        IsProcessInJob with no job would answer for any job (636).
        """
        if windows():
            if self._job is None:
                raise OSError("This tree's job handle is closed; membership cannot be read.")
            try:
                return _win32.process_in_job(pid, self._job)
            except OSError as error:
                if getattr(error, "winerror", None) == _win32.ERROR_INVALID_PARAMETER:
                    return False
                raise
        try:
            return os.getpgid(pid) == self.pid
        except ProcessLookupError:
            return False

    def close(self) -> None:
        """Release this owner's job handle (Windows); the tree itself is not stopped.

        With ``kill_on_close`` this ends the tree once no other handle is open.
        """
        if self.pid is not None and _named.get(self.pid) == self.name:
            _named.pop(self.pid, None)
        if self._job is not None:
            job, self._job = self._job, None
            _win32.close_handle(job)
