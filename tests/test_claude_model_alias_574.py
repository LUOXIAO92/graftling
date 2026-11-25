"""A Claude child requested by catalog alias or by the alias's full id (574).

Claude's initialize catalog lists aliases (``haiku``) with the full id each
resolves to (``resolvedModel``: ``claude-haiku-5-5``), and a Session reports
that full id at start. Before 574 the alias passed the candidate check and then
failed the init check; the full id failed the candidate check. Native discovery
is faked; nothing here starts a Runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

import graftling
import graftling.control
import graftling.models
from graftling import AgentControl, Caller
from graftling.claude.driver import ClaudeDriver
from graftling.claude.execution import alias_resolution
from posix_only import SH_SCRIPT, require_posix

CALLER = Caller('test-host', None, 'main-session', 'claude')
CATALOG = [  # the shape models._claude returns for Claude 2.1.330's catalog (Task 574 repro)
    {'id': 'default', 'alias': 'Default (recommended)', 'provider': 'anthropic', 'supported_efforts': None,
     'resolved_model': 'claude-opus-5-5'},
    {'id': 'opus', 'alias': 'Opus 5.5', 'provider': 'anthropic', 'supported_efforts': None,
     'resolved_model': 'claude-opus-5-5'},
    {'id': 'haiku', 'alias': 'Haiku 5.5', 'provider': 'anthropic', 'supported_efforts': ['low', 'high'],
     'resolved_model': 'claude-haiku-5-5'},
    {'id': 'claude-haiku-4-5-20251001', 'alias': 'Haiku 4.5', 'provider': 'anthropic', 'supported_efforts': None,
     'resolved_model': 'claude-haiku-4-5-20251001'},
    {'id': 'sonnet', 'alias': 'Sonnet', 'provider': 'anthropic', 'supported_efforts': None},  # no resolvedModel
]


class Recording:
    """Finishes at once, reporting ``resolved`` as the started model, and keeps each selection."""

    def __init__(self, resolved: str | None = None) -> None:
        self.selections: list[dict] = []
        self.resolved = resolved

    def prepare(self, runtime, caller, *, configuration=None, selection=None):
        self.selections.append(selection)
        return {'runtime': runtime}

    def start(self, run):
        run.events.session('native-' + run.agent[-6:])
        if self.resolved:
            run.events.process(resolved_model=self.resolved)
        run.events.finished('completed', last_message='pong')

    def deliver(self, run, message):
        raise AssertionError('not used')

    def interrupt(self, run):
        return False

    def requests(self, run):
        return []


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graftling.control, '_discover_models', lambda connection: (
        graftling.discover_models(connection) if connection.get('models') is not None else
        {'runtime': connection['runtime'], 'status': 'available', 'message': 'fake', 'models': CATALOG}))


def create(control: AgentControl, **arguments) -> tuple[dict, bool]:
    result = graftling.bind(control, CALLER)({'action': 'create', 'instruction': 'Work.', **arguments})
    return result.document, result.failed


def status(control: AgentControl, document: dict) -> dict:
    return graftling.bind(control, CALLER)({'action': 'status', 'agent': document['result']['agent']}).document['result']


def test_an_alias_carries_its_catalog_resolution_and_status_shows_both(tmp_path: Path, native) -> None:
    driver = Recording('claude-haiku-5-5')
    control = AgentControl(tmp_path, {'claude': driver})
    document, failed = create(control, runtime='claude', model='haiku', reasoning_effort='high')
    assert not failed
    assert (driver.selections[0]['model'], driver.selections[0]['expected_model']) == ('haiku', 'claude-haiku-5-5')
    shown = status(control, document)
    assert (shown['model'], shown['resolved_model']) == ('haiku', 'claude-haiku-5-5')


def test_a_full_id_some_entry_resolves_to_is_a_candidate(tmp_path: Path, native) -> None:
    driver = Recording()
    control = AgentControl(tmp_path, {'claude': driver})
    document, failed = create(control, runtime='claude', model='claude-haiku-5-5', reasoning_effort='low')
    assert not failed
    assert driver.selections[0]['model'] == 'claude-haiku-5-5' and 'expected_model' not in driver.selections[0]
    shown = status(control, document)
    assert shown['model'] == 'claude-haiku-5-5' and 'resolved_model' not in shown
    # Two aliases resolving to one id (default, opus) share one provider: still one candidate.
    _, failed = create(control, runtime='claude', model='claude-opus-5-5')
    assert not failed


def test_a_full_id_entry_needs_no_expected_model(tmp_path: Path, native) -> None:
    driver = Recording()
    _, failed = create(AgentControl(tmp_path, {'claude': driver}), runtime='claude', model='claude-haiku-4-5-20251001')
    assert not failed and 'expected_model' not in driver.selections[0]


@pytest.mark.parametrize('model', ['claude-haiku-9-9', 'claude-sonnet-5-5', 'claude-haiku'])
def test_ids_no_entry_names_or_resolves_to_are_refused(tmp_path: Path, native, model: str) -> None:
    driver = Recording()
    document, failed = create(AgentControl(tmp_path, {'claude': driver}), runtime='claude', model=model)
    assert failed and document['error']['code'] == 'invalid-model'
    assert 'resolves a listed entry' in document['error']['message'] and driver.selections == []


def test_an_alias_without_resolved_model_keeps_exact_behaviour(tmp_path: Path, native) -> None:
    driver = Recording()
    _, failed = create(AgentControl(tmp_path, {'claude': driver}), runtime='claude', model='sonnet')
    assert not failed and 'expected_model' not in driver.selections[0]


def test_declared_models_are_matched_exactly(tmp_path: Path, native) -> None:
    (tmp_path / 'config.yml').write_text('connections:\n  c:\n    runtime: claude\n    models: [haiku]\n')
    driver = Recording()
    control = AgentControl(tmp_path, {'claude': driver})
    _, failed = create(control, runtime='claude', model='haiku')
    assert not failed and 'expected_model' not in driver.selections[0]
    document, failed = create(control, runtime='claude', model='claude-haiku-5-5')
    assert failed and document['error']['code'] == 'invalid-model'
    assert 'resolves a listed entry' not in document['error']['message']


def test_the_init_check_accepts_only_the_catalog_resolution() -> None:
    alias = {'model': 'haiku', 'expected_model': 'claude-haiku-5-5'}
    assert alias_resolution(alias, 'claude-haiku-5-5') == 'claude-haiku-5-5'
    assert alias_resolution(alias, 'claude-haiku-4-5-20251001') is None       # another haiku: still a change
    assert alias_resolution(alias, 'claude-sonnet-5-5') is None               # cross-family
    assert alias_resolution(alias, 'haiku') is None                           # the same id needs no resolution
    assert alias_resolution({'model': 'haiku'}, 'claude-haiku-5-5') is None    # declared: exact
    assert alias_resolution({'model': 'claude-haiku-5-5'}, 'claude-haiku-4-5') is None
    assert alias_resolution({'model': 'default', 'expected_model': 'claude-opus-5-5'},
                            'claude-opus-5-5') == 'claude-opus-5-5'


def test_the_claude_driver_passes_the_expected_model_to_its_request(tmp_path: Path) -> None:
    require_posix(SH_SCRIPT)
    claude = tmp_path / 'claude'
    claude.write_text('#!/bin/sh\necho "2.1.330 (Claude Code)"\n')
    claude.chmod(0o755)
    driver = ClaudeDriver(tmp_path, {'executable': str(claude)})
    marked = driver.prepare('claude', CALLER, selection={'settings': None, 'model': 'haiku',
                                                         'reasoning_effort': None, 'expected_model': 'claude-haiku-5-5'})
    plain = driver.prepare('claude', CALLER, selection={'settings': None, 'model': 'claude-haiku-5-5',
                                                        'reasoning_effort': None})
    assert (marked['model'], marked['expected_model']) == ('haiku', 'claude-haiku-5-5')
    assert plain['model'] == 'claude-haiku-5-5' and 'expected_model' not in plain


def test_the_native_catalog_keeps_resolved_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {'type': 'control_response', 'response': {'request_id': 'catalog', 'subtype': 'success', 'response': {
        'models': [{'value': 'haiku', 'displayName': 'Haiku 5.5', 'resolvedModel': 'claude-haiku-5-5'},
                   {'value': 'sonnet'}]}}}
    monkeypatch.setattr(graftling.models, '_executable', lambda name: name)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(reply), ''))
    models = graftling.models._claude({'runtime': 'claude'}, tmp_path)['models']
    assert [(m['id'], m.get('resolved_model')) for m in models] == [('haiku', 'claude-haiku-5-5'), ('sonnet', None)]
