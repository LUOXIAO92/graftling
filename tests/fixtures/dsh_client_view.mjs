/** Pure browser-module projection: no host/model calls and no UI acceptance claim. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const [clientPath, nativePackage] = process.argv.slice(2);
const require = createRequire(nativePackage);
const { SlotCore } = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-client-ui-slots')));
let module;
vm.runInNewContext(readFileSync(clientPath, 'utf8'), { window: { __ModuleLoader__: {
  load(definition) { module = definition; },
} } });
assert.equal(module.id, 'graftling-dsh');
function MarkdownText(props) { return props.text; }
const views = module.factory(name => {
  // react, and DSH's Markdown view from the primitives its Chat package uses (538)
  if (name === '@deepseek-ai/dsh-client-ui-primitives') return { MarkdownText };
  assert.equal(name, 'react');
  return { createElement: (tag, props, ...children) => ({ tag, props, children }) };
});
const slots = new SlotCore();
slots.register({ name: 'root', children: {
  'tool.call.toolview': { kind: 'keyed', scope: 'session' },
  'conversation.chat.node': { kind: 'keyed', scope: 'session' },
} }, () => null);
function NativeTrigger(props) { return props.node.data.content; }
const registerNative = () => slots.register({ name: 'conversation.chat.node', key: 'turn-trigger', locale: 'chat' }, NativeTrigger);
const delayedNative = process.argv[4] === 'delayed';
if (!delayedNative) registerNative();
views.apply({ slots: {
  inject(name, callback) { callback(); },
  entriesOfSlot: name => slots.entriesOfSlot(name),
  subscribe: (name, callback) => slots.subscribe(name, callback),
  register: (options, component) => slots.register(options, component),
} });
if (delayedNative) {
  registerNative();
  await new Promise(resolve => queueMicrotask(resolve));
}
// agents calls, under graftling's server or the name Sessions recorded before 0.6.0,
// keep DSH's native Tool card: graftling adds no tool view of its own (527).
assert.deepEqual(slots.entriesOfSlot('tool.call.toolview'), []);
console.log('Native Tool card: no tool.call.toolview entry for agents calls');

// Real native slot selection keeps the original renderer under our attribution.
const incoming = slots.entriesOfSlot('conversation.chat.node')
  .find(entry => entry.options.key === 'turn-trigger');
assert.notEqual(incoming.component, NativeTrigger);
assert.equal(incoming.locale, 'chat');
const source = Object.freeze({ kind: 'agent-message', form: 'relay',
  senderSessionId: 'actual-pi-session', senderAgentId: 'actual-pi-agent',
  recipientAgentId: 'actual-parent-agent', communicationId: 'actual-child-execution' });
const node = Object.freeze({ kind: 'turn-trigger', data: Object.freeze({
  source, seq: 47, time: 12345,
  content: Object.freeze([{ type: 'text', text: 'native reply body' }]),
}) });
const props = Object.freeze({ node, sessionId: 'actual-parent-session' });
const retained = JSON.stringify(props);
const received = incoming.component(props);
assert.equal(received.props['data-agent-incoming'], source.communicationId);
assert.equal(received.children.at(-1).tag, NativeTrigger);
assert.equal(received.children.at(-1).props, props);
const presentation = JSON.stringify(received);
for (const value of [source.senderSessionId, source.senderAgentId,
  source.recipientAgentId, props.sessionId]) assert.ok(presentation.includes(value));
assert.equal(presentation.match(/native reply body/g).length, 1);
assert.equal(JSON.stringify(incoming.component(props)), presentation);
assert.equal(JSON.stringify(props), retained);
for (const other of [{ kind: 'goal' }, { kind: 'agent-message', senderSessionId: 'runtime-only' }]) {
  const unchanged = { ...props, node: { ...node, data: { ...node.data, source: other } } };
  const original = incoming.component(unchanged);
  assert.equal(original.tag, NativeTrigger);
  assert.equal(original.props, unchanged);
}
console.log('Native keyed slot: true sender/recipient, one unchanged body, other triggers delegated');

// 538: a GraphTraj/Graftling notice relayed from the member to this conversation: who · event over
// the Markdown content (DSH's MarkdownText), no title or Facts line, and each value once: the sender's
// equal agent/session ids and a recipient that is this conversation are not printed.
const noticeText = '[Graftling notice from agent-abc · completed · execution exec-1]\n\n'
  + '## Result 538\n\n- alpha\n- beta\n\n```\necho 538\n```\n\nFacts: execution_id `exec-1` · outcome `completed`';
const noticeSource = Object.freeze({ kind: 'agent-message', form: 'relay', senderSessionId: 'agent-abc',
  senderAgentId: 'agent-abc', recipientAgentId: 'main-session', communicationId: 'graphtraj-notice:main-session:2:0' });
const noticeProps = Object.freeze({ sessionId: 'main-session', t: key => `t:${key}`, node: Object.freeze({ kind: 'turn-trigger',
  data: Object.freeze({ source: noticeSource, seq: 48, time: 12346, content: Object.freeze([{ type: 'text', text: noticeText }]) }) }) });
const shown = incoming.component(noticeProps);
assert.equal(shown.children.at(-1).tag, NativeTrigger);       // the model's text stays in DSH's card
assert.equal(shown.children.at(-1).props, noticeProps);
const ours = JSON.stringify(shown.children.slice(0, -1));
assert.equal(ours.match(/agent-abc/g).length, 1);
assert.ok(ours.includes('agent-abc · completed'));
assert.ok(!/From|To Agent|main-session|Facts|exec-1|Graftling notice/.test(ours), ours);
const md = shown.children[0].children[1].children[0];
assert.equal(md.tag, MarkdownText);
assert.equal(md.props.text, '## Result 538\n\n- alpha\n- beta\n\n```\necho 538\n```');
assert.equal(md.props.labels.code.copyLabel, 't:copy');
// A plain relay whose agent and session ids are equal: 'From' and the value once.
const plain = incoming.component({ ...noticeProps, node: { ...noticeProps.node, data: { ...noticeProps.node.data,
  content: [{ type: 'text', text: 'plain message' }] } } });
const plainOurs = JSON.stringify(plain.children.slice(0, -1));
assert.equal(plainOurs.match(/agent-abc/g).length, 1);
assert.ok(plainOurs.includes('"From"') && !plainOurs.includes('main-session'), plainOurs);
console.log('Notice: who · event over Markdown; each value once; the native card keeps the model text');
