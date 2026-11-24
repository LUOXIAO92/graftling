"""Models by provider, the read-only ``models`` feature and ``create``'s ``provider`` (609).

Everything runs through public seams: :func:`graftling.discover_models`, the
bound ``subagents`` function over :class:`AgentControl` and the feature
discovery. The native clients are small fakes on ``PATH`` (Pi, Claude) or
stand-ins for the service classes discovery opens (Codex, DSH); no Runtime and
no Agent is started.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

import graftling
import graftling.codex.app_server
import graftling.dsh.service
from graftling import AgentControl, Caller, discover_models
from graftling.discovery import handle_request


CALLER = Caller('test-host', None, 'main-session', 'claude')
SECRET = 'sk-test-value-609'

# Pi's catalog spans providers; one id is offered by both.
PI_MODELS = [
    {'id': 'shared-model', 'name': 'Shared', 'provider': 'deepseek'},
    {'id': 'deepseek-chat', 'provider': 'deepseek'},
    {'id': 'shared-model', 'provider': 'openrouter'},
    {'id': 'router-only', 'provider': 'openrouter'},
]

TWO_PI = """\
connections:
  ds:
    runtime: pi
    home: {home}
    provider: deepseek
    api_key_env: DEEPSEEK_TEST_KEY
  or:
    runtime: pi
    home: {home}
    provider: openrouter
    api_key_env: OPENROUTER_TEST_KEY
presets:
  cheap:
    connection: ds
    model: deepseek-chat
"""


class RecordingDriver:
    """Finishes at once and keeps every ``prepare`` call's selection."""

    def __init__(self) -> None:
        self.prepared: list[dict] = []

    def prepare(self, runtime, caller, *, configuration=None, selection=None):
        self.prepared.append(selection)
        return {'runtime': runtime}

    def require_available(self, connection):
        pass

    def start(self, run):
        run.events.session('native-' + run.agent[-6:])
        run.events.finished('completed', last_message='done')

    def deliver(self, run, message):
        raise AssertionError('not used')

    def interrupt(self, run):
        return False

    def requests(self, run):
        return []

    def reply(self, run, request, response):
        raise AssertionError('not used')


def fake_client(directory: Path, name: str, body: str) -> None:
    """Write an executable Python script ``name`` into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f'#!{sys.executable}\nimport json, os, sys\n{body}', encoding='utf-8')
    path.chmod(0o755)


@pytest.fixture
def pi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``pi`` answering ``get_available_models``; returns the log of the ``auth.json`` each query saw."""
    log = tmp_path / 'pi-log.jsonl'
    fake_client(tmp_path / 'bin', 'pi', f"""
sys.stdin.readline()
auth = os.path.join(os.environ['PI_CODING_AGENT_DIR'], 'auth.json')
seen = json.load(open(auth)) if os.path.isfile(auth) and not os.path.islink(auth) else None
with open({str(log)!r}, 'a') as stream:
    stream.write(json.dumps({{'auth': seen}}) + '\\n')
print(json.dumps({{'id': 'models', 'type': 'response', 'success': True,
                  'data': {{'models': {PI_MODELS!r}}}}}))
""")
    monkeypatch.setenv('PATH', f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv('DEEPSEEK_TEST_KEY', SECRET)
    monkeypatch.setenv('OPENROUTER_TEST_KEY', SECRET)
    (tmp_path / 'pi-home').mkdir()
    # The native default login's home, so no listing reads the user's own.
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(tmp_path / 'pi-home'))
    return log


def configured(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'config.yml').write_text(text, encoding='utf-8')
    return root


def call(control: AgentControl, **request) -> tuple[dict, bool]:
    result = graftling.bind(control, CALLER)(request)
    return result.document, result.failed


def two_pi(tmp_path: Path) -> tuple[AgentControl, RecordingDriver]:
    driver = RecordingDriver()
    root   = configured(tmp_path / 'root', TWO_PI.format(home=tmp_path / 'pi-home'))
    return AgentControl(root, {'pi': driver}), driver


# -- discover_models: queried with the connection, grouped by provider ----------

def test_pi_catalog_is_grouped_by_provider_and_keeps_the_flat_list(tmp_path: Path, pi: Path) -> None:
    found = discover_models({'runtime': 'pi', 'home': str(tmp_path / 'pi-home')})
    assert found['status'] == 'available'
    assert [(m['provider'], m['id']) for m in found['models']] == [(m['provider'], m['id']) for m in PI_MODELS]
    assert [(g['provider'], [m['id'] for m in g['models']]) for g in found['groups']] == [
        ('deepseek', ['shared-model', 'deepseek-chat']), ('openrouter', ['shared-model', 'router-only'])]


def test_a_pi_connection_is_listed_with_its_provider_and_named_key(tmp_path: Path, pi: Path) -> None:
    found = discover_models({'runtime': 'pi', 'home': str(tmp_path / 'pi-home'),
                             'provider': 'openrouter', 'api_key_env': 'OPENROUTER_TEST_KEY'})
    assert [(g['provider'], [m['id'] for m in g['models']]) for g in found['groups']] == [
        ('openrouter', ['shared-model', 'router-only'])]
    # Pi was given only the variable's name for that provider, never its value.
    assert json.loads(pi.read_text().splitlines()[-1]) == {
        'auth': {'openrouter': {'type': 'api_key', 'key': '$OPENROUTER_TEST_KEY'}}}
    assert SECRET not in json.dumps(found)


def test_claude_lists_with_the_connection_endpoint_key_and_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / 'claude-env.json'
    reply = {'type': 'control_response', 'response': {'request_id': 'catalog', 'subtype': 'success',
                                                      'response': {'models': [{'value': 'claude-opus-5-5'}]}}}
    fake_client(tmp_path / 'bin', 'claude', f"""
sys.stdin.readline()
json.dump({{name: os.environ.get(name) for name in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY')}},
          open({str(log)!r}, 'w'))
print(json.dumps({reply!r}))
""")
    monkeypatch.setenv('PATH', f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv('GATEWAY_TEST_KEY', SECRET)
    found = discover_models({'runtime': 'claude', 'provider': 'gateway', 'base_url': 'https://gw.example/v1',
                             'api_key_env': 'GATEWAY_TEST_KEY'})
    assert [(g['provider'], [m['id'] for m in g['models']]) for g in found['groups']] == [
        ('gateway', ['claude-opus-5-5'])]
    assert json.loads(log.read_text()) == {'ANTHROPIC_BASE_URL': 'https://gw.example/v1', 'ANTHROPIC_API_KEY': SECRET}
    assert SECRET not in json.dumps(found)

    # Without a connection provider the catalog stays Anthropic's.
    assert discover_models({'runtime': 'claude'})['groups'][0]['provider'] == 'anthropic'


def test_codex_names_its_models_by_the_connection_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Server:
        def __init__(self, **options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def read_configuration(self, cwd):
            return {'model_provider': 'home-provider'}

        async def list_models(self):
            return [{'model': 'gpt-5.5', 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}]

    fake_client(tmp_path / 'bin', 'codex', '')
    monkeypatch.setenv('PATH', f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setattr(graftling.codex.app_server, 'CodexAppServer', Server)
    assert discover_models({'runtime': 'codex', 'provider': 'deepseek'})['groups'] == [
        {'provider': 'deepseek', 'models': [{'id': 'gpt-5.5', 'alias': 'gpt-5.5', 'provider': 'deepseek',
                                             'supported_efforts': ['low']}]}]
    assert discover_models({'runtime': 'codex'})['groups'][0]['provider'] == 'home-provider'


def test_dsh_catalog_groups_follow_its_provider_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Service:
        def __init__(self, executable, cwd, environment, **options):
            pass

        def start(self):
            pass

        def close(self):
            pass

        def rpc(self, method):
            return {'groups': [{'id': 'deepseek-official', 'models': [{'id': 'deepseek-v4-flash'}]},
                               {'id': 'opencode-go', 'models': [{'id': 'glm-5'}]}]}

    fake_client(tmp_path / 'bin', 'dsh', '')
    monkeypatch.setenv('PATH', f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setattr(graftling.dsh.service, 'DshService', Service)
    found = discover_models({'runtime': 'dsh'})
    assert [(g['provider'], [m['id'] for m in g['models']]) for g in found['groups']] == [
        ('deepseek-official', ['deepseek-v4-flash']), ('opencode-go', ['glm-5'])]
    assert found['provider_status'] == {'deepseek-official': 'available', 'opencode-go': 'available'}


def test_declared_models_are_grouped_by_their_providers() -> None:
    found = discover_models({'runtime': 'claude', 'provider': 'gateway',
                             'models': ['a', {'id': 'b', 'provider': 'other'}]})
    assert [(g['provider'], [m['id'] for m in g['models']]) for g in found['groups']] == [
        ('gateway', ['a']), ('other', ['b'])]


# -- the models feature ----------------------------------------------------------

def test_models_lists_each_connection_by_provider_read_only_and_key_free(tmp_path: Path, pi: Path) -> None:
    control, driver = two_pi(tmp_path)
    document, failed = call(control, action='models', runtime='pi')
    assert not failed and document['agent'] is None
    listed = document['result']
    assert listed['runtime'] == 'pi'
    assert [(c['connection'], c['provider'], c['api_key_env'], c['status'],
             [(g['provider'], [m['id'] for m in g['models']]) for g in c['groups']])
            for c in listed['connections']] == [
        ('ds', 'deepseek', 'DEEPSEEK_TEST_KEY', 'available', [('deepseek', ['shared-model', 'deepseek-chat'])]),
        ('or', 'openrouter', 'OPENROUTER_TEST_KEY', 'available', [('openrouter', ['shared-model', 'router-only'])])]
    # Read only: no Agent was prepared or recorded, and no key value is in the result.
    assert driver.prepared == [] and not (control.root / 'control' / 'agents').exists()
    assert SECRET not in json.dumps(document)


def test_models_narrows_to_a_declared_provider_and_refuses_another(tmp_path: Path, pi: Path) -> None:
    control, _ = two_pi(tmp_path)
    document, failed = call(control, action='models', runtime='pi', provider='openrouter')
    assert not failed and [c['connection'] for c in document['result']['connections']] == ['or']
    document, failed = call(control, action='models', runtime='pi', provider='anthropic')
    assert failed and document['error']['code'] == 'invalid-provider'
    assert "['deepseek', 'openrouter']" in document['error']['message']


def test_models_without_a_connection_lists_the_native_login(tmp_path: Path, pi: Path) -> None:
    control = AgentControl(tmp_path / 'empty-root', {'pi': RecordingDriver()})
    document, failed = call(control, action='models', runtime='pi')
    assert not failed
    [entry] = document['result']['connections']
    assert (entry['connection'], entry['provider']) == (None, None)
    assert [g['provider'] for g in entry['groups']] == ['deepseek', 'openrouter']
    document, failed = call(control, action='models', runtime='gemini')
    assert failed and document['error']['code'] == 'unsupported-runtime'


def test_models_is_a_discoverable_feature_taking_runtime_and_provider() -> None:
    found = handle_request({'action': 'discover', 'query': 'models'}, lambda request: None).document
    assert 'models' in [item['feature'] for item in found['features']]
    schema = handle_request({'action': 'describe', 'feature': 'models', 'schema': True},
                            lambda request: None).document['input_schema']
    assert set(schema['properties']) == {'runtime', 'provider'} and schema['required'] == ['runtime']
    create = handle_request({'action': 'describe', 'feature': 'create', 'schema': True},
                            lambda request: None).document['input_schema']
    assert 'provider' in create['properties'] and create['required'] == ['instruction', 'runtime']


# -- create's provider --------------------------------------------------------------

def test_provider_selects_its_connection_and_tells_a_shared_model_apart(tmp_path: Path, pi: Path) -> None:
    control, driver = two_pi(tmp_path)
    for provider, connection in (('openrouter', 'or'), ('deepseek', 'ds')):
        document, failed = call(control, action='create', runtime='pi', instruction='Work.',
                                provider=provider, model='shared-model')
        assert not failed, document
        selection = driver.prepared[-1]
        assert (selection['connection'], selection['provider'], selection['model']) == (
            connection, provider, 'shared-model')
        assert document['result']['connection'] == connection


def test_without_provider_several_connections_stay_ambiguous(tmp_path: Path, pi: Path) -> None:
    control, driver = two_pi(tmp_path)
    document, failed = call(control, action='create', runtime='pi', instruction='Work.', model='shared-model')
    assert failed and document['error'] == {
        'code': 'ambiguous-connection',
        'message': f"{control.root / 'config.yml'} declares several pi connections ['ds', 'or']; "
                   'select one with a preset.'}
    assert driver.prepared == []


def test_a_provider_no_connection_declares_is_refused_before_anything_starts(tmp_path: Path, pi: Path) -> None:
    control, driver = two_pi(tmp_path)
    document, failed = call(control, action='create', runtime='pi', instruction='Work.', provider='anthropic')
    assert failed and document['error']['code'] == 'invalid-provider'
    assert 'cannot name a key' in document['error']['message']
    assert driver.prepared == [] and not (control.root / 'control' / 'agents').exists()

    # A connection that declares no provider is not chosen by one either.
    root = configured(tmp_path / 'plain', f"connections:\n  only:\n    runtime: pi\n    home: {tmp_path / 'pi-home'}\n")
    document, failed = call(AgentControl(root, {'pi': driver}), action='create', runtime='pi',
                            instruction='Work.', provider='deepseek')
    assert failed and document['error']['code'] == 'invalid-provider'
    assert "declared providers: none" in document['error']['message']


def test_provider_with_a_preset_must_be_the_preset_connection_provider(tmp_path: Path, pi: Path) -> None:
    control, driver = two_pi(tmp_path)
    document, failed = call(control, action='create', runtime='pi', instruction='Work.',
                            preset='cheap', provider='openrouter')
    assert failed and document['error']['code'] == 'invalid-provider'
    assert "contradicts preset 'cheap'" in document['error']['message']
    document, failed = call(control, action='create', runtime='pi', instruction='Work.',
                            preset='cheap', provider='deepseek')
    assert not failed and (driver.prepared[-1]['connection'], driver.prepared[-1]['model']) == ('ds', 'deepseek-chat')
