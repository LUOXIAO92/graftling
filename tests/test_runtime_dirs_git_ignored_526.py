"""graftling's own runtime directories ignore themselves in a git work tree (526).

DSH lists a turn's changed files by snapshotting the work tree through a
private index with ``git add --all``; untracked files count, ignored ones do
not. A data root inside a user's repository must not show graftling's
archives and records there, while the user's own new files still do.
"""
import os
from pathlib import Path
import subprocess

from graftling import AgentControl, Caller
from graftling.sessions.archive import JsonlSessionStore, archive_path

from test_agent_control import ScriptedDriver, call

HEADER = {'version': 5, 'id': 'dsh:session-526', 'createdAt': 100, 'isSeeded': False}


def test_archive_write_leaves_a_self_ignoring_sessions_directory(tmp_path: Path) -> None:
    """Only ``<root>/sessions`` gains ``*``; the caller's root is never written."""
    root = tmp_path / 'root'
    path = archive_path(root, HEADER['id'])
    JsonlSessionStore.create(path, HEADER)
    assert path.is_file() and JsonlSessionStore(path).header == HEADER
    assert (root / 'sessions' / '.gitignore').read_text() == '*\n'
    assert not (root / '.gitignore').exists()


def test_an_existing_gitignore_is_kept(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    (root / 'sessions').mkdir(parents=True)
    (root / 'sessions' / '.gitignore').write_text('# mine\n')
    JsonlSessionStore.create(archive_path(root, HEADER['id']), HEADER)
    assert (root / 'sessions' / '.gitignore').read_text() == '# mine\n'


def test_failed_ignore_write_does_not_fail_the_archive(tmp_path: Path) -> None:
    """A ``.gitignore`` that cannot be created never weakens the archive."""
    root = tmp_path / 'root'
    (root / 'sessions' / '.gitignore').mkdir(parents=True)  # a directory: open('x') raises
    path = archive_path(root, HEADER['id'])
    JsonlSessionStore.create(path, HEADER)
    assert JsonlSessionStore(path).header == HEADER


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], check=True, text=True,
                          capture_output=True, env=env).stdout


def test_git_lists_user_files_but_no_graftling_runtime_files(tmp_path: Path) -> None:
    repo = tmp_path / 'project'
    repo.mkdir()
    git(repo, 'init', '-q')
    root = repo / '.graphtraj' / 'swarm'
    JsonlSessionStore.create(archive_path(root, HEADER['id']), HEADER)
    driver = ScriptedDriver()
    control = AgentControl(root, {'scripted': driver})
    created, failed = call(control, Caller('test-host', None, session='host'), action='create',
                           runtime='scripted', instruction='hi', name='helper')
    assert not failed
    driver.release(created['execution'])
    for name in ('control', 'live', 'native', 'sessions'):
        assert (root / name / '.gitignore').read_text() == '*\n'
    (repo / 'user-edit.txt').write_text('mine\n')

    # As DSH does: a private index, add everything, list it.
    env = {**os.environ, 'GIT_INDEX_FILE': str(tmp_path / 'private-index')}
    git(repo, 'add', '--all', env=env)
    listed = git(repo, 'ls-files', env=env).split()
    status = git(repo, 'status', '--porcelain', '--untracked-files=all').splitlines()
    assert listed == ['user-edit.txt']
    assert status == ['?? user-edit.txt']
