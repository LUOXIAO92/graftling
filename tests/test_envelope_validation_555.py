"""555: one call returns every input problem, and execute takes its arguments from a JSON file."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import graftling
from graftling import AgentsResult, ValidationErrors, handle_request, validate_envelope, validate_schema

SOURCE = Path(__file__).resolve().parents[1] / 'src'
SECRET = 'do-not-echo-7f3a'


def executed(request: dict) -> AgentsResult:
    """The host callback: echo what reached the business layer."""
    return AgentsResult({'executed': request})


def paths(result: AgentsResult) -> list[str]:
    """The failing paths of one rejected result, in order."""
    assert result.failed and result.document['error']['code'] == 'invalid-input'
    return [error['path'] for error in result.document['error']['errors']]


def test_envelope_and_argument_errors_return_together() -> None:
    """An unknown envelope field and several argument problems come back in one result."""
    result = handle_request({'action': 'execute', 'feature': 'create', 'query': 'x',
                             'arguments': {'runtime': 1, 'name': [], 'colour': 'red'}}, executed)
    assert paths(result) == ['request.query', 'arguments.instruction', 'arguments.runtime',
                             'arguments.name', 'arguments.colour']
    message = result.document['error']['message']
    assert 'arguments.instruction: required' in message and 'arguments.colour: unexpected parameter' in message


def test_flat_tool_reports_every_problem() -> None:
    """The registered agents function lists missing, unknown and empty fields at once."""
    call = graftling.bind(object(), graftling.Caller('test-host', None))
    result = call({'action': 'wait', 'agent': ' ', 'colour': 1})
    assert [error['path'] for error in result.document['error']['errors']] == [
        'request.colour', 'request.timeout_seconds', 'request.agent']


def test_validate_schema_lists_all_and_stays_a_value_error() -> None:
    """Hosts catching ValueError keep working and can read and extend the list."""
    schema = {'type': 'object', 'required': ['a', 'b'], 'additionalProperties': False,
              'properties': {'a': {'const': True}, 'b': {'type': 'array', 'minItems': 1}}}
    with pytest.raises(ValueError) as caught:
        validate_schema({'a': False, 'b': []}, schema, 'arguments')
    assert isinstance(caught.value, ValidationErrors)
    assert caught.value.errors == [{'path': 'arguments.a', 'reason': 'expected True'},
                                   {'path': 'arguments.b', 'reason': 'expected at least 1 items'}]
    combined = ValidationErrors(caught.value.errors + [{'path': 'arguments.c', 'reason': 'host rule'}])
    assert str(combined).endswith('; arguments.c: host rule')
    with pytest.raises(ValidationErrors) as single:
        validate_schema(1, {'type': 'string'}, 'x')
    assert str(single.value) == 'x: expected string'


def test_any_of_reports_the_meant_alternative() -> None:
    """Only the alternative of the value's type is reported; otherwise each alternative is named."""
    schema = {'anyOf': [
        {'type': 'string'},
        {'type': 'object', 'required': ['kind', 'text'], 'additionalProperties': False,
         'properties': {'kind': {'enum': ['a', 'b']}, 'text': {'type': 'string'}}},
    ]}
    found = graftling.schema_errors({'kind': 'c'}, schema, 'value')
    assert found == [{'path': 'value.text', 'reason': 'required'},
                     {'path': 'value.kind', 'reason': "expected one of ['a', 'b']"}]
    [mixed] = graftling.schema_errors(3, schema, 'value')
    assert mixed['path'] == 'value' and 'value: expected string | value: expected object' in mixed['reason']


def write(path: Path, text: str) -> Path:
    """Write one arguments file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_arguments_file_supplies_and_inline_overrides(tmp_path: Path) -> None:
    """The file's object is the arguments; inline fields replace its top-level fields."""
    write(tmp_path / 'ws' / 'args' / 'create.json',
          json.dumps({'runtime': 'codex', 'instruction': 'long body', 'name': 'from-file'}))
    result = handle_request({'action': 'execute', 'feature': 'create', 'arguments_file': 'args/create.json',
                             'arguments': {'name': 'inline'}}, executed, files=tmp_path / 'ws')
    assert result.document == {'executed': {'action': 'create', 'runtime': 'codex',
                                            'instruction': 'long body', 'name': 'inline'}}
    absolute = handle_request({'action': 'execute', 'feature': 'create',
                               'arguments_file': str(tmp_path / 'ws' / 'args' / 'create.json')},
                              executed, files=tmp_path / 'ws')
    assert absolute.document['executed']['name'] == 'from-file'


def test_arguments_file_content_is_validated_with_the_envelope(tmp_path: Path) -> None:
    """Problems in the file's object return together with envelope problems, without its values."""
    write(tmp_path / 'a.json', json.dumps({'runtime': SECRET, 'colour': SECRET, 'name': 5}))
    result = handle_request({'action': 'execute', 'feature': 'create', 'arguments_file': 'a.json',
                             'schema': True}, executed, files=tmp_path)
    assert paths(result) == ['request.schema', 'arguments.instruction', 'arguments.colour', 'arguments.name']
    assert SECRET not in json.dumps(result.document)


@pytest.mark.parametrize(('content', 'reason'), [
    (None, 'does not exist'),
    ('{"runtime": "' + SECRET + '",', 'is not valid JSON (line 1, column'),
    (f'["{SECRET}"]', 'must hold a JSON object'),
])
def test_arguments_file_refusals_echo_no_content(tmp_path: Path, content: str | None, reason: str) -> None:
    """Missing, invalid and non-object files are refused by position or kind only."""
    if content is not None:
        write(tmp_path / 'a.json', content)
    result = handle_request({'action': 'execute', 'feature': 'status', 'arguments_file': 'a.json'},
                            executed, files=tmp_path)
    assert paths(result) == ['request.arguments_file']
    assert reason in result.document['error']['message']
    assert SECRET not in json.dumps(result.document)


def test_arguments_file_outside_the_boundary_is_refused(tmp_path: Path) -> None:
    """Paths leaving the host's directory, by .., absolute path or link, read nothing."""
    workspace = tmp_path / 'ws'
    outside = write(tmp_path / 'secret.json', json.dumps({'agent': SECRET}))
    workspace.mkdir()
    (workspace / 'link.json').symlink_to(outside)
    (workspace / 'dir-link').symlink_to(tmp_path)
    for named in ('../secret.json', str(outside), 'link.json', 'dir-link/secret.json', '../missing.json'):
        result = handle_request({'action': 'execute', 'feature': 'status', 'arguments_file': named},
                                executed, files=workspace)
        assert paths(result) == ['request.arguments_file'], named
        assert 'outside the directory this caller may read' in result.document['error']['message']
        assert SECRET not in json.dumps(result.document)
    directory = handle_request({'action': 'execute', 'feature': 'status', 'arguments_file': '.'},
                               executed, files=workspace)
    assert 'is not a regular file' in directory.document['error']['message']


def test_without_a_host_boundary_a_file_is_refused(tmp_path: Path) -> None:
    """A host that supplies no directory never reads a file; validate_envelope refuses it too."""
    write(tmp_path / 'a.json', '{}')
    result = handle_request({'action': 'execute', 'feature': 'status', 'arguments_file': str(tmp_path / 'a.json')},
                            executed)
    assert paths(result) == ['request.arguments_file']
    assert 'accepts no arguments file' in result.document['error']['message']
    with pytest.raises(ValidationErrors):
        validate_envelope({'action': 'execute', 'feature': 'status', 'arguments_file': 'a.json'})
    assert validate_envelope({'action': 'execute', 'feature': 'status', 'arguments_file': 'a.json'},
                             files=tmp_path) == 'execute'


def test_envelope_schema_describes_the_file_field() -> None:
    """Hosts reusing the envelope and MCP clients see the field and the merge rule."""
    described = graftling.ENVELOPE_SCHEMA['properties']['arguments_file']['description']
    assert 'JSON file' in described and 'fix the file' in described
    assert 'replace' in graftling.ENVELOPE_SCHEMA['properties']['arguments']['description']


# A stand-in for the native DSH entry (as in test_dsh_standalone_host): it starts the
# real DSH host, then the real agents-mcp stdio entry as its own direct child.
FAKE_DSH = '''
import json, os, subprocess, sys
host = subprocess.Popen([sys.executable, '-m', 'graftling.dsh.host', '--config', os.environ['TEST_CONFIG']],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
host.stdin.write(os.environ['TEST_LAUNCH'] + '\\n')
host.stdin.flush()
address = json.loads(host.stdout.readline())['connection']
env = {**os.environ, 'GRAFTLING_CONNECTION': address, 'DSH_SESSION_ID': 'dsh-main', 'DSH_AGENT_SOURCE': 'main'}
lines = [json.dumps({'jsonrpc': '2.0', 'id': index, 'method': 'tools/call',
                     'params': {'name': 'subagents','arguments': request}})
         for index, request in enumerate(json.loads(os.environ['TEST_REQUESTS']))]
mcp = subprocess.run([sys.executable, '-I', '-m', 'graftling.mcp'], env=env, input='\\n'.join(lines) + '\\n',
                     capture_output=True, text=True, timeout=60)
host.stdin.close()
host.wait(20)
print(json.dumps([json.loads(line)['result']['structuredContent'] for line in mcp.stdout.splitlines()]))
'''


def test_agents_mcp_reads_files_inside_the_verified_session_cwd(tmp_path: Path) -> None:
    """Through agents-mcp and its owning DSH host the boundary is the native Session's cwd."""
    package = tmp_path / 'dsh'
    write(package / 'package.json', json.dumps({'name': '@deepseek-ai/dsh', 'version': '0.2.2-alpha.2'}))
    entry = write(package / 'lib' / 'bin.py', FAKE_DSH)
    workspace = tmp_path / 'ws'
    write(workspace / 'status.json', '{"agent": "nobody"}')
    write(tmp_path / 'outside.json', '{"agent": "nobody"}')
    write(tmp_path / 'host.json', json.dumps({'root': str(tmp_path / 'data')}))
    requests = [
        {'action': 'execute', 'feature': 'status', 'arguments_file': 'status.json'},
        {'action': 'execute', 'feature': 'status', 'arguments_file': '../outside.json'},
        {'action': 'execute', 'feature': 'create', 'extra': 1, 'arguments': {'runtime': 2}},
    ]
    env = {key: value for key, value in os.environ.items()
           if key not in {'GRAFTLING_CONNECTION', 'DSH_SESSION_ID', 'DSH_AGENT_SOURCE'}}
    env.update(GRAFTLING_DSH_NODE=sys.executable, GRAFTLING_DSH_ENTRY=str(entry), PYTHONPATH=str(SOURCE),
               TEST_CONFIG=str(tmp_path / 'host.json'), TEST_REQUESTS=json.dumps(requests),
               TEST_LAUNCH=json.dumps({'header': {'version': 4, 'id': 'dsh-main', 'cwd': str(workspace),
                                                  'isSeeded': False}, 'model': 'deepseek-parent'}))
    done = subprocess.run([sys.executable, str(entry)], cwd=workspace, env=env,
                          capture_output=True, text=True, timeout=90)
    assert done.returncode == 0, done.stderr
    read, outside, invalid = json.loads(done.stdout)
    assert read['error']['code'] == 'not-found'
    assert [error['path'] for error in outside['error']['errors']] == ['request.arguments_file']
    assert [error['path'] for error in invalid['error']['errors']] == [
        'request.extra', 'arguments.instruction', 'arguments.runtime']
