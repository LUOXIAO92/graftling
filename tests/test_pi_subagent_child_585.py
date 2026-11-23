"""A Pi subagent child starts no graftling host; a Pi Main is unchanged (585).

A child is a Session with PI_SUBAGENT_CHILD=1 (nicobailon/pi-subagents),
PI_SUBAGENT_DEPTH > 0 (mjakl/pi-subagent) or no persistent Session file (Pi's
bundled subagent example runs ``pi --no-session``). Driven through the real
extension's ``registerAgents().start``, as Pi's session_start does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_pi_default_host_572 import installed, start

MAIN = '/sessions/pi-main.jsonl'
CHILDREN = {
    'nicobailon: CHILD=1 with its own Session file': {'PI_SUBAGENT_CHILD': '1', 'TEST_SESSION_FILE': MAIN},
    'mjakl: DEPTH=1, no Session file': {'PI_SUBAGENT_DEPTH': '1', 'TEST_SESSION_FILE': ''},
    'DEPTH=2 with a Session file': {'PI_SUBAGENT_DEPTH': '2', 'TEST_SESSION_FILE': MAIN},
    'bundled example: no marker, no Session file': {'TEST_SESSION_FILE': ''},
}
MODES = {'default': {}, '--agents-config': {'TEST_CONFIG': '/conf/pi-host.json'}, 'GRAFTLING_DATA_ROOT': 'DATA'}


def mode_environment(tmp_path: Path, mode: str) -> dict[str, str]:
    """The Session's own way to choose a data root; DATA is a root that does not exist yet."""
    chosen = MODES[mode]
    return {'GRAFTLING_DATA_ROOT': str(tmp_path / 'data')} if chosen == 'DATA' else dict(chosen)


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('child', CHILDREN)
def test_a_subagent_child_starts_no_host_creates_nothing_and_reports_nothing(
        tmp_path: Path, child: str, mode: str) -> None:
    """No host, no agents tool, no error; neither <project>/.agents-control nor the variable's root appears."""
    extension, _ = installed(tmp_path)
    found = start(tmp_path, extension, **CHILDREN[child], **mode_environment(tmp_path, mode))
    assert found == ({'started': False, 'servers': []}, [])
    assert not (tmp_path / 'project' / '.agents-control').exists() and not (tmp_path / 'data').exists()


def test_a_child_is_recognised_before_the_python_and_project_checks(tmp_path: Path) -> None:
    """A child with neither a usable cwd nor a graftling Python is still silent: the test comes first."""
    extension, _ = installed(tmp_path, with_python=False)
    assert start(tmp_path, extension, TEST_SESSION_FILE='', TEST_CWD='relative/x') == (
        {'started': False, 'servers': []}, [])


@pytest.mark.parametrize('markers', [{}, {'PI_SUBAGENT_DEPTH': '0'}, {'PI_SUBAGENT_DEPTH': ''},
                                     {'PI_SUBAGENT_CHILD': ''}, {'PI_SUBAGENT_CHILD': '0'}],
                         ids=['unset', 'DEPTH=0', 'DEPTH=empty', 'CHILD=empty', 'CHILD=0'])
@pytest.mark.parametrize('mode', MODES)
def test_a_main_with_a_session_file_still_starts_its_host(tmp_path: Path, markers: dict, mode: str) -> None:
    """572 default, --agents-config and GRAFTLING_DATA_ROOT (577) are unchanged for a Main."""
    extension, python = installed(tmp_path)
    found, launches = start(tmp_path, extension, TEST_SESSION_FILE=MAIN, **markers, **mode_environment(tmp_path, mode))
    assert found == {'started': True, 'servers': [{'name': 'graftling', 'command': str(python)}]}
    config = ['--config', '/conf/pi-host.json'] if mode == '--agents-config' else []
    assert [launch['argv'] for launch in launches] == [['-I', '-m', 'graftling.pi.host', *config]]


@pytest.mark.parametrize('session_file', [MAIN, ''])
def test_graftlings_own_child_keeps_its_owner_connection(tmp_path: Path, session_file: str) -> None:
    """GRAFTLING_CONNECTION is tested first, as before: MCP through the owner, no host, with or without a file."""
    extension, _ = installed(tmp_path)
    found, launches = start(tmp_path, extension, TEST_SESSION_FILE=session_file, PI_SUBAGENT_DEPTH='1',
                            GRAFTLING_CONNECTION='/owner/address', GRAFTLING_PYTHON='/owner/python')
    assert found == {'started': True, 'servers': [{'name': 'graftling', 'command': '/owner/python'}]}
    assert launches == []


@pytest.mark.parametrize('session_file', [MAIN, ''])
def test_a_managed_dsh_context_is_unchanged(tmp_path: Path, session_file: str) -> None:
    """Inert without the flag; with the flag still refused, child or not."""
    extension, _ = installed(tmp_path)
    assert start(tmp_path, extension, TEST_SESSION_FILE=session_file, DSH_SESSION_ID='x') == (
        {'started': False, 'servers': []}, [])
    found, launches = start(tmp_path, extension, TEST_SESSION_FILE=session_file, DSH_SESSION_ID='x',
                            TEST_CONFIG='/conf/pi-host.json')
    assert 'cannot replace an existing managed Agent context' in found['error'] and launches == []
