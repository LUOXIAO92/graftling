"""How the Claude Code Main shows agents traffic: who, and the content as Markdown (533).

Node runs the host mod against a stub ``$``; the real screen is checked by hand.
"""
import json
from pathlib import Path
import re
import subprocess

import graftling
from graftling import notice_text

MODULE = Path(graftling.__file__).resolve().parent / 'claude' / 'host_context.mjs'
REPLY = '## Result\n\n- item `x`\n\n> a "quote"'
NOTICE = notice_text({'alias': 'n533', 'event': 'completed', 'execution': 'exec-1',
                      'message': 'Session execution ended:\n' + json.dumps({
                          'session': 's', 'execution_id': 'exec-1', 'outcome': 'completed',
                          'permissions': {'requested': 'auto'}, 'last_agent_message': REPLY})})
REQUEST = notice_text({'alias': 'w@e', 'event': 'claude/can_use_tool', 'execution': 'exec-2', 'request': 'r',
                       'message': 'Claude requires a native permission reply:\n' + json.dumps({
                           'execution_id': 'exec-2', 'request_id': 'req-9', 'request_token': 'tok-7',
                           'params': {'tool_name': 'Bash'}})})

PROGRAM = r'''
import assert from 'node:assert/strict';
const [module, scenario, notice, request] = process.argv.slice(1);
const {register} = await import(module);
const handlers = new Map();
register((name, matcher, fn) => {
  const key = !fn ? name : matcher.tool ? `${name}:${matcher.tool}` : `${name}:${matcher.component}`;
  handlers.set(key, fn ?? matcher); return {catch() {}};
});
const log = [], placed = [], submitted = [];
// Elements record their props, so the test reads the drawn tree.
const element = (type) => (props) => ({type, ...props});
const elements = {Box: element('Box'), Text: element('Text'), Markdown: element('Markdown')};
const host = {
  plugin: {name: 'graftling-host'},
  session: {id: async () => 'main-session', cwd: async () => '/p'},
  command: {register: async () => {}},
  env: {get: async (key) => key.endsWith('PYTHON') ? '/py' : '/cfg'},
  process: {run: async (argv, options) => ({exitCode: 0, stderr: '',
    stdout: JSON.stringify(JSON.parse(options.stdin).operation === 'notices'
      ? {notices: [{id: 'n1', event: {alias: 'n533'}, text: notice}], position: 0} : {})})},
  clock: {setTimeout: () => {}},
  prompt: {submit: async (value) => submitted.push(value)},
  ui: {log: async (value) => log.push(value), resolve: () => elements,
       place: scenario === 'unplaced' ? async () => ({isPlaced: false, reason: 'no attached surface'})
         : async ({id}) => { placed.push(id); return {isPlaced: true, requestId: id}; }},
};
const flat = (node) => node == null ? '' : typeof node === 'string' ? node
  : [node.type === 'Markdown' ? `<md>${node.text}</md>` : '', ...(node.children ?? []).map(flat)].join('');
const tool = 'tool.call:mcp__graftling__subagents';
const message = '# Plan\n\n1. read `a.md`\n2. reply';
const document = {agent: 'agent-1', execution: 'exec-1', result: {name: 'n533', last_result: {id: 'exec-1', last_message: '**done**'}}};
await handlers.get(tool)(host, {tool: 'mcp__graftling__subagents', action: 'execute', feature: 'send',
  arguments: {agent: 'agent-1', message}}, async () => ({result: [{type: 'text', text: JSON.stringify(document)}]}));
if (scenario === 'unplaced') {
  // Where no row can be drawn, the old one-line-per-row log remains.
  assert.ok(log.includes('┌ subagents · main-session → n533') && log.includes('│ # Plan') && log.includes('│ **done**'));
  console.log('ok'); process.exit(0);
}
assert.deepEqual(log, []);
assert.equal(placed.length, 2);
assert.ok(placed.every((id) => /^[A-Za-z0-9_-]{1,64}$/.test(id)));
const render = handlers.get('ui.render:Placed');
const sent = flat(await render(host, {component: 'Placed', props: {plugin: 'graftling-host', id: placed[0]}}, async () => 'theirs'));
assert.equal(sent, `subagents → n533<md>${message}</md>`);
const reply = flat(await render(host, {component: 'Placed', props: {plugin: 'graftling-host', id: placed[1]}}, async () => 'theirs'));
assert.equal(reply, 'n533 · replied<md>**done**</md>');
// A row this process never placed (another mod's) is left alone.
assert.equal(await render(host, {component: 'Placed', props: {plugin: 'other', id: 'build-status'}}, async () => 'theirs'), 'theirs');
// The pushed notice: Claude gets the shared text; the user sees who, Markdown, facts.
const timers = [];
host.clock.setTimeout = (fn) => timers.push(fn);
await handlers.get('session.start')(host, {}, async () => {});
await timers[0]();
assert.deepEqual(submitted, [{text: notice}]);
const user = handlers.get('ui.render:UserMessage');
const both = notice + '\n\n' + request;
const drawn = await user(host, {component: 'UserMessage', props: {text: both, origin: {kind: 'plugin'}}}, async () => 'theirs');
const text = flat(drawn);
assert.ok(text.startsWith('n533 · completed<md>## Result\n\n- item `x`\n\n> a "quote"</md>execution_id `exec-1` · outcome `completed`'), text);
assert.ok(text.includes('w@e · needs a permission reply<md>Claude requires a native permission reply'), text);
assert.ok(text.endsWith('execution_id `exec-2` · request_id `req-9` · request_token `tok-7`'), text);
assert.ok(!text.includes('permissions') && !text.includes('\\n'));
// A prompt without a notice title is Claude Code's drawing.
assert.equal(await user(host, {component: 'UserMessage', props: {text: 'hello', origin: {kind: 'user'}}}, async () => 'theirs'), 'theirs');
console.log('ok');
'''


def run(scenario: str) -> subprocess.CompletedProcess:
    return subprocess.run(['node', '--input-type=module', '-e', PROGRAM, MODULE.as_uri(), scenario, NOTICE, REQUEST],
                          capture_output=True, text=True, timeout=20)


def test_sent_and_received_messages_are_placed_rows_with_markdown() -> None:
    result = run('placed')
    assert result.returncode == 0, result.stderr


def test_mod_reads_no_host_noun_as_a_value() -> None:
    """Claude Code's validator refuses ``$.noun`` read as a value (``$.plugin.name``); only calls pass."""
    assert re.findall(r'\$\.[A-Za-z_]+\b(?!\.[A-Za-z_]+\()', MODULE.read_text()) == []


def test_without_placed_rows_the_log_fallback_remains() -> None:
    result = run('unplaced')
    assert result.returncode == 0, result.stderr
