/** Official browser module: incoming agent-message attribution; never submits model input. */
window.__ModuleLoader__.load({
  id: 'graftling-dsh',
  factory(require) {
    const { createElement: h } = require('react');
    // DSH's own Markdown view, as its Chat package uses it; plain text when this DSH has none.
    let MarkdownText = null;
    try { ({ MarkdownText } = require('@deepseek-ai/dsh-client-ui-primitives')); } catch { MarkdownText = null; }
    // notice_text's first line; its last paragraph names the facts the model acts on (538).
    const TITLE = /^\[(?:Graftling|GraphTraj) notice from (.+?) · ([^\]]+?)(?: · execution \S+)?\]$/;

    /** A notice as who, event and its Markdown content, or null for any other message. */
    function noticeParts(content) {
      const text = (Array.isArray(content) ? content : []).filter(block => block?.type === 'text')
        .map(block => block.text).join('\n');
      const [title, ...rest] = text.split('\n\n');
      const match = TITLE.exec(title ?? '');
      if (!match) return null;
      if (rest.length && rest.at(-1).startsWith('Facts: ')) rest.pop();
      return { who: match[1], event: match[2], body: rest.join('\n\n') };
    }

    function markdown(text, t) {
      if (!MarkdownText) return h('div', { style: { whiteSpace: 'pre-wrap' } }, text);
      const label = key => (typeof t === 'function' ? t(key) : key);
      return h(MarkdownText, { text, labels: {
        code: { copyLabel: label('copy'), copiedLabel: label('copied'), toolbarLabels: {
          codeLabel: label('codeBlock.title'), wrapLabel: label('codeBlock.wrap'), unwrapLabel: label('codeBlock.unwrap') } },
        footnotes: label('markdown.footnotes') } });
    }

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

            // Each value once (538): a session equal to its agent, and a recipient that is this
            // conversation itself, add nothing. A notice shows who · event over its Markdown; the
            // native card below still holds the model's text (title with the execution, Facts).
            function IncomingAgentMessage(props) {
              const source = props.node.data.source;
              const original = h(NativeTrigger, props);
              if (source?.kind !== 'agent-message' || !['communicationId', 'senderSessionId',
                'senderAgentId', 'recipientAgentId'].every(key =>
                typeof source[key] === 'string' && source[key].length > 0)) return original;
              const field = (label, agent, session) => h('div', null,
                h('dt', { style: { fontSize: 12, opacity: 0.7 } }, label),
                h('dd', { style: { margin: 0, overflowWrap: 'anywhere' } },
                  h('div', null, agent), session === agent ? null : h('div', null, session)));
              const notice = noticeParts(props.node.data.content);
              const fields = [];
              if (!notice) {
                fields.push(field(source.senderSessionId === source.senderAgentId ? 'From' : 'From Agent / Session',
                  source.senderAgentId, source.senderSessionId));
              }
              if (source.recipientAgentId !== props.sessionId) {
                fields.push(field('To Agent / Session', source.recipientAgentId, props.sessionId));
              }
              return h('section', { 'data-agent-incoming': source.communicationId },
                notice ? h('div', { 'data-agent-notice': true, style: { margin: '8px 0' } },
                  h('div', { style: { fontWeight: 600, overflowWrap: 'anywhere' } }, `${notice.who} · ${notice.event}`),
                  notice.body ? h('div', { style: { marginTop: 4 } }, markdown(notice.body, props.t)) : null) : null,
                fields.length ? h('dl', { style: { margin: '8px 0', display: 'grid', gap: 8 } }, ...fields) : null,
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
