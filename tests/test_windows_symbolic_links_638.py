"""A Runtime that needs symbolic links refuses on Windows without Developer Mode (638, decision 4).

Every Runtime's Trace entry is a symbolic link, and Pi's agent directory links
the native login. Windows lets an account create one only with Developer Mode
(or administrator rights). The hosted Windows runner is an administrator, so
the missing privilege is simulated: graftling.links sees Windows, and creating
a link fails as Windows fails it (ERROR_PRIVILEGE_NOT_HELD, 1314).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import graftling.links
from graftling.claude.execution import ClaudeExecution
from graftling.codex.managed_session import CodexManagedExecution
from graftling.dsh.execution import DshExecution
from graftling.pi.execution import PiExecution
from graftling.runtime import RuntimeAdapterError


@pytest.fixture
def without_developer_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows, where this account may not create a symbolic link."""
    def refused(*arguments: object, **options: object) -> None:
        raise OSError(1, 'A required privilege is not held by the client', None, 1314)

    monkeypatch.setattr(graftling.links, 'os', SimpleNamespace(name='nt', symlink=refused))


def executions(tmp_path: Path) -> list[tuple[str, object]]:
    """One execution of each Runtime, as a driver creates it; none has run."""
    request = {'worktree_path': str(tmp_path), 'runtime_home': str(tmp_path / 'home')}
    unused  = lambda *_: None  # noqa: E731

    return [
        ('Claude Code', ClaudeExecution(request, 'x', tmp_path, unused, unused, tmp_path / 'claude.jsonl', None)),
        ('Pi', PiExecution(request, 'x', tmp_path, unused, unused, tmp_path / 'pi.jsonl', None)),
        ('DSH', DshExecution(request, 'x', tmp_path, unused, unused, tmp_path / 'dsh.jsonl', None)),
        # The refusal is the first thing run() does, so no Codex context is needed to reach it.
        ('Codex', object.__new__(CodexManagedExecution)),
    ]


def test_each_runtime_refuses_before_it_starts(tmp_path: Path, without_developer_mode: None) -> None:
    """The refusal names the Runtime and Developer Mode; nothing is started, linked or copied."""
    for runtime, execution in executions(tmp_path):
        with pytest.raises(RuntimeAdapterError) as refused:
            execution.run()
        assert refused.value.code == 'runtime-unavailable'
        assert refused.value.message.startswith(f'{runtime} needs symbolic links')
        assert 'Windows Developer Mode' in refused.value.message
    # No stderr log, Trace entry or private agent directory: refused before any of them.
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_posix_always_creates_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX needs no privilege, so nothing is probed and nothing is refused."""
    monkeypatch.setattr(graftling.links, 'os', SimpleNamespace(name='posix', symlink=None))
    graftling.links.require_symbolic_links('Pi')
    assert graftling.links.symbolic_links_available()


def test_windows_with_developer_mode_is_not_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where the probe link can be created, the Runtime is not refused."""
    monkeypatch.setattr(graftling.links, 'os', SimpleNamespace(name='nt', symlink=os.symlink))
    graftling.links.require_symbolic_links('Codex')
