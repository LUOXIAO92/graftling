"""Real Claude Code as graftling's host, without a model or a login (652).

A local stub of the Messages API (127.0.0.1, a dummy token) answers Claude Code with
scripted tool calls, so real Claude Code makes the real call and its result shows in
its stream-json output. Claude Code's configuration, home and working directories are
temporary; every credential-class variable is removed from its environment.

    python claude_real_entry.py --claude EXE --python PY --work DIR --out DIR [--cases market,untrusted,caller]

Cases (graftling installed for PY; on POSIX PY must be graftling's own Python):
  market     the marketplace plugin, added from PY's installation and installed at project
             scope, one print-mode turn. Native Windows: the init event lists the plugin, its
             server is not connected, and the module says the plugin is unsupported and names
             configuration --write (a ui_log event and a host.log line). Elsewhere: the server
             is connected and nothing names Windows (unchanged behaviour).
  untrusted  configuration --write in a folder whose trust was not accepted; the model calls
             subagents. The call is refused naming the plugin and the folder trust; host.log
             has that line.
  caller     the same in a trusted folder; the model runs the context step with its Bash tool
             (refused as a command under this same Claude Code, with its pid), then calls
             subagents (answered: the plugin is loaded).
Writes OUT/<case>/ (stdout, stderr, debug lines, host.log, the stub's request summary) and
OUT/results.json; prints one ITEM line per check and a last RESULT line; exits 0 when every
item passes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL = 'mcp__graftling__subagents'
DUMMY = 'dummy-not-a-key-652'
CREDENTIAL = re.compile(r'(KEY|TOKEN|SECRET|SECRETS|PASSWORD|CREDENTIAL|CREDENTIALS)$')
PROVIDER = re.compile(r'^(ANTHROPIC_|CLAUDE|OPENAI_|DEEPSEEK_|CODEX_|AWS_|GOOGLE_|AZURE_|GRAFTLING_|ACTIONS_)|_BASE_URL$')


# --- the Messages API stub ---------------------------------------------------------------------

class Stub:
    """Answers each turn of the main conversation with the next scripted tool call, then with text.

    ``steps`` are (tool name, input) pairs. A request offering no tools (a side request such as a
    title) gets text. A step whose tool is not offered while ToolSearch is gets a ToolSearch call
    for it first. Every request is summarised in ``requests``.
    """

    def __init__(self, steps: list[tuple[str, dict]]) -> None:
        self.steps    = steps
        self.requests = []
        self.lock     = threading.Lock()

    def answer(self, body: dict) -> list[dict]:
        """Return the content blocks of the reply to one request."""
        tools = [tool.get('name') for tool in body.get('tools') or []]
        used  = [block for message in body.get('messages') or [] if message.get('role') == 'assistant'
                 for block in (message.get('content') if isinstance(message.get('content'), list) else [])
                 if block.get('type') == 'tool_use']
        done  = sum(1 for block in used if block.get('name') != 'ToolSearch')
        searched = {block.get('input', {}).get('query') for block in used if block.get('name') == 'ToolSearch'}
        reply = [{'type': 'text', 'text': 'DONE-652'}]
        if tools and done < len(self.steps):
            name, arguments = self.steps[done]
            reply = [{'type': 'tool_use', 'id': f'toolu_652_{len(used)}', 'name': name, 'input': arguments}]
            query = f'select:{name}'
            if name not in tools and 'ToolSearch' in tools and query not in searched:
                reply = [{'type': 'tool_use', 'id': f'toolu_652_{len(used)}', 'name': 'ToolSearch',
                          'input': {'query': query, 'max_results': 1}}]
        with self.lock:
            self.requests.append({'tools': len(tools), 'offered': sorted(set(tools) & {name for name, _ in self.steps}),
                                  'tool_uses_before': len(used), 'reply': [block.get('name', 'text') for block in reply]})
        return reply


def serve(stub: Stub) -> ThreadingHTTPServer:
    """Start the stub on 127.0.0.1 at a free port, in a daemon thread."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *arguments) -> None:
            """Keep the console for the items."""

        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self.send(404, b'{"type":"error","error":{"type":"not_found_error","message":"stub"}}', 'application/json')

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            path = self.path.split('?')[0]
            if path.endswith('/count_tokens'):
                return self.send(200, b'{"input_tokens": 1}', 'application/json')
            if not path.endswith('/v1/messages'):
                return self.do_GET()
            content = stub.answer(body)
            stop = 'tool_use' if content[0]['type'] == 'tool_use' else 'end_turn'
            message = {'id': 'msg_652', 'type': 'message', 'role': 'assistant', 'model': body.get('model', 'stub'),
                       'content': content, 'stop_reason': stop, 'stop_sequence': None,
                       'usage': {'input_tokens': 1, 'output_tokens': 1}}
            if not body.get('stream'):
                return self.send(200, json.dumps(message).encode(), 'application/json')
            events = [('message_start', {'type': 'message_start', 'message': {**message, 'content': [], 'stop_reason': None}})]
            for index, block in enumerate(content):
                if block['type'] == 'tool_use':
                    events += [('content_block_start', {'type': 'content_block_start', 'index': index,
                                                        'content_block': {**block, 'input': {}}}),
                               ('content_block_delta', {'type': 'content_block_delta', 'index': index, 'delta': {
                                   'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])}})]
                else:
                    events += [('content_block_start', {'type': 'content_block_start', 'index': index,
                                                        'content_block': {'type': 'text', 'text': ''}}),
                               ('content_block_delta', {'type': 'content_block_delta', 'index': index,
                                                        'delta': {'type': 'text_delta', 'text': block['text']}})]
                events.append(('content_block_stop', {'type': 'content_block_stop', 'index': index}))
            events += [('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None},
                                          'usage': {'output_tokens': 1}}),
                       ('message_stop', {'type': 'message_stop'})]
            text = ''.join(f'event: {name}\ndata: {json.dumps(data)}\n\n' for name, data in events)
            self.send(200, text.encode(), 'text/event-stream')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --- Claude Code runs ---------------------------------------------------------------------------

def environment(work: Path, url: str, extra: dict | None = None) -> dict:
    """This environment without credentials or provider settings; temporary home and Claude config; the stub."""
    kept = {name: value for name, value in os.environ.items()
            if not any(CREDENTIAL.search(part) for part in name.upper().split('_')) and not PROVIDER.search(name.upper())}
    home = work / 'home'
    home.mkdir(parents=True, exist_ok=True)
    kept.update({'HOME': str(home), 'CLAUDE_CONFIG_DIR': str(work / 'config'), 'ANTHROPIC_BASE_URL': url,
                 'ANTHROPIC_AUTH_TOKEN': DUMMY, 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
                 'DISABLE_AUTOUPDATER': '1', 'ENABLE_TOOL_SEARCH': 'false', 'PYTHONUTF8': '1'})
    if os.name == 'nt':
        kept['USERPROFILE'] = str(home)
    kept.update(extra or {})
    return kept


def claude(arguments: list[str], cwd: Path, env: dict, out: Path, label: str, bound: int = 240) -> dict:
    """Run Claude Code once; keep its stdout and stderr; return its exit and stream-json events."""
    try:
        done = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=bound, stdin=subprocess.DEVNULL)
        code, stdout, stderr = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as expired:
        code = 'timeout'
        stdout = expired.stdout.decode('utf-8', 'replace') if isinstance(expired.stdout, bytes) else expired.stdout or ''
        stderr = expired.stderr.decode('utf-8', 'replace') if isinstance(expired.stderr, bytes) else expired.stderr or ''
    (out / f'{label}-stdout.jsonl').write_text(stdout, encoding='utf-8')
    (out / f'{label}-stderr.txt').write_text(stderr, encoding='utf-8')
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return {'exit': code, 'events': events, 'stderr': stderr}


def init_event(run: dict) -> dict:
    return next((event for event in run['events'] if event.get('type') == 'system' and event.get('subtype') == 'init'), {})


def tool_results(run: dict) -> list[dict]:
    """Every tool result in the stream: {'tool': name, 'error': bool, 'text': text}."""
    names = {}
    found = []
    for event in run['events']:
        content = (event.get('message') or {}).get('content')
        for block in content if isinstance(content, list) else []:
            if block.get('type') == 'tool_use':
                names[block.get('id')] = block.get('name')
            if block.get('type') == 'tool_result':
                inner = block.get('content')
                text = inner if isinstance(inner, str) else '\n'.join(
                    part.get('text', '') for part in inner or [] if isinstance(part, dict))
                found.append({'tool': names.get(block.get('tool_use_id')), 'error': bool(block.get('is_error')),
                              'text': text})
    return found


def debug_lines(path: Path, *words: str) -> list[str]:
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    return [line for line in text.splitlines() if any(word in line for word in words)]


def read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def trust(work: Path, project: Path) -> list[str]:
    """Record the folder trust as the trust dialog does: projects[<path>].hasTrustDialogAccepted in .claude.json."""
    config = work / 'config' / '.claude.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    document = json.loads(read(config) or '{}')
    keys = list(dict.fromkeys([str(project), project.as_posix(), str(project.resolve()), project.resolve().as_posix()]))
    for key in keys:
        document.setdefault('projects', {}).setdefault(key, {})['hasTrustDialogAccepted'] = True
    config.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    return keys


class Items:
    """PASS/FAIL items, printed as they are judged."""

    def __init__(self) -> None:
        self.items = []

    def add(self, case: str, name: str, ok: bool, observable: str) -> None:
        verdict = 'PASS' if ok else 'FAIL'
        self.items.append({'case': case, 'item': name, 'verdict': verdict, 'observable': observable})
        print(f'ITEM {case} {name}: {verdict} {observable}', flush=True)


def short(text: str, size: int = 300) -> str:
    text = ' '.join(str(text).split())
    return text if len(text) <= size else text[:size] + '...'


def configure(options: argparse.Namespace, project: Path, out: Path) -> Path:
    """configuration --write for ``project`` with graftling's Python; returns the host configuration."""
    project.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([options.python, '-m', 'graftling.claude.host', 'configuration', '--project', str(project),
                           '--claude', options.claude, '--write'], cwd=project, capture_output=True, text=True,
                          encoding='utf-8', timeout=120)
    (out / 'configuration-write.txt').write_text(done.stdout + done.stderr, encoding='utf-8')
    if done.returncode != 0:
        raise SystemExit(f'configuration --write failed: {done.stderr}')
    return project / '.agents-control' / 'claude-host.json'


def print_args(prompt: str, debug: Path, *extra: str) -> list[str]:
    return ['-p', prompt, '--output-format', 'stream-json', '--verbose', '--max-turns', '6',
            '--debug-file', str(debug), *extra]


# --- cases --------------------------------------------------------------------------------------

def case_market(options: argparse.Namespace, items: Items, work: Path, out: Path) -> None:
    """The marketplace plugin, one print-mode turn with no tool call."""
    stub = Stub([])
    server = serve(stub)
    try:
        project = work / 'project'
        project.mkdir(parents=True)
        env = environment(work, f'http://127.0.0.1:{server.server_port}', {'GRAFTLING_PYTHON': options.python})
        package = subprocess.run([options.python, '-c', 'import graftling, pathlib; print(pathlib.Path(graftling.__file__).parent)'],
                                 capture_output=True, text=True, timeout=60).stdout.strip()
        steps = [subprocess.run([options.claude, *arguments], cwd=project, env=env, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=180)
                 for arguments in (['plugin', 'marketplace', 'add', package],
                                   ['plugin', 'install', 'graftling@graftling', '--scope', 'project'])]
        (out / 'plugin-install.txt').write_text('\n'.join(f'exit {step.returncode}\n{step.stdout}{step.stderr}'
                                                          for step in steps), encoding='utf-8')
        items.add('market', 'plugin installed', all(step.returncode == 0 for step in steps),
                  f'marketplace add exit {steps[0].returncode}; plugin install exit {steps[1].returncode}')
        debug = out / 'debug.txt'
        run = claude([options.claude, *print_args('Reply with exactly DONE-652.', debug)], project, env, out, 'claude')
        init = init_event(run)
        plugins = [f"{plugin.get('name')}@{plugin.get('version')}" for plugin in init.get('plugins') or []]
        servers = {server.get('name'): server.get('status') for server in init.get('mcp_servers') or []}
        status = servers.get('plugin:graftling:subagents')
        logged = [event.get('text', '') for event in run['events']
                  if event.get('type') == 'system' and event.get('subtype') == 'ui_log' and event.get('plugin') == 'graftling']
        hostlog = read(project / '.agents-control' / 'host.log')
        lines = debug_lines(debug, 'plugin:graftling:subagents', '(graftling)', 'graftling@graftling')
        (out / 'debug-graftling.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (out / 'host.log').write_text(hostlog, encoding='utf-8')
        facts = (f'exit {run["exit"]}; init plugins {plugins}; mcp_servers {servers}; graftling ui_log {logged}; '
                 f'host.log {short(hostlog)!r}; sh on PATH {shutil.which("sh", path=env.get("PATH"))}; '
                 f'debug lines naming the server {len(lines)}: '
                 f'{[short(line, 240) for line in lines if "plugin:graftling:subagents" in line][:6]}')
        listed = any(plugin.startswith('graftling@') for plugin in plugins)
        if os.name == 'nt':
            said = [text for text in logged if 'not supported on native Windows' in text and 'configuration --write' in text]
            items.add('market', 'R2 native Windows: unsupported, configuration --write named',
                      listed and status != 'connected' and bool(said) and said[0] in hostlog, facts)
        else:
            items.add('market', 'R2 POSIX: the plugin serves as before', listed and status == 'connected'
                      and not any('Windows' in text for text in logged) and 'native Windows' not in hostlog, facts)
    finally:
        server.shutdown()


def case_untrusted(options: argparse.Namespace, items: Items, work: Path, out: Path) -> None:
    """configuration --write, folder trust not accepted; the model calls subagents."""
    stub = Stub([(TOOL, {'action': 'discover'})])
    server = serve(stub)
    try:
        project = work / 'project'
        configure(options, project, out)
        env = environment(work, f'http://127.0.0.1:{server.server_port}')
        debug = out / 'debug.txt'
        run = claude([options.claude, *print_args('Call the subagents tool with action discover.', debug,
                                                  '--allowedTools', TOOL)], project, env, out, 'claude')
        init = init_event(run)
        plugins = [plugin.get('name') for plugin in init.get('plugins') or []]
        servers = {server.get('name'): server.get('status') for server in init.get('mcp_servers') or []}
        results = [result for result in tool_results(run) if result['tool'] == TOOL]
        hostlog = read(project / '.agents-control' / 'host.log')
        (out / 'host.log').write_text(hostlog, encoding='utf-8')
        text = results[0]['text'] if results else ''
        facts = (f'exit {run["exit"]}; init plugins {plugins}; mcp_servers {servers}; subagents results '
                 f'{len(results)}; first result {short(text, 700)!r}; host.log refusal lines '
                 f'{hostlog.count("subagents-call refused:")}; stub requests {stub.requests}')
        items.add('untrusted', 'R1 refusal names the plugin and folder trust',
                  'agents-claude-host' not in plugins and servers.get('graftling') == 'connected' and bool(results)
                  and 'authority-denied' in text and 'folder trust' in text and 'has not loaded it' in text
                  and hostlog.count('subagents-call refused:') == 1, facts)
    finally:
        server.shutdown()


def case_caller(options: argparse.Namespace, items: Items, work: Path, out: Path) -> None:
    """configuration --write, folder trusted; the Bash tool runs the context step, then the model calls subagents."""
    project = work / 'project'
    config = configure(options, project, out)
    prepare = project / 'prepare.json'
    prepare.write_text('{}\n', encoding='utf-8')
    command = (f'"{Path(options.python).as_posix()}" -m graftling.claude.host context --config "{config.as_posix()}" '
               f'< "{prepare.as_posix()}"; echo "exit=$?"')
    stub = Stub([('Bash', {'command': command, 'description': 'Run the context step from the Bash tool'}),
                 (TOOL, {'action': 'discover'})])
    server = serve(stub)
    try:
        keys = trust(work, project)
        env = environment(work, f'http://127.0.0.1:{server.server_port}')
        debug = out / 'debug.txt'
        run = claude([options.claude, *print_args('Run the context step, then call the subagents tool.', debug,
                                                  '--allowedTools', f'Bash {TOOL}')], project, env, out, 'claude')
        init = init_event(run)
        plugins = [plugin.get('name') for plugin in init.get('plugins') or []]
        results = tool_results(run)
        bash = next((result['text'] for result in results if result['tool'] == 'Bash'), '')
        called = next((result for result in results if result['tool'] == TOOL), None)
        (out / 'host.log').write_text(read(project / '.agents-control' / 'host.log'), encoding='utf-8')
        pid = re.search(r'Claude Code pid (\d+)', bash)
        items.add('caller', 'R3 Bash-tool context step: refused as the same session',
                  'not started by Claude Code itself' in bash and 'another Claude Code session' not in bash
                  and 'exit=1' in bash and pid is not None,
                  f'exit {run["exit"]}; trusted keys {len(keys)}; init plugins {plugins}; Bash result {short(bash, 700)!r}; '
                  f'stub requests {stub.requests}')
        items.add('caller', 'trusted folder: the plugin loaded and subagents answered',
                  'agents-claude-host' in plugins and called is not None and not called['error']
                  and 'authority-denied' not in called['text'],
                  f'subagents result {short(called["text"], 300) if called else None!r} '
                  f'(error {called["error"] if called else None})')
    finally:
        server.shutdown()


CASES = {'market': case_market, 'untrusted': case_untrusted, 'caller': case_caller}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--claude', required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--cases', default=','.join(CASES))
    options = parser.parse_args()
    items = Items()
    version = subprocess.run([options.claude, '--version'], capture_output=True, text=True, timeout=60).stdout.strip()
    graftling = subprocess.run([options.python, '-c', 'import graftling; print(graftling.__version__, graftling.__file__)'],
                               capture_output=True, text=True, timeout=60).stdout.strip()
    print(f'claude {version}; graftling {graftling}; platform {sys.platform}', flush=True)
    for case in options.cases.split(','):
        work, out = options.work / case, options.out / case
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        out.mkdir(parents=True, exist_ok=True)
        try:
            CASES[case](options, items, work, out)
        except Exception as error:  # one case's fault is its FAIL item; the others still run
            items.add(case, 'case ran', False, f'{type(error).__name__}: {short(str(error), 500)}')
    verdict = 'PASS' if items.items and all(item['verdict'] == 'PASS' for item in items.items) else 'FAIL'
    options.out.mkdir(parents=True, exist_ok=True)
    (options.out / 'results.json').write_text(json.dumps({'claude': version, 'graftling': graftling,
                                                          'platform': sys.platform, 'verdict': verdict,
                                                          'items': items.items}, indent=2) + '\n', encoding='utf-8')
    print(f'RESULT {verdict} {sum(item["verdict"] == "PASS" for item in items.items)} of {len(items.items)} items passed')
    sys.exit(0 if verdict == 'PASS' else 1)


if __name__ == '__main__':
    main()
