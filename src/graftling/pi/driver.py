"""Pi control with explicitly resolved native settings and no Task lookup."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Mapping

from graftling import AgentsError, Caller, Run
from graftling.control import require_available, within_workspace
from graftling.models import pi_destination, pi_named_keys, pi_offers
from graftling.runtime import RuntimeAdapterError
from .execution import PiExecution, RPC_SECONDS

# Native Pi thinking levels; a model's own list may be narrower.
EFFORTS = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh')


def _refuse_key_held_by_reviewer(request: Mapping[str, Any]) -> None:
    """Refuse a child whose only key for its model is the one its reviewer keeps from it.

    The launch removes the reviewer's variable, so the child would fail at its
    first prompt. Only Pi knows what authenticates a model, so it is asked with
    the variable and without; any other answer is not this cause.
    """
    review = request.get('review') or {}
    variable = review.get('api_key_env')
    if (not variable or request.get('api_key_env') or request.get('base_url')
            or not os.environ.get(variable)):
        return
    target = (request['executable'], request['agent_dir'], request['provider'], request['model'])
    # Pi offers a models.json provider with or without its variable, so the file decides there.
    if (variable in pi_named_keys(request['agent_dir'], request['provider'])
            or pi_offers(*target, without=variable) is False and pi_offers(*target) is True):
        raise AgentsError(
            'invalid-config',
            f"Reviewer {review.get('name', '')!r} uses {variable}, which is kept from the child it reviews, "
            f"so Pi would have no key for provider {request['provider']!r}. Set api_key_env on the connection "
            f'(naming {variable} deliberately shares it with the child), give the reviewer a different '
            "api_key_env, or log in to this provider in the connection's home.")


def _mcp_servers(*files: Path) -> set[str]:
    """The MCP server names Pi's mcp.json files configure (``mcpServers``/``servers`` keys, else top level)."""
    names: set[str] = set()
    for path in files:
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(document, dict):
            servers = document.get('mcpServers', document.get('servers', document))
            names |= {name for name, value in (servers.items() if isinstance(servers, dict) else ())
                      if isinstance(value, dict)}
    return names


def _may_add_tools(*directories: Path) -> bool:
    """Whether Pi may load host extensions or packages here, whose tools cannot be known before start."""
    for directory in directories:
        for name in ('extensions', 'npm'):
            folder = directory / name
            if folder.is_dir() and any(folder.iterdir()):
                return True
        try:
            settings = json.loads((directory / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(settings, dict) and (settings.get('packages') or settings.get('extensions')):
            return True
    return False


def check_tool_names(request: Mapping[str, Any], names: list[str], own_server: str = 'graftling') -> list[str]:
    """Refuse (``unavailable``) the entries Pi provably does not know; return those it cannot check before start.

    Known: Pi's built-ins, the child's own ``mcp__<own_server>__*`` (agents and the owner's tools), and a
    well-formed ``mcp__<server>__<tool>`` of a server the host or project ``mcp.json`` configures. A plain
    non-built-in name is provably unknown when the host loads no extension or package; with them it could be
    an extension tool, which Pi alone knows at start: such names, ``*`` patterns and the tool part of another
    configured server are returned as unverified (551: refused or reported, never silently ignored).
    """
    from .execution import PI_BUILTIN_TOOLS

    agent = Path(request.get('agent_dir') or os.environ.get('PI_CODING_AGENT_DIR') or Path.home() / '.pi/agent')
    project = Path(request['worktree_path']) / '.pi' if request.get('worktree_path') else None
    places = [agent, *([project] if project else [])]
    servers = _mcp_servers(*(place / 'mcp.json' for place in places))
    extensions = _may_add_tools(*places)
    unknown, unverified = [], []
    for raw in dict.fromkeys(names):
        name = raw[1:] if raw[:1] in '+-' else raw
        if '*' in name:
            unverified.append(raw)
        elif name.startswith('mcp__'):
            match = re.fullmatch(r'mcp__([A-Za-z0-9_-]+?)__([A-Za-z0-9_.-]+)', name)
            if match is None or (match.group(1) != own_server and match.group(1) not in servers):
                unknown.append(raw)
            elif match.group(1) != own_server:
                unverified.append(raw)
        elif name not in PI_BUILTIN_TOOLS:
            (unverified if extensions else unknown).append(raw)
    if unknown:
        raise AgentsError('unavailable', f'Pi does not know the tools {unknown}: not a Pi built-in '
                                         f'({sorted(PI_BUILTIN_TOOLS)}), not this child\'s mcp__{own_server}__*, '
                                         f'and not a tool of a configured MCP server ({sorted(servers)})'
                                         + ('' if extensions else '; the host loads no Pi extension') + '.')
    return unverified


def apply_tools(request: dict, tools: Mapping[str, Any], own_server: str = 'graftling') -> None:
    """Put a create's tool choice (551) into a Pi launch request; Pi applies it natively at launch.

    Every entry is checked first (:func:`check_tool_names`): a provably unknown one refuses the create;
    ones Pi alone can resolve at start are kept in ``request['unverified_tools']`` and reported.
    """
    chosen = list(tools['tools']) if tools.get('tools') is not None else None
    denied = list(tools.get('disallowed_tools') or ())
    unverified = check_tool_names(request, [*(chosen or ()), *denied], own_server)
    request['tools'] = chosen
    request['disallowed_tools'] = denied
    if unverified:
        request['unverified_tools'] = unverified


def selected(request: Mapping[str, Any], selection: Mapping[str, Any]) -> dict:
    """Apply one create selection to the host's Pi launch request.

    A selected connection or another provider replaces the host's endpoint
    and key, so a host key never reaches another provider. When the provider,
    model, home or key changes, the network destination is read again from
    Pi's catalog in the selected home; a model without a plain http(s)
    destination is refused rather than launched under the wrong grants.
    """
    host = request
    request = deepcopy(dict(request))
    connection = selection.get('settings')
    if connection is not None and 'base_url' in connection:
        raise AgentsError('invalid-config', 'A Pi connection takes no base_url: config.yml has no provider API. '
                                            "Declare the endpoint as a provider in Pi's own models.json in the "
                                            "connection's home and select that provider and model.")
    provider = selection.get('provider')
    if connection is not None or provider not in (None, request['provider']):
        for field in ('api_key_env', 'base_url', 'provider_api'):
            request.pop(field, None)
    if connection is not None:
        if 'api_key_env' in connection:
            request['api_key_env'] = connection['api_key_env']
        if 'home' in connection:
            request['agent_dir'] = connection['home']
    if provider is not None:
        request['provider'] = provider
    if selection.get('model') is not None:
        request['model'] = selection['model']
    if selection.get('review') is not None:
        request['review'] = deepcopy(dict(selection['review']))
    effort = selection.get('reasoning_effort')
    if effort is not None:
        if effort not in EFFORTS:
            raise AgentsError('invalid-reasoning-effort',
                              f'Pi takes reasoning_effort {list(EFFORTS)}, not {effort!r}.')
        request['reasoning_effort'] = effort
    chosen = ('provider', 'model', 'agent_dir', 'api_key_env')
    if not request.get('base_url') and any(request.get(field) != host.get(field) for field in chosen):
        try:
            request['provider_domain'] = pi_destination(
                request['executable'], request['agent_dir'], request['provider'], request['model'],
                request.get('api_key_env'))
        except RuntimeAdapterError as error:
            raise AgentsError('invalid-config', f"{error.message} Provider {request['provider']!r}, "
                                                f"model {request['model']!r}.") from None
    _refuse_key_held_by_reviewer(request)
    return request


class PiDriver:
    """Bind a host-prepared Pi launch request to the independent control core.

    The host resolves model/provider, resource references and native isolation
    before binding. This driver retains that exact request for continuation;
    it does not discover product configuration or Git.
    """

    supports_review = True

    def __init__(self, request: dict) -> None:
        """Keep a detached, already validated native launch configuration."""
        self.request = deepcopy(request)
        self.turns: dict[str, PiExecution] = {}
        self.lock = threading.Lock()

    def prepare(self, runtime: str, caller: Caller, *, selection: Mapping[str, Any] | None = None,
                workspace: Path | None = None, tools: Mapping[str, Any] | None = None) -> dict:
        """Return the chosen native configuration without reading project state.

        ``selection`` is the create's resolved connection, model and effort.
        ``workspace`` replaces the host worktree as cwd and sandbox grant root;
        every other read, write and network grant stays as resolved.
        ``tools`` (551) becomes Pi's ``--tools``/``--exclude-tools``
        (:func:`graftling.pi.execution.tool_arguments`); omitted, Pi keeps the
        host's own settings.
        """
        if runtime != 'pi':
            raise AgentsError('unsupported-runtime', 'PiDriver only runs Pi.')
        request = deepcopy(self.request) if selection is None else selected(self.request, selection)
        if workspace is not None:
            request['worktree_path'] = str(within_workspace(workspace, self.request['worktree_path']))
        if tools is not None:
            apply_tools(request, tools)
        return {'adapter_request': request, 'evidence': {}, 'cwd': request['worktree_path']}

    def require_available(self, connection: Mapping[str, Any]) -> None:
        """Refuse a connection whose Pi is not installed or not logged in."""
        home = Path(connection.get('home') or os.environ.get('PI_CODING_AGENT_DIR')
                    or Path.home() / '.pi/agent')
        require_available(connection, executable=self.request.get('executable') or shutil.which('pi'),
                          home=home, logins=('auth.json',))

    def start(self, run: Run) -> None:
        """Return after native Session binding, retaining ownership until actual stop."""
        ready = threading.Event()
        failure: list[BaseException] = []

        def created(session: str, pid: int) -> None:
            """Publish the exact native Session before model work begins."""
            run.events.session(session)
            run.events.process(runtime_pid=pid)

        def framed(notice: Mapping[str, Any]) -> str:
            """Name the child and this parent, as the Claude and Codex bindings do."""
            return f"[subagents · {notice['agent']} → {run.agent}]\n{notice['body']}"

        # The child's end that continues this Session is the turn's input.
        notice = run.settings.get('completion_message')
        prompt = framed(notice) if notice else run.message
        request = dict(run.settings['adapter_request'])
        if run.settings.get('pass_env'):
            request['pass_env'] = run.settings['pass_env']
        turn = PiExecution(request, prompt, run.directory,
                           lambda session, pid: ready.set(), created,
                           run.directory / 'trace.jsonl', run.session)
        # Reconciliation stops this execution's escaped tools if its owner dies:
        # by marker (457) and by the descendants recorded in this file (459, 461).
        if getattr(turn, 'member_marker', None):
            facts = {'tool_marker': turn.member_marker}
            if getattr(turn, 'tool_descendants', None) is not None:
                facts['tool_descendants'] = str(Path(turn.tool_descendants).resolve())
            run.events.process(**facts)
        turn.archive = run.archive
        turn.extra_fields = run.extra_fields
        turn.agent = run.agent
        turn.agent_call = run.call
        turn.parent_notice = lambda message, details: (run.events.request(details), {'delivery': 'retained'})[1]
        turn.record_process = lambda pid, outcome=None: (
            run.events.process(runtime_pid=pid) if outcome is None else run.events.process(runtime_exit=outcome))
        if notice:
            turn.unseen.append({'text': prompt, 'notice': notice})

        def receive(event: dict) -> bool | str:
            """Queue into the running turn, or continue the retained Session when nothing reached it."""
            if event['parent'] != run.agent or not event['session']:
                return False
            # Held notices are flushed when start returns, before Pi accepted the prompt.
            turn.accepted.wait(RPC_SECONDS)
            return (turn.send_notice(framed(event), event)
                    or run.events.continue_from_child(event, run.archive))

        run.events.receive_children(receive)
        with self.lock:
            self.turns[run.execution] = turn

        def return_unseen() -> None:
            """Hold again what a stopped turn dropped from Pi's queue, before the end's flush looks."""
            for dropped in turn.take_unseen():
                run.events.return_notice(dropped)

        def own() -> None:
            """Report native completion only after confirmed native settlement."""
            try:
                result = turn.run()
                # A completed turn settled with Pi's queue empty: nothing was dropped.
                if result['outcome'] != 'completed':
                    return_unseen()
                run.events.finished(result['outcome'], last_message=result.get('last_agent_message'))
            except Exception as error:
                failure.append(error)
                return_unseen()
                run.events.finished('failed', error={
                    'code': getattr(error, 'code', 'RUNTIME_WORKER_FAILED'), 'message': str(error),
                }, terminal_confirmed=getattr(error, 'terminal_confirmed', False))
            finally:
                with self.lock:
                    self.turns.pop(run.execution, None)
                turn.accepted.set()
                ready.set()

        # The owning process must keep an execution's owner until it reports its end,
        # whichever thread called create: a daemon caller would make it daemon too.
        threading.Thread(target=own, name=f'pi-{run.agent}', daemon=False).start()
        ready.wait()
        if failure:
            error = failure[0]
            raise AgentsError(getattr(error, 'code', 'RUNTIME_WORKER_FAILED'), str(error))

    def _operate(self, run: Run, **request: Any) -> dict:
        """Apply one native operation to this process's owner of ``run``."""
        with self.lock:
            turn = self.turns.get(run.execution)
        if turn is None:
            raise AgentsError('not-owned', 'This host does not own the Pi execution.')
        try:
            return turn.operate({**request, 'session': turn.session, 'execution_id': turn.execution_id})
        except RuntimeAdapterError as error:
            raise AgentsError(error.code, error.message) from None

    def deliver(self, run: Run, message: str) -> None:
        """Queue input into the same active native Session."""
        self._operate(run, operation='send', instruction=message)

    def interrupt(self, run: Run) -> bool:
        """Request native cancellation; the owner confirms actual quiescence."""
        with self.lock:
            turn = self.turns.get(run.execution)
        if turn is None:
            raise AgentsError('not-owned', 'This host does not own the Pi execution.')
        return turn.terminate()

    def requests(self, run: Run) -> list[dict]:
        """Expose pending native dialogs without answering them."""
        return self._operate(run, operation='requests')['requests']

    def reply(self, run: Run, request: Mapping[str, Any], response: Mapping[str, Any]) -> dict:
        """Return one explicit reply through the native dialog binding."""
        return self._operate(run, operation='reply', request_token=request.get('request_token'),
                             response=dict(response))
