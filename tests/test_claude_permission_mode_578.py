"""578: a Claude child whose system/init reports another permission mode than requested.

A narrower mode (Claude asks or refuses more) is recorded and warned, and the run completes; a wider, not
comparable or unknown mode still fails the run, which is interrupted at once. The real ClaudeDriver and
ClaudeExecution run the controlled CLI peer (claude_cli_fixture.py); CLAUDE_FIXTURE_PERMISSION_MODE sets the mode
its init reports.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import graftling
from graftling import AgentControl, Caller
from graftling.claude.driver import ClaudeDriver
from graftling.claude.execution import ClaudeExecution
from test_claude_review import native  # noqa: F401  (fixture)


def evidence(requested: str | None, observed: object) -> dict:
    execution = object.__new__(ClaudeExecution)
    execution.request = {'permission_mode': requested, 'permission_mode_source': 'role'}
    execution.native_init = {'permissionMode': observed}
    return execution.permission_evidence()


@pytest.mark.parametrize(('requested', 'observed'), [
    ('auto', 'default'), ('auto', 'manual'), ('auto', 'plan'), ('auto', 'dontAsk'),
    ('default', 'plan'), ('default', 'dontAsk')])
def test_narrower_modes(requested: str, observed: str) -> None:
    found = evidence(requested, observed)
    assert (found['requested'], found['observed'], found['matches_request'], found['narrower']) == (
        requested, observed, False, True)


@pytest.mark.parametrize(('requested', 'observed'), [
    ('default', 'auto'), ('default', 'acceptEdits'), ('default', 'bypassPermissions'),
    ('auto', 'acceptEdits'), ('auto', 'bypassPermissions')])
def test_wider_or_not_comparable_modes(requested: str, observed: str) -> None:
    found = evidence(requested, observed)
    assert (found['matches_request'], found['narrower']) == (False, False)


@pytest.mark.parametrize(('requested', 'observed', 'matches'), [
    ('auto', 'auto', True), ('default', 'manual', True), ('auto', 'unexpected', None), ('auto', None, None)])
def test_matching_or_unknown_modes_carry_no_narrower(requested: str, observed: object, matches) -> None:
    found = evidence(requested, observed)
    assert found['matches_request'] is matches and 'narrower' not in found


def run_child(root: Path, monkeypatch: pytest.MonkeyPatch, requested: str, observed: str) -> tuple[dict, list[str]]:
    """One child under ``requested`` whose init reports ``observed``; its wait result and control subtypes sent."""
    monkeypatch.setenv('CLAUDE_FIXTURE_PERMISSION_MODE', observed)
    sent: list[str] = []
    original = ClaudeExecution._control

    def recording(self, subtype: str, **fields: object) -> str:
        sent.append(subtype)
        return original(self, subtype, **fields)

    monkeypatch.setattr(ClaudeExecution, '_control', recording)
    control = AgentControl(root / 'agents', {'claude': ClaudeDriver(root, {
        'model': 'claude-opus-5-5', 'permission_mode': requested})})
    call = graftling.bind(control, Caller('test-host', None, 'parent-session', 'claude'))
    created = call({'action': 'create', 'runtime': 'claude', 'instruction': 'first'})
    assert not created.failed, created.document
    agent = created.document['result']['agent']
    waited = call({'action': 'wait', 'agent': agent, 'timeout_seconds': 20})
    assert not waited.failed, waited.document
    return waited.document['result'], sent


def records(root: Path) -> tuple[dict, dict]:
    session = yaml.safe_load(next((root / 'agents').rglob('session.yml')).read_text())
    diagnostics = yaml.safe_load(next((root / 'agents').rglob('claude-diagnostics-*.yml')).read_text())
    return session, diagnostics


def test_a_narrower_mode_is_recorded_warned_and_the_run_completes(
        tmp_path: Path, native: None, monkeypatch: pytest.MonkeyPatch) -> None:
    result, sent = run_child(tmp_path, monkeypatch, 'auto', 'default')
    assert result['last_result']['outcome'] == 'completed', result
    assert 'first' in result['last_result']['last_message']
    assert result['permission_mode'] == {'requested': 'auto', 'observed': 'default', 'narrower': True}
    session, diagnostics = records(tmp_path)
    assert session['permissions']['requested'] == 'auto' and session['permissions']['observed'] == 'default'
    assert session['permissions']['matches_request'] is False and session['permissions']['narrower'] is True
    assert {'source': 'system_init', 'category': 'permission_mode_narrower'} in diagnostics['warnings']
    assert diagnostics['permission_mode']['narrower'] is True
    assert 'interrupt' not in sent


def test_a_matching_mode_reports_no_permission_mode(
        tmp_path: Path, native: None, monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = run_child(tmp_path, monkeypatch, 'auto', 'auto')
    assert result['last_result']['outcome'] == 'completed' and 'permission_mode' not in result
    _, diagnostics = records(tmp_path)
    assert not any(w['category'] == 'permission_mode_narrower' for w in diagnostics['warnings'])


@pytest.mark.parametrize(('requested', 'observed'), [
    ('default', 'auto'), ('auto', 'acceptEdits'), ('auto', 'bypassPermissions'), ('auto', '')])
def test_a_wider_not_comparable_or_unknown_mode_fails_and_interrupts(
        tmp_path: Path, native: None, monkeypatch: pytest.MonkeyPatch, requested: str, observed: str) -> None:
    result, sent = run_child(tmp_path, monkeypatch, requested, observed)
    assert result['last_result']['outcome'] == 'failed', result
    assert result['last_result']['error'] == {
        'code': 'RUNTIME_REQUEST_FAILED', 'message': 'Claude did not confirm the requested permission mode.'}
    assert 'permission_mode' not in result
    assert sent.count('interrupt') == 1
    session, _ = records(tmp_path)
    assert session['permissions']['matches_request'] is (None if observed == '' else False)
