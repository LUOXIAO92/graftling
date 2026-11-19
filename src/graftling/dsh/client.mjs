/** Official browser module: incoming agent-message attribution; never submits model input. */
window.__ModuleLoader__.load({
  id: 'graftling-dsh',
  factory(require) {
    const { createElement: h } = require('react');

    return {
      inject: ['slots'],
      apply(ctx) {
        // agents calls keep DSH's native Tool card; this module adds no tool view (527).
        ctx.slots.inject('conversation.chat.node', () => {
          // The installed Chat package owns the body, disclosure and timestamp.
          // Its public slot entry lets us add attribution without copying them.
          let disposeView;
          const install = () => {
            if (disposeView) return;
            const native = ctx.slots.entriesOfSlot('conversation.chat.node')
              .find(entry => entry.options.key === 'turn-trigger');
            if (!native) return;
            const NativeTrigger = native.component;

            function IncomingAgentMessage(props) {
              const source = props.node.data.source;
              const original = h(NativeTrigger, props);
              if (source?.kind !== 'agent-message' || !['communicationId', 'senderSessionId',
                'senderAgentId', 'recipientAgentId'].every(key =>
                typeof source[key] === 'string' && source[key].length > 0)) return original;
              const field = (label, agent, session) => h('div', null,
                h('dt', { style: { fontSize: 12, opacity: 0.7 } }, label),
                h('dd', { style: { margin: 0, overflowWrap: 'anywhere' } },
                  h('div', null, agent), h('div', null, session)));
              return h('section', { 'data-agent-incoming': source.communicationId },
                h('dl', { style: { margin: '8px 0', display: 'grid', gap: 8 } },
                  field('From Agent / Session', source.senderAgentId, source.senderSessionId),
                  field('To Agent / Session', source.recipientAgentId, props.sessionId)),
                original);
            }

            disposeView = ctx.slots.register({ name: 'conversation.chat.node', key: 'turn-trigger',
              priority: (native.options.priority ?? 0) - 1, locale: native.locale,
            }, IncomingAgentMessage);
          };
          // Slot declaration precedes native entry registration during web boot.
          const unsubscribe = ctx.slots.subscribe('conversation.chat.node', install);
          install();
          return () => { unsubscribe(); disposeView?.(); };
        });
      },
    };
  },
});
