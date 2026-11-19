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
const views = module.factory(name => {
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
