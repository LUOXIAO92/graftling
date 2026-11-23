"""Run captured Codex settings through the independent Agent control contract."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Callable, Mapping, Sequence

from graftling import AgentsError, Caller, Run
from graftling.control import require_available, within_workspace
from graftling.extra_tools import ExtraTool
from graftling.runtime import RuntimeAdapterError
from .context import restore_codex_context
from .managed_session import CodexManagedExecution


# Public calls may bind fresh consumer instances in the same owning process.
_TURNS: dict[str, CodexManagedExecution] = {}
_TURNS_LOCK = threading.Lock()

# Native Codex effort levels; a model's own list may be narrower.
EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh')


def _select(settings: dict, parameters: dict, selection: Mapping[str, Any]) -> None:
    """Apply one create selection to captured Codex settings in place.

    A selected connection replaces the host's native home, endpoint and
    credential reference together, so a key never reaches another endpoint.
    """
    connection = selection.get('settings')
    config = parameters['config']
    if connection is not None:
        settings['connection'] = {key: connection[name] for key, name in (
            ('runtime_home', 'home'), ('base_url', 'base_url'), ('api_key_env', 'api_key_env'))
            if name in connection}
        if 'provider' in connection:
            config['model_provider'] = connection['provider']
    if selection.get('model') is not None:
        parameters['model'] = selection['model']
    effort = selection.get('reasoning_effort')
    if effort is not None:
        if effort not in EFFORTS:
            raise AgentsError('invalid-reasoning-effort',
                              f'Codex takes reasoning_effort {list(EFFORTS)}, not {effort!r}.')
        config['model_reasoning_effort'] = effort
    review = selection.get('review')
    if review is not None:
        # With no request reaching the client the reviewer would never run.
        if 'never' in (parameters.get('approvalPolicy'), config.get('approval_policy')):
            raise AgentsError('invalid-config', "Codex approval_policy 'never' asks for no approval, so the "
                                                f"review plugin (reviewer {review['name']!r}) would never run.")
        settings['review'] = deepcopy(dict(review))
        parameters['approvalsReviewer'] = config['approvals_reviewer'] = 'user'


def owner_tools_kept(pairs: Sequence[ExtraTool], disallowed: Sequence[str] | None, server: str) -> list:
    """Return the owner's extra tools minus those a create disallowed, bare or as ``mcp__<server>__<name>``."""
    denied = set(disallowed or ())
    return [(descriptor, callback) for descriptor, callback in pairs
            if descriptor.get('name') not in denied and f"mcp__{server}__{descriptor.get('name')}" not in denied]


def apply_tools(config: dict, tools: Mapping[str, Any], *, own_server: str | None = None,
                own_tools: Sequence[str] = ()) -> None:
    """Apply a create's tool choice (551) to captured Codex config in place.

    Names of the owner's own tools (``own_tools``, or any ``mcp__<own_server>__*``)
    are left out here: the driver drops a disallowed one from the child's
    extra tools (:func:`owner_tools_kept`), so the child runs without it.

    Codex can express only ``web_search`` (config ``web_search``) and MCP
    tools ``mcp__<server>__<tool>`` (per-server ``enabled_tools`` and
    ``disabled_tools``); any other name is refused, since Codex has no native
    switch for one built-in tool (shell, apply_patch, ...), which stay as
    captured. An MCP tool of a server the captured config does not define is
    refused too: Codex rejects a server entry without a transport. Every
    refusal happens before anything is written. Narrowing applies directly. Enabling web search that the
    captured host config disables has no Codex approval route, so it is
    refused with ``authorization-required`` instead of being granted.
    """
    own = (lambda name: name in own_tools
           or (own_server is not None and name.startswith(f'mcp__{own_server}__')))
    allowed = None if tools.get('tools') is None else [name for name in tools['tools'] if not own(name)]
    denied = [name for name in tools.get('disallowed_tools') or () if not own(name)]
    named = [*(allowed or ()), *denied]
    mcp = {name: name[5:].split('__', 1) for name in named if name.startswith('mcp__') and name.count('__') >= 2}
    unknown = [name for name in named if name != 'web_search' and name not in mcp]
    if unknown:
        raise AgentsError('unavailable', f'Codex has no native per-tool switch for {sorted(set(unknown))}; '
                                         'it can allow or deny only web_search and mcp__<server>__<tool>.')
    servers = config.get('mcp_servers') or {}
    absent = sorted({server for server, _ in mcp.values() if not isinstance(servers.get(server), dict)})
    if absent:
        # Codex rejects a server entry without a transport ('invalid transport'),
        # so a tool filter is written only beside a server the captured config defines.
        raise AgentsError('unavailable', f'MCP servers {absent} are not in the captured Codex settings, so '
                                         'their tools cannot be allowed or denied for this child.')
    if 'web_search' in denied or (allowed is not None and 'web_search' not in allowed):
        config['web_search'] = 'disabled'
    elif allowed is not None and config.get('web_search') == 'disabled':
        raise AgentsError('authorization-required',
                          'web_search is disabled in the host Codex settings; Codex offers no approval to enable '
                          'it for a child, so it was not granted.')
    for name, (server, tool) in mcp.items():
        entry = servers[server]
        key = 'disabled_tools' if name in denied else 'enabled_tools'
        entry[key] = list(dict.fromkeys([*entry.get(key, ()), tool]))


def _receive_children(turn: CodexManagedExecution, run: Run) -> None:
    """Deliver a child's end or request once, as the Claude and DSH bindings do."""
    def receive(event: dict) -> bool:
        """Steer the running turn, or continue the retained Session when its input closed."""
        if event['parent'] != run.agent or not event['session']:
            return False
        try:
            turn.operate({'operation': 'send', 'session': turn.session, 'execution_id': turn.execution_id,
                          'instruction': f"[subagents ·{event['agent']} → {run.agent}]\n{event['body']}"})
            return True
        except RuntimeAdapterError as error:
            # These two are raised before any input is written.
            if error.code not in {'runtime-input-closed', 'operation-failed'}:
                raise
        return run.events.continue_from_child(event, run.archive)

    run.events.receive_children(receive)


class CodexDriver:
    """Own native Codex turns, using host-selected Run.settings.

    Settings contain the existing adapter_request, evidence and connection
    documents. The host resolves settings before binding this driver; no role,
    Git tree or product installation is consulted. A consumer may
    supply its existing execution subclass to bind optional archive/review policy.
    """

    def __init__(
        self,
        settings: Mapping[str, Any] | None = None,
        *,
        execution: Callable[..., CodexManagedExecution] = CodexManagedExecution,
        extra_tools: Callable[[Run], Sequence[ExtraTool]] | None = None,
    ) -> None:
        """Capture explicit native settings without starting a Runtime.

        ``extra_tools(run)`` returns the host's own tools for that Agent's
        child, served beside ``subagents`` (:mod:`graftling.extra_tools`).
        """
        self.settings = deepcopy(dict(settings)) if settings is not None else None
        self.execution = execution
        self.extra_tools = extra_tools
        # A consumer's execution binds its own review policy, not this plugin.
        self.supports_review = execution is CodexManagedExecution

    def prepare(self, runtime: str, caller: Caller, *, configuration: str | None = None,
                selection: Mapping[str, Any] | None = None, workspace: Path | None = None,
                tools: Mapping[str, Any] | None = None) -> dict:
        """Validate retained native values, with native auto review as the default.

        ``selection`` is the create's resolved connection, model and effort.
        ``workspace`` narrows the captured worktree to the child's cwd; the
        permission profiles, approval settings and network stay as captured.
        """
        if runtime != 'codex':
            raise AgentsError('unsupported-runtime', 'This driver runs Codex only.')
        if self.settings is None or configuration is not None:
            raise AgentsError('invalid-config', 'The host must supply captured Codex settings.')
        settings = deepcopy(self.settings)
        try:
            context = restore_codex_context(settings['adapter_request'], settings.get('evidence', {}))
            request = context.launch_document()['adapter_request']
            parameters = request['session_parameters']
            if workspace is not None:
                request['worktree_path'] = parameters['cwd'] = str(within_workspace(workspace, request['worktree_path']))
            config = parameters['config']
            if 'approvalsReviewer' not in parameters and 'approvals_reviewer' not in config:
                config['approvals_reviewer'] = 'auto_review'
            config['features'] = {**config.get('features', {}), 'exec_permission_approvals': True}
            if selection is not None:
                _select(settings, parameters, selection)
            if tools is not None:
                apply_tools(config, tools, own_server=self.execution.mcp_server)
                # The driver drops a disallowed owner tool at start (owner_tools_kept).
                settings['disallowed_tools'] = list(tools.get('disallowed_tools') or ())
            settings['adapter_request'] = request
            settings.setdefault('evidence', {})
        except (KeyError, RuntimeAdapterError) as error:
            raise AgentsError('invalid-config', str(error)) from error
        return settings

    def require_available(self, connection: Mapping[str, Any]) -> None:
        """Refuse a connection whose Codex is not installed or not logged in."""
        home = Path(connection.get('home') or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        require_available(connection, executable=shutil.which('codex'), home=home, logins=('auth.json',))

    def start(self, run: Run) -> None:
        """Return at native startup while one owner retains execution until close."""
        ready = threading.Event()
        failure: list[Exception] = []

        def created(session: str, pid: int) -> None:
            """Record native identity before any model input can run."""
            run.events.session(session)
            run.events.process(runtime_pid=pid)

        options = {'connection': run.settings.get('connection', {}), 'agent_call': run.call,
                   'archive': run.archive, 'extra_fields': run.extra_fields}
        prompt = run.message
        if self.execution is not CodexManagedExecution:
            options.update(independent=True)
        elif 'completion_message' in run.settings:
            # The child's end that continues this Session is the turn's input.
            notice = run.settings['completion_message']
            prompt = f"[subagents ·{notice['agent']} → {run.agent}]\n{notice['body']}"
        if run.settings.get('review') is not None:
            options.update(review=run.settings['review'])
        if self.extra_tools is not None:
            # An explicit disallow removes the owner's tool; the child runs without it (551).
            options.update(extra_tools=owner_tools_kept(self.extra_tools(run), run.settings.get('disallowed_tools'),
                                                        self.execution.mcp_server))
        request = dict(run.settings['adapter_request'])
        if run.settings.get('pass_env'):
            request['pass_env'] = run.settings['pass_env']
        turn = self.execution(request, prompt, run.directory,
                              lambda session, pid: ready.set(), dict(run.settings.get('evidence', {})),
                              trace_file=run.directory / 'trace.jsonl', expected_session=run.session,
                              session_created=created, **options)
        if getattr(turn, 'member_marker', None):
            # Reconciliation stops this execution's escaped tools if its owner dies:
            # by marker and by the descendants recorded in this file (457, 459, 462).
            facts = {'tool_marker': turn.member_marker}
            if getattr(turn, 'tool_descendants', None) is not None:
                facts['tool_descendants'] = str(Path(turn.tool_descendants).resolve())
            run.events.process(**facts)
        turn.parent_notice = lambda message, details: (run.events.request(details), {'delivery': 'retained'})[1]
        if self.execution is CodexManagedExecution:
            _receive_children(turn, run)
        with _TURNS_LOCK:
            _TURNS[run.execution] = turn

        def own() -> None:
            """Report native completion only after the owning connection closes."""
            try:
                result = turn.run()
                run.events.finished(result['outcome'], last_message=result.get('last_agent_message'),
                                    error=result.get('error'),
                                    terminal_confirmed=result.get('terminal_confirmed', True))
            except Exception as error:
                failure.append(error)
                run.events.finished('failed', error={
                    'code': getattr(error, 'code', 'RUNTIME_WORKER_FAILED'), 'message': str(error),
                }, terminal_confirmed=getattr(error, 'terminal_confirmed', False))
            finally:
                with _TURNS_LOCK:
                    _TURNS.pop(run.execution, None)
                ready.set()

        # The owning process must keep an execution's owner until it reports its end,
        # whichever thread called create: a daemon caller would make it daemon too.
        threading.Thread(target=own, name=f'codex-{run.agent}', daemon=False).start()
        ready.wait()
        if failure:
            error = failure[0]
            raise AgentsError(getattr(error, 'code', 'RUNTIME_WORKER_FAILED'), str(error))

    def _turn(self, run: Run) -> CodexManagedExecution:
        """Return only this process's native owner for the given execution."""
        with _TURNS_LOCK:
            turn = _TURNS.get(run.execution)
        if turn is None:
            raise AgentsError('not-owned', 'This process does not own the native execution.')
        return turn

    def _operate(self, run: Run, **request: Any) -> dict:
        """Target the exact native Session and turn retained by the owner."""
        turn = self._turn(run)
        try:
            return turn.operate({**request, 'session': turn.session, 'execution_id': turn.execution_id})
        except RuntimeAdapterError as error:
            refused = AgentsError(error.code, error.message)
            refused.finishing = error.finishing
            raise refused from error

    def deliver(self, run: Run, message: str) -> None:
        """Deliver ordinary text into the active native turn."""
        self._operate(run, operation='send', instruction=message)

    def interrupt(self, run: Run) -> bool:
        """Request interruption, leaving terminal confirmation to the owner."""
        return self._turn(run).terminate()

    def requests(self, run: Run) -> list[dict]:
        """Return pending native requests without answering them."""
        return self._operate(run, operation='requests')['requests']

    def reply(self, run: Run, request: Mapping[str, Any], response: Mapping[str, Any]) -> dict:
        """Send one explicit response to the retained native request."""
        return self._operate(run, operation='reply', request_token=request.get('request_token'),
                             response=dict(response))
