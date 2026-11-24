"""A Claude connection's ``base_url`` never receives a host Claude login (613).

Through public seams only: the bound ``subagents`` function over
:class:`AgentControl` with the real :class:`ClaudeDriver`, and
:func:`graftling.discover_models`. The native client is a fake ``claude`` on
``PATH`` that logs each start's argv and authentication-related environment;
no model, network or user login is involved.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

import graftling
from graftling import AgentControl, Caller, discover_models
from graftling.child_env import is_credential_name
from graftling.claude.driver import ClaudeDriver


CALLER = Caller('test-host', None, 'main-session', 'claude')
DUMMY = 'dummy-gateway-key-613'
GATEWAY = 'https://gw.example/v1'
# Everything a host may log Claude Code in, route or authenticate it with.
HOST = {'ANTHROPIC_API_KEY': 'host-anthropic', 'ANTHROPIC_AUTH_TOKEN': 'host-token',
        'CLAUDE_CODE_OAUTH_TOKEN': 'host-oauth', 'ANTHROPIC_CUSTOM_HEADERS': 'Authorization: Bearer host-header',
        'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_SECRET_ACCESS_KEY': 'host-aws'}
WATCHED = (*HOST, 'ANTHROPIC_BASE_URL', 'GATEWAY_TEST_KEY')
CATALOG = {'type': 'control_response', 'response': {'request_id': 'catalog', 'subtype': 'success',
                                                   'response': {'models': [{'value': 'claude-opus-5-5'}]}}}


@pytest.fixture
def claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``claude``: logs every start but ``--version``, answers a catalog query; returns the log."""
    log = tmp_path / 'claude-starts.jsonl'
    path = tmp_path / 'bin' / 'claude'
    path.parent.mkdir()
    path.write_text(f"""#!{sys.executable}
import json, os, sys
if sys.argv[1:] == ['--version']:
    print('2.1.300 (Claude Code)')
    sys.exit(0)
with open({str(log)!r}, 'a') as stream:
    stream.write(json.dumps({{'argv': sys.argv[1:],
                              'env': {{name: os.environ[name] for name in {WATCHED!r} if name in os.environ}}}}) + '\\n')
if '--session-id' not in sys.argv:
    sys.stdin.readline()
    print(json.dumps({CATALOG!r}))
""", encoding='utf-8')
    path.chmod(0o755)
    monkeypatch.setenv('PATH', f"{path.parent}:/usr/bin:/bin")
    for name in [name for name in os.environ if is_credential_name(name)]:
        monkeypatch.delenv(name)
    for name, value in {**HOST, 'GATEWAY_TEST_KEY': DUMMY}.items():
        monkeypatch.setenv(name, value)
    # A logged-in native home that is not the user's.
    home = tmp_path / 'claude-home'
    home.mkdir()
    (home / '.credentials.json').write_text('{}')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(home))
    return log


def control(tmp_path: Path, connection: str = '', **configuration) -> AgentControl:
    root = tmp_path / 'root'
    root.mkdir(exist_ok=True)
    if connection:
        (root / 'config.yml').write_text('connections:\n  gw:\n    runtime: claude\n' + connection, encoding='utf-8')
    driver = ClaudeDriver(tmp_path, {'executable': str(tmp_path / 'bin' / 'claude'), **configuration})
    return AgentControl(root, {'claude': driver})


def call(agents: AgentControl, **request) -> tuple[dict, bool]:
    result = graftling.bind(agents, CALLER)(request)
    return result.document, result.failed


def child_starts(log: Path) -> list[dict]:
    """The logged starts of a child (the tool probe and catalog queries have no Session id)."""
    lines = log.read_text().splitlines() if log.exists() else []
    return [entry for entry in map(json.loads, lines) if '--session-id' in entry['argv']]


def setting_sources(argv: list[str]) -> str | None:
    return argv[argv.index('--setting-sources') + 1] if '--setting-sources' in argv else None


@pytest.mark.parametrize('models', ['', '    models: [claude-haiku-5-5]\n'])
def test_a_connection_endpoint_without_its_key_is_refused_alike_by_models_and_create(
    tmp_path: Path, claude: Path, models: str,
) -> None:
    agents = control(tmp_path, f'    base_url: {GATEWAY}\n    provider: gateway\n' + models)
    listed, failed = call(agents, action='models', runtime='claude')
    assert failed and listed['error']['code'] == 'invalid-config'
    assert "Connection 'gw': base_url needs api_key_env" in listed['error']['message']
    created, failed = call(agents, action='create', runtime='claude', instruction='Work.')
    assert failed and created['error'] == listed['error']
    # Refused before anything started: no Claude process, no Agent record.
    assert not claude.exists() and not (agents.root / 'control' / 'agents').exists()
    with pytest.raises(ValueError, match='base_url needs api_key_env'):
        discover_models({'runtime': 'claude', 'base_url': GATEWAY})
    assert not claude.exists()


def test_a_host_claude_endpoint_without_its_key_is_refused_before_any_process(
    tmp_path: Path, claude: Path,
) -> None:
    created, failed = call(control(tmp_path, base_url=GATEWAY), action='create', runtime='claude',
                           instruction='Work.')
    assert failed and created['error']['code'] == 'invalid-config'
    assert 'base_url needs api_key_env' in created['error']['message']
    assert not claude.exists()


def test_a_child_reaches_its_endpoint_with_only_the_connection_key(tmp_path: Path, claude: Path) -> None:
    agents = control(tmp_path, f'    base_url: {GATEWAY}\n    api_key_env: GATEWAY_TEST_KEY\n')
    # Named tools: no default-tools probe start (576).
    call(agents, action='create', runtime='claude', instruction='Work.', tools=['Read'])
    [start] = child_starts(claude)
    assert start['env'] == {'ANTHROPIC_API_KEY': DUMMY, 'GATEWAY_TEST_KEY': DUMMY, 'ANTHROPIC_BASE_URL': GATEWAY}
    # No user, project or local settings file can add a login (env, apiKeyHelper).
    assert setting_sources(start['argv']) == ''

    # Its model listing is queried the same way.
    found = discover_models({'runtime': 'claude', 'base_url': GATEWAY, 'api_key_env': 'GATEWAY_TEST_KEY'})
    assert found['status'] == 'available'
    query = json.loads(claude.read_text().splitlines()[-1])
    assert query['env'] == start['env'] and setting_sources(query['argv']) == ''


@pytest.mark.parametrize('connection', ['', '    provider: anthropic\n'])
def test_without_an_endpoint_the_child_keeps_the_native_login_as_before(
    tmp_path: Path, claude: Path, connection: str,
) -> None:
    call(control(tmp_path, connection), action='create', runtime='claude', instruction='Work.', tools=['Read'])
    [start] = child_starts(claude)
    assert start['env'] == {name: HOST[name] for name in (
        'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_CUSTOM_HEADERS',
        'CLAUDE_CODE_USE_BEDROCK', 'AWS_SECRET_ACCESS_KEY')}
    assert setting_sources(start['argv']) is None
