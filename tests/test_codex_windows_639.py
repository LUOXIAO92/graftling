"""639 (W7): a Codex child and the Codex parent host through graftling, on every platform.

- M17: on Windows graftling writes no Unix socket permission entry for Codex
  (``apply_unix_socket_access``, ``project_access``, ``fork_session``); POSIX
  writes them as before. Both branches run on every host (``windows`` patched).
- A Codex child is created, runs a tool and ends its turn, is stopped, and after
  its owner dies is taken over by the next host: the reconcile ends it
  ``owner-exited`` and stops the tools left, the stop confirms that end, and the
  same native Session continues under the new owner.
- The Codex parent host serves one real MCP tool call over its channel (the
  named pipe on Windows), and the Session ends without a signal: the host stops
  and records the child it owns, and the MCP server ends at its stdin's EOF (M18).

The Codex service is the controlled peer (``codex_stdio_peer.py``), started
through a ``.cmd`` stand-in on Windows (637). On Windows its tools inherit the
service's handles (``PEER_TOOL_INHERIT``), as a Runtime that starts children
inheriting handles does; that real Codex does so is an inference for the real
Windows run. Every process this test stops is one it started, checked by pid
and start time.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

from graftling import AgentControl, Caller, bind
from graftling.codex.driver import CodexDriver
from graftling.member_processes import find_marked_processes
from graftling.process import process_arguments
from graftling.tool_descendants import Identity, identity
import test_codex_tool_reclaim_462 as reclaim

WINDOWS = sys.platform == 'win32'
native_settings = reclaim.native_settings

OWNER = r'''
import json, os, sys
from pathlib import Path
from graftling import AgentControl, Caller, bind
from graftling.codex.driver import CodexDriver
control = AgentControl(Path(sys.argv[1]), {'codex': CodexDriver(json.loads(sys.argv[2]))})
call = bind(control, Caller(*json.loads(sys.argv[3])))
print(json.dumps({'pid': os.getpid()}), flush=True)
for line in sys.stdin:
    print(json.dumps(call(json.loads(line)).document), flush=True)
'''


# --- M17 ---------------------------------------------------------------------------------

def socket_request(worktree: str) -> dict:
    return {'worktree_path': worktree, 'arguments': ['codex', 'app-server', '--listen', 'stdio://'],
            'session_parameters': {'config': {'default_permissions': 'chosen', 'permissions': {
                'chosen': {'extends': ':workspace', 'filesystem': {worktree: 'write'}}}}}}


@pytest.mark.parametrize('on_windows', [False, True], ids=['posix', 'windows'])
def test_unix_socket_grants_are_written_only_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_windows: bool,
) -> None:
    """``codex.unix_sockets`` is checked everywhere, projected only on POSIX."""
    from graftling.codex.context import CodexAdapterError, apply_unix_socket_access

    monkeypatch.setattr('graftling.process_tree.windows', lambda: on_windows)
    worktree, socket = str(tmp_path), str(tmp_path / 'agents.sock')
    request = socket_request(worktree)
    original = copy.deepcopy(request)
    apply_unix_socket_access(request, {worktree: [socket]})
    if on_windows:
        assert request == original
    else:
        network = request['session_parameters']['config']['permissions']['chosen']['network']
        assert network['unix_sockets'] == {socket: 'allow'}
        assert network['dangerously_allow_all_unix_sockets'] is False
        assert any(part.startswith('permissions=') for part in request['arguments'])
    with pytest.raises(CodexAdapterError):
        apply_unix_socket_access(socket_request(worktree), {worktree: ['relative.sock']})


@pytest.mark.parametrize('on_windows', [False, True], ids=['posix', 'windows'])
def test_local_websocket_projection_names_no_unix_socket_key_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_windows: bool,
) -> None:
    """The loopback grant leaves Unix socket keys out on Windows, and its withdrawal restores the profile."""
    from graftling.codex.local_websocket import project_access

    monkeypatch.setattr('graftling.process_tree.windows', lambda: on_windows)
    worktree = str(tmp_path)
    request = socket_request(worktree)
    config = request['session_parameters']['config']
    config['features'] = {'network_proxy': True}
    config['permissions']['chosen']['network'] = {'enabled': True}
    original = copy.deepcopy(request)
    project_access(request, {'url': 'ws://127.0.0.1:45345/rpc', 'codex_home': str(tmp_path / 'home'),
                             'token_file': str(tmp_path / 'token'), 'worktrees': [worktree]})
    network = config['permissions']['chosen']['network']
    assert network['domains']['127.0.0.1'] == 'allow'
    assert network['allow_local_binding'] is False
    assert ('dangerously_allow_all_unix_sockets' in network) is not on_windows, network
    assert any('unix_sockets' in part for part in request['arguments']) is not on_windows
    project_access(request, None)
    assert config == original['session_parameters']['config']


@pytest.mark.parametrize('on_windows', [False, True], ids=['posix', 'windows'])
def test_fork_selects_a_socket_profile_only_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_windows: bool,
) -> None:
    """A fork asked for a socket profile gets it on POSIX; on Windows it keeps Main's own profile."""
    from graftling.codex.app_server import CodexAppServer

    monkeypatch.setattr('graftling.process_tree.windows', lambda: on_windows)
    server = CodexAppServer(cwd=tmp_path)
    sent = {}

    async def answer(method: str, params: dict, **options: object) -> dict:
        sent.update(method=method, params=params)
        return {'thread': {'id': 'fork-1', 'ephemeral': True, 'forkedFromId': 'main', 'path': None},
                'model': 'm', 'modelProvider': 'p',
                'activePermissionProfile': {'id': params.get('permissions')}}

    server._call = answer
    socket = str(tmp_path / 'agents.sock')
    parent = {'id': 'main', 'model': 'm', 'modelProvider': 'p', 'turn_settings': {},
              'configuration': {'activePermissionProfile': {'id': 'chosen'}}}
    session = asyncio.run(server.fork_session(parent, network_profile='with-socket', unix_socket=socket))
    assert session.thread_id == 'fork-1' and sent['method'] == 'thread/fork'
    if on_windows:
        assert sent['params']['permissions'] == 'chosen' and 'config' not in sent['params'], sent
    else:
        assert sent['params']['permissions'] == 'with-socket'
        override = sent['params']['config']['permissions.with-socket']
        assert override == {'extends': 'chosen', 'network': {'enabled': True, 'unix_sockets': {socket: 'allow'}}}


# --- Codex child: tool, end, stop, takeover ---------------------------------------------

def tool_pair(root: Path, agent: str, since: int) -> dict[str, Identity]:
    """The running tool and its sleeping grandchild of ``agent``'s current execution.

    Windows: listed through the service's job (637), matched by last argument.
    POSIX: the process table, by command line (462).
    """
    if not WINDOWS:
        found = reclaim.tools(since)
        return {key: found[key] for key in ('shell', 'sleep') if key in found}
    try:
        marker = reclaim.record(root, agent)['process'].get('tool_marker')
        listed = find_marked_processes(marker) if marker else {}
    except (OSError, ValueError, KeyError, IndexError):
        return {}  # the record is being replaced, or the job is not open yet
    named = {reclaim.WINDOWS_TOOL: 'shell', reclaim.WINDOWS_SLEEP: 'sleep'}
    found: dict[str, Identity] = {}
    for pid in listed:
        try:
            last = process_arguments(pid)[-1:]
        except OSError:
            continue
        item = identity(pid) if last and last[0] in named else None
        if item is not None:
            found[named[last[0]]] = item
    return found


def both(root: Path, agent: str, since: int) -> dict[str, Identity] | None:
    found = tool_pair(root, agent, since)
    return found if len(found) == 2 else None


def gone(items: dict[str, Identity]) -> bool:
    return not any(reclaim.is_alive(item) for item in items.values())


def stopped_pids(root: Path, agent: str) -> set[int]:
    """Every pid the owner's reclaims recorded stopped for ``agent`` (codex-termination-*.yml)."""
    directory = Path(reclaim.record(root, agent)['process']['tool_descendants']).parent
    pids = set()
    for path in directory.parent.glob('*/codex-termination-*.yml'):
        report = yaml.safe_load(path.read_text(encoding='utf-8'))['escaped_processes']
        pids |= {item['pid'] for item in report['stopped']}
    return pids


def test_codex_child_tool_end_stop_and_takeover(
    tmp_path: Path, native_settings: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create with a tool call that ends its turn; stop a running one; take it over after its owner dies."""
    monkeypatch.setenv('PEER_TOOL_INHERIT', '1')
    root = tmp_path / 'agents'
    since = identity(os.getpid()).start
    owner = subprocess.Popen(
        [sys.executable, '-c', OWNER, str(root), json.dumps(native_settings), json.dumps(reclaim.CALLER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8',
    )
    seen: list[Identity] = []

    def ask(request: dict) -> dict:
        owner.stdin.write(json.dumps(request) + '\n')
        owner.stdin.flush()
        document = json.loads(owner.stdout.readline())
        assert 'error' not in document, document
        return document

    def tools_now(agent: str, label: str) -> dict[str, Identity]:
        found = reclaim.wait_for(lambda: both(root, agent, since), 40 if WINDOWS else 15)
        assert found, f'{label}: the peer did not start the tool; last execution: {reclaim.record(root, agent)}'
        seen.extend(found.values())
        if not WINDOWS:
            time.sleep(1.5)  # the owner's sampler records the tool (462); Windows lists the job instead
        return found

    try:
        assert json.loads(owner.stdout.readline())['pid'] == owner.pid
        # 1. Create: one tool call, then the turn ends; the owner's reclaim stops what the tool left.
        created = ask({'action': 'create', 'runtime': 'codex', 'instruction': 'tool-end'})
        agent, session = created['agent'], created['session']
        first = tools_now(agent, 'tool-end')
        done = ask({'action': 'wait', 'agent': agent, 'timeout_seconds': 60})['result']
        assert done['last_result']['outcome'] == 'completed', done
        assert done['last_result']['last_message'] == 'tool ran'
        assert reclaim.wait_for(lambda: gone(first), 20), first
        assert reclaim.wait_for(lambda: {item.pid for item in first.values()} <= stopped_pids(root, agent), 20)

        # 2. Stop: a running turn with a running tool is interrupted; the tool is stopped with it.
        ask({'action': 'resume', 'agent': agent, 'message': 'tool-hold'})
        second = tools_now(agent, 'stop')
        assert ask({'action': 'interrupt', 'agent': agent})['result']['interrupt'] == 'requested'
        stopped = ask({'action': 'wait', 'agent': agent, 'timeout_seconds': 60})['result']
        assert stopped['last_result']['outcome'] == 'interrupted', stopped
        assert stopped['state'] == 'standby'
        assert reclaim.wait_for(lambda: gone(second), 20), second
        assert reclaim.wait_for(lambda: {item.pid for item in second.values()} <= stopped_pids(root, agent), 20)

        # 3. Takeover: the owner dies during a turn with a running tool; the next host takes over.
        ask({'action': 'resume', 'agent': agent, 'message': 'tool-hold'})
        third = tools_now(agent, 'takeover')
        owner.kill()
        owner.wait(10)
        call = bind(AgentControl(root, {'codex': CodexDriver(native_settings)}), Caller(*reclaim.CALLER))

        def reconciled() -> dict | None:
            result = call({'action': 'status', 'agent': agent})
            assert not result.failed, result.document
            execution = reclaim.record(root, agent)
            return execution if execution['state'] == 'ended' else None
        execution = reclaim.wait_for(reconciled, 60)
        assert execution, f'the dead owner\'s execution never ended: {reclaim.record(root, agent)}'
        assert execution['error']['code'] == 'owner-exited', execution
        escaped = execution['escaped_processes']
        print('escaped_processes:', json.dumps(escaped))
        assert {item.pid for item in third.values()} <= {item['pid'] for item in escaped['stopped']}, escaped
        assert reclaim.wait_for(lambda: gone(third), 20), third
        confirmed = call({'action': 'interrupt', 'agent': agent})
        assert not confirmed.failed, confirmed.document
        assert confirmed.document['result']['termination_confirmed'] == [execution['id']], confirmed.document
        resumed = call({'action': 'resume', 'agent': agent, 'message': 'after takeover'})
        assert not resumed.failed, resumed.document
        assert resumed.document['session'] == session
        done = call({'action': 'wait', 'agent': agent, 'timeout_seconds': 60})
        assert done.document['result']['last_result']['last_message'] == 'after takeover', done.document
        assert done.document['result']['state'] == 'standby'
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(10)
        leftovers = [item for item in seen if reclaim.is_alive(item)]
        if WINDOWS:
            for item in leftovers:
                os.kill(item.pid, 9)  # TerminateProcess
        else:
            reclaim.stop_own(leftovers + [
                item for items in reclaim.ours(since).values() for item in items
                if reclaim.argv(item.pid).startswith(('/bin/sleep 303', sys.executable + ' -c'))
            ])
    assert not leftovers, f'processes left running: {leftovers}'


# --- Codex parent host -------------------------------------------------------------------

def test_codex_parent_host_serves_an_mcp_call_and_ends_its_session_without_a_signal(
    tmp_path: Path, native_settings: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent's MCP server creates a child over the host's channel; at the Session's end the
    host stops and records that child, and the MCP server ends at its stdin's EOF (M18)."""
    from graftling.codex.host import CodexHost

    ended = tmp_path / 'mcp-end.jsonl'
    monkeypatch.setenv('PEER_MCP_END', str(ended))
    monkeypatch.setenv('PEER_MCP_CALLS', json.dumps([
        {'action': 'execute', 'feature': 'create', 'arguments': {'runtime': 'codex', 'instruction': 'hold'}},
        {'action': 'execute', 'feature': 'status', 'arguments': {}},
    ]))
    directory = tmp_path / 'host'
    host = CodexHost(native_settings, directory, 'mcp-session')
    result = host.run()
    assert result['outcome'] == 'completed', result
    answers = json.loads(result['last_agent_message'])
    assert not answers[0]['result']['isError'], answers[0]
    agent = answers[0]['result']['structuredContent']['agent']
    assert [item['agent'] for item in answers[1]['result']['structuredContent']['result']['agents']] == [agent]
    # The MCP server was authenticated in the live service's tree (Windows: its job).
    callers = {item['caller_pid'] for item in result['authenticated_callers']}
    assert callers and os.getpid() not in callers, result['authenticated_callers']
    assert all(item['parent_session'] == result['parent_session'] for item in result['authenticated_callers'])
    # The host stopped the child it owned and recorded its end.
    assert result['stop_unconfirmed'] == []
    execution = json.loads((directory / 'control/agents' / f'{agent}.json').read_text(encoding='utf-8'))['executions'][-1]
    assert execution['state'] == 'ended' and execution['outcome'] == 'interrupted', execution
    # The MCP server ended on its own at stdin EOF: exit code 0, not terminated.
    [end] = [json.loads(line) for line in ended.read_text(encoding='utf-8').splitlines()]
    assert end['exit_code'] == 0, end
