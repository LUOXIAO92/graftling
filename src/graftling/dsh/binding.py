"""Bind DSH to the common execution callback and native reply receiver."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from graftling import Run
from graftling.connection import agents_connection
from graftling.extra_tools import ExtraTool, extra_tool_table
from graftling.notices import notice_id
from graftling.process import authenticate_process
from .execution import DshExecution


def session_context(turn: DshExecution) -> dict | None:
    """Read Session/source/cwd from the verified native follow snapshot."""
    header = turn.native_header
    if header is None:
        return None
    child = header.get('origin') == 'subagent' or header.get('delegationDepth', 0) > 0
    return {'runtime': 'dsh', 'session': header['id'],
            'source': 'child' if child else 'main', 'cwd': header.get('cwd')}


def bind_run(turn: DshExecution, run: Run, extra_tools: Sequence[ExtraTool] | None = None) -> None:
    """Use the existing core identity; no preparation or second controller.

    ``extra_tools`` are the host's own tools served beside ``agents`` on the
    same channel (:mod:`graftling.extra_tools`); each keeps its own authorization.
    """
    if run.call is None:
        raise ValueError('The DSH execution needs its owning Agents callback.')
    table = extra_tool_table(extra_tools)
    # disallowed_tools naming an owner tool as `mcp__<server>__<name>` (551): it is not offered at all. DSH cannot
    # hide any other MCP tool (agents included), so such a deny refuses the create instead of being ignored.
    denied = [name for name in (getattr(turn, 'request', {}) or {}).get('disallowed_tools') or () if name.startswith('mcp__')]
    own = {f'mcp__{getattr(turn, "mcp_server", "graftling")}__{name}': name for name in table}
    refused = [name for name in denied if name not in own]
    if refused:
        from graftling.tool import AgentsError

        raise AgentsError('unavailable', f'DSH can deny only the owner tools {sorted(own)} among MCP tools; '
                                         f'{refused} cannot be hidden from this child.')
    for name in denied:
        table.pop(own[name], None)
    turn.extra_tools = table
    if 'completion_message' in run.settings:
        # GraphTraj nests native settings in adapter_request; the independent
        # Driver passes them directly. Both receive the same verified notice.
        turn.request['completion_message'] = run.settings['completion_message']

    @contextmanager
    def connection(directory: Path) -> Iterator[str]:
        """Authenticate the actual MCP subprocess using the request lock PID."""
        def authenticate(pid: int) -> None:
            """Use only the still-owned native service's OS ancestry."""
            authenticate_process(turn.service.process if turn.service else None, pid)

        with agents_connection(directory, run.call, authenticate, native_context=lambda: session_context(turn),
                               extra_tools=list(turn.extra_tools.values())) as address:
            yield address

    def receive(event: dict) -> bool:
        """Deliver once, or continue the same native Session from standby."""
        if event['parent'] != run.agent or not event['session']:
            return False
        with turn.lock:
            active = turn.active and not turn.stopping.is_set() and not turn.finished.is_set()
        if not active:
            return run.events.continue_from_child(event, run.archive)
        # The relay admits one id once, so each request of an execution has its own.
        identity = (notice_id(event['execution'], 'request', event['request']) if event.get('request')
                    else event['execution'])
        with turn.lock:
            result = turn.operate({
                'operation': 'receive', 'session': turn.session, 'execution_id': turn.execution_id,
                'message': {'id': identity, 'sessionId': turn.session,
                            'senderSessionId': event['session'], 'sender': event['agent'],
                            'recipient': run.agent, 'body': event['body']},
            })
        return result.get('accepted') is True

    turn.host_connection = connection
    turn.connection_env = 'GRAFTLING_CONNECTION'
    run.events.receive_children(receive)
