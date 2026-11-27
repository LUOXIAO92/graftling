"""A DSH child or model listing on a provider DSH routes through llm-pi-ai (608).

A stand-in DSH install carries a stand-in pi-ai catalog that Node reads as DSH
does; the controlled DshService (dsh_standalone_peer.py) stands in for the
native service. The installed DSH is used only where said, without a model turn.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from graftling.claude.host import IndependentCalls
from graftling.dsh import execution
from graftling.dsh.errors import DshError
from graftling.dsh.settings import catalog_providers, dsh_package, prepare
from graftling.models import discover_models
from dsh_standalone_peer import FakeDshService, install_dsh
from test_dsh_standalone_child import claude_main, launch_patches, selected, stop_owned

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='Node reads the DSH provider catalog.')

KEY_ENV = 'OPENROUTER_608_KEY'
KEY = 'never-shown-608-key'
MODEL = 'openai/gpt-4.1-nano'
CONFIG = """connections:
  router-dsh:
    runtime: dsh
    provider: {provider}
{key}    models:
      - {model}
"""


def catalog(root: Path, providers: dict[str, list[str]]) -> None:
    """A stand-in pi-ai beside the DSH install: its providers and their chat models."""
    package = root / 'node_modules/@earendil-works/pi-ai'
    package.mkdir(parents=True)
    (package / 'package.json').write_text(json.dumps({
        'name': '@earendil-works/pi-ai', 'type': 'module', 'exports': {'./providers/all': './all.js'}}))
    (package / 'all.js').write_text(
        f'const chat = {json.dumps(providers)};\n'
        'export const builtinProviders = () => Object.keys(chat).map((id) => ({id}));\n'
        'export const getBuiltinModels = (id) => chat[id];\n'
        # A provider listing only image models is no chat route.
        'export const getAllBuiltinModels = (id) => id === "images-only" ? ["picture"] : chat[id];\n')


@pytest.fixture
def dsh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A stand-in DSH whose catalog serves openrouter; the parent also holds DSH's default key."""
    for name in [name for name in os.environ if name.startswith('GRAPHTRAJ_')]:
        monkeypatch.delenv(name)
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'never-shown-608-default-key')
    monkeypatch.setattr(execution.DshExecution, 'service_type', FakeDshService)
    FakeDshService.instances = []
    executable = install_dsh(tmp_path / 'dsh')
    catalog(tmp_path / 'dsh', {'openrouter': [MODEL], 'opencode-go': ['m'], 'images-only': []})
    return {'executable': str(executable), 'model': 'deepseek-flash', 'sandbox': 'workspace-write'}


def configure(root: Path, provider: str, *, key: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'config.yml').write_text(CONFIG.format(provider=provider, model=MODEL,
                                                   key=f'    api_key_env: {KEY_ENV}\n' if key else ''))


def test_a_child_runs_on_the_provider_with_only_its_key(tmp_path: Path, dsh: dict) -> None:
    configure(tmp_path / 'agents', 'openrouter')
    host = IndependentCalls(tmp_path / 'agents', tmp_path, {}, {'dsh': dsh})
    invoke, _, _ = claude_main(host, tmp_path)
    try:
        created = invoke('create', runtime='dsh', instruction='one', model=MODEL)
        assert (created['result']['connection'], created['result']['model']) == ('router-dsh', MODEL)
        invoke('wait', agent=created['agent'], timeout_seconds=10)
        [service] = FakeDshService.instances
        assert selected(service) == {'sessionId': service.session, 'provider': 'openrouter', 'model': MODEL}
        rows = {row.get('id'): row for row in service.patches if 'id' in row}
        assert rows['llm-pi-ai'] == {'id': 'llm-pi-ai', 'config': {'providers': {'openrouter': {
            'apiKeyEnv': KEY_ENV, 'models': [{'id': MODEL}]}}}}
        # DeepSeek's own route and search rows keep DSH's defaults; the child holds no DeepSeek key.
        assert not {'llm-deepseek', 'web-search-deepseek', 'web-deepseek-api'} & set(rows)
        # Names only: a failing assertion must not print the launch environment.
        assert (KEY_ENV in service.environment, 'DEEPSEEK_API_KEY' in service.environment) == (True, False)
        assert KEY not in json.dumps(invoke('status', agent=created['agent']))
    finally:
        stop_owned(host.control)


@pytest.mark.parametrize(('provider', 'key', 'reason'), [
    ('openrouter', False, 'needs api_key_env'),
    ('no-such-provider', True, "does not serve provider 'no-such-provider'"),
    ('images-only', True, "does not serve provider 'images-only'"),
])
def test_a_provider_dsh_cannot_serve_on_a_named_key_is_refused(tmp_path: Path, dsh: dict, provider: str, key: bool,
                                                               reason: str) -> None:
    configure(tmp_path / 'agents', provider, key=key)
    host = IndependentCalls(tmp_path / 'agents', tmp_path, {}, {'dsh': dsh})
    _, call, _ = claude_main(host, tmp_path)
    result = call('subagents', {'action': 'execute', 'feature': 'create',
                                'arguments': {'runtime': 'dsh', 'instruction': 'one', 'model': MODEL}})
    assert result.failed
    error = json.dumps(result.document)
    assert 'invalid-config' in error and reason in error, error
    assert FakeDshService.instances == []


def test_prepare_refuses_without_the_installed_catalog(tmp_path: Path, dsh: dict) -> None:
    bare = install_dsh(tmp_path / 'bare-dsh')
    with pytest.raises(DshError) as caught:
        prepare({**dsh, 'executable': str(bare), 'provider': 'openrouter', 'api_key_env': KEY_ENV}, tmp_path)
    assert caught.value.code == 'ROLE_CONFIG_UNSUPPORTED' and 'cannot be read' in caught.value.message
    # deepseek-official needs no catalog and keeps its llm-deepseek row.
    settings = prepare({**dsh, 'executable': str(bare), 'api_key_env': KEY_ENV}, tmp_path)
    _, patches, _ = launch_patches(settings, tmp_path)
    assert settings['provider'] == 'deepseek-official'
    assert [row for row in patches if row.get('id') in ('llm-deepseek', 'llm-pi-ai')] == [
        {'id': 'llm-deepseek', 'config': {'apiKeyEnv': KEY_ENV}}]


class CatalogService:
    """The listing's DSH service: keeps the patch it was given and answers one catalog."""

    patches: list = []

    def __init__(self, executable: str, cwd: Path, environment: dict, **options: object) -> None:
        CatalogService.patches = json.loads(Path(options['profile_patch']).read_text())

    def start(self) -> None:
        """Ready at once."""

    def rpc(self, method: str, body: dict | None = None) -> dict:
        assert method == 'session/modelCatalog'
        return {'groups': [{'id': 'deepseek-official', 'models': [{'id': 'deepseek-flash', 'reasoning': None}]},
                           {'id': 'openrouter', 'models': [{'id': MODEL, 'name': 'GPT-4.1 Nano'}]}]}

    def close(self) -> None:
        """Nothing to stop."""


def test_listing_routes_the_provider_like_its_child(tmp_path: Path, dsh: dict,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import graftling.dsh.service

    monkeypatch.setattr(graftling.dsh.service, 'DshService', CatalogService)
    if os.name == 'nt':
        # Windows finds a command on PATH only under a PATHEXT name; the service is a stand-in anyway.
        Path(dsh['executable']).with_suffix('.cmd').write_text('@exit /b 1\n')
    monkeypatch.setenv('PATH', f"{Path(dsh['executable']).parent}{os.pathsep}{os.environ['PATH']}")
    found = discover_models({'runtime': 'dsh', 'provider': 'openrouter', 'api_key_env': KEY_ENV})
    assert CatalogService.patches == [{'id': 'llm-pi-ai', 'config': {'providers': {'openrouter': {
        'apiKeyEnv': KEY_ENV}}}}]
    assert (found['status'], found['models']) == ('available', [
        {'id': MODEL, 'alias': 'GPT-4.1 Nano', 'provider': 'openrouter', 'supported_efforts': None}])
    for connection, reason in (({'provider': 'openrouter'}, 'needs api_key_env'),
                               ({'provider': 'no-such-provider', 'api_key_env': KEY_ENV}, 'does not serve')):
        with pytest.raises(ValueError, match=reason):
            discover_models({'runtime': 'dsh', **connection})


@pytest.fixture
def installed() -> str:
    found = shutil.which('dsh')
    if found is None:
        pytest.skip('The installed DSH is required for native provider evidence.')
    return found


def test_the_installed_dsh_serves_the_documented_providers(installed: str) -> None:
    served = catalog_providers(str(dsh_package(installed)))
    assert served is not None and {'openrouter', 'opencode-go', 'opencode'} <= set(served)
    assert 'deepseek-official' not in served


def test_the_installed_dsh_composes_the_provider_route(tmp_path: Path, dsh: dict, installed: str,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """The installed DSH loads the child's patch with the route keyed by name; no service, no network."""
    settings = prepare({**dsh, 'executable': installed, 'provider': 'openrouter', 'api_key_env': KEY_ENV,
                        'model': MODEL}, tmp_path)
    environment, _, patch = launch_patches(settings, tmp_path)
    dumped = subprocess.run([installed, '--profile', 'web', '--patch', str(patch), '--dump-config'],
                            cwd=tmp_path, env={**environment, 'DSH_HOME': str(tmp_path / 'dump-home')},
                            capture_output=True, text=True, timeout=60)
    assert dumped.returncode == 0, dumped.stderr[-2000:]
    rows = {row.get('id'): row for row in yaml.load(dumped.stdout, Loader=execution._PatchLoader)}
    assert rows['llm-pi-ai']['config'] == {'providers': {'openrouter': {'apiKeyEnv': KEY_ENV}}}
    assert not rows['llm-pi-ai'].get('disabled')
    assert KEY not in dumped.stdout

