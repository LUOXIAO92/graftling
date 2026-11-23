/** Pi's session-local independent agents entry; loading this file is a native trust step. */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// How long a Session with a UI that starts no turn by itself waits before taking held notices.
const READY_MS = 2000;
// How often, and how many times, to look for a notice's entry after its message_end.
const ENTRY_POLL_MS = 20;
const ENTRY_POLLS = 250;
// The MCP server name graftling registers its agents tool under: the model sees
// mcp__<this>__agents. A consumer registering under its own name passes `server`.
export const MCP_SERVER = 'graftling';

/**
 * Draw a notice custom message of `customType` as `agents · <sender> · <event>`
 * over its Markdown `details.body`; the model still reads its whole content.
 * A message without a sender and body keeps Pi's default drawing.
 */
export function registerNoticeDisplay(pi, sdk, customType = 'graftling:agent-communication') {
  pi.registerMessageRenderer(customType, message => {
    const { sender, event, body } = message.details ?? {};
    if (typeof sender !== 'string' || typeof body !== 'string') return undefined;
    return new sdk.CustomMessageComponent({ ...message,
      customType: `agents · ${sender}${event ? ` · ${event}` : ''}`, content: body });
  });
}

/** Render the same successful MCP communication facts without model injection. */
export function registerAgentDisplay(pi, sdk, servers = [MCP_SERVER]) {
  const customType = 'graftling:agent-communication';
  registerNoticeDisplay(pi, sdk, customType);
  pi.registerEntryRenderer(customType, entry => {
    const { sender, recipient, body } = entry.data;
    return new sdk.CustomMessageComponent({
      role: 'custom', customType: `agents · ${sender} → ${recipient}`,
      content: body, display: true, timestamp: 0,
    });
  });

  pi.on('tool_result', (event, ctx) => {
    if (!ctx.hasUI || event.isError || !servers.includes(event.details?.server)) return;
    const tool = event.details.tool;
    const input = tool === 'agents' && event.input.action === 'execute'
        ? {action: event.input.feature, ...event.input.arguments} : undefined;
    if (!input) return;
    const result = event.structuredContent?.structuredContent;
    if (!result || result.error || !result.agent) return;
    const record = result.result || {};
    const caller = record.parent || ctx.sessionManager.getSessionId();
    const child = record.name || result.agent;

    // ponytail: scan native display entries; index only if measured history
    // size warrants it. Persisted fact IDs also deduplicate after reload.
    const displayed = new Set(ctx.sessionManager.getEntries()
      .filter(entry => entry.type === 'custom' && entry.customType === customType)
      .map(entry => entry.data.fact));
    const append = (fact, sender, recipient, body, execution = result.execution) => {
      if (typeof body !== 'string' || !body || displayed.has(fact)) return;
      pi.appendEntry(customType, { fact, sender, recipient, body, agent: result.agent,
        session: result.session, execution, source: 'agents' });
      displayed.add(fact);
    };
    if (['create', 'send', 'resume'].includes(input.action) && record.resumed !== false) {
      append(`send:${event.toolCallId}`, caller, child, input.instruction ?? input.message);
    }
    const reply = record.last_result;
    if (reply?.id) append(`reply:${result.agent}:${reply.id}`, child, caller, reply.last_message, reply.id);
    // appendEntry is Pi's display/state-only path. Do not return a modified
    // tool result or use sendMessage: either would change the model input.
  });
}


/** Resolve the import-only public SDK export of the actual owning Pi CLI. */
export async function loadHostSdk(executable = process.argv[1]) {
  let directory = dirname(realpathSync(executable));
  while (dirname(directory) !== directory) {
    const manifest = join(directory, 'package.json');
    if (existsSync(manifest)) {
      const data = JSON.parse(readFileSync(manifest, 'utf8'));
      if (data.name === '@earendil-works/pi-coding-agent') {
        return import(pathToFileURL(resolve(directory, data.exports['.'].import)).href);
      }
    }
    directory = dirname(directory);
  }
  throw new Error('The owning Pi SDK public entry is unavailable.');
}

/**
 * The Python of the graftling installation this file belongs to (572).
 * GRAFTLING_PYTHON wins when set. Otherwise the candidates are the
 * interpreters of the environment whose site-packages holds this file
 * (<prefix>/lib/pythonX.Y/site-packages/graftling/pi/ -> <prefix>/bin/),
 * then python3 and python on PATH (an editable install). A candidate counts
 * only when `-I` imports graftling from this same directory. No network.
 */
export function graftlingPython(environment = process.env, here = import.meta.url) {
  if (environment.GRAFTLING_PYTHON) return environment.GRAFTLING_PYTHON;
  const pkg = dirname(dirname(realpathSync(fileURLToPath(here))));
  const site = dirname(pkg);
  const candidates = [];
  if (basename(site) === 'site-packages') {
    const lib = dirname(site);
    const prefix = dirname(dirname(lib));
    candidates.push(join(prefix, 'bin', basename(lib)), join(prefix, 'bin', 'python3'), join(prefix, 'bin', 'python'));
  }
  candidates.push('python3', 'python');
  const expected = join(pkg, '__init__.py');
  for (const candidate of candidates) {
    const found = spawnSync(candidate, ['-I', '-c', 'import os, graftling; print(os.path.realpath(graftling.__file__))'],
      {encoding: 'utf8', env: environment, timeout: 20000});
    if (found.status === 0 && found.stdout.trim() === expected) return candidate;
  }
  throw new Error(`graftling: no Python that imports this graftling (${pkg}) was found; tried ${candidates.join(', ')}. `
    + 'Set GRAFTLING_PYTHON to the Python graftling is installed in (docs/install/pi.md).');
}

/** The Session's project directory for the default data root, or an error naming --agents-config. */
function requireProjectDirectory(cwd) {
  let usable = typeof cwd === 'string' && isAbsolute(cwd);
  try { usable = usable && statSync(cwd).isDirectory(); } catch { usable = false; }
  if (!usable) {
    throw new Error(`graftling: this Pi Session has no usable project directory (cwd: ${cwd}), so the agents tool `
      + 'has no default data root <project>/.agents-control. Start pi with --agents-config <file> naming a data root '
      + '(docs/install/pi.md).');
  }
  return cwd;
}

/** Capture plugin markers during session_start, before a shared host restores env. */
export function sessionEnvironment(ctx, environment) {
  return {
    GRAFTLING_NATIVE_RUNTIME: 'pi',
    GRAFTLING_NATIVE_SESSION: ctx.sessionManager.getSessionId(),
    GRAFTLING_NATIVE_SESSION_FILE: ctx.sessionManager.getSessionFile() || '',
    PI_SUBAGENT_CHILD: environment.PI_SUBAGENT_CHILD || '',
    PI_SUBAGENT_DEPTH: environment.PI_SUBAGENT_DEPTH || '0',
  };
}

/** Whether this notice's message is among the Session's ``custom_message`` entries. */
function noticeInSession(manager, fact) {
  // ponytail: scan native entries, as the display does; a notice is rare.
  return manager.getEntries().some(entry => entry.type === 'custom_message' &&
    entry.customType === 'graftling:agent-communication' && entry.details?.fact === fact);
}

/**
 * Inject one host notice (a child's answer, permission request or a
 * failed/interrupted end) into this Pi Session through Pi's native
 * custom-message API: an idle Session starts a turn with it, a streaming one
 * receives it as a follow-up. Returns true for a notice replayed after a crash
 * between injecting and acknowledging: it is already among the Session's
 * entries (Pi's ``SessionManager.appendCustomMessageEntry``) and is not
 * injected again. Otherwise Pi has only taken it; see ``registerAgents``.
 */
export function deliverNotice(pi, notice, session, manager) {
  const {id, alias, agent, event, message, execution} = notice;
  const fact = `notice:${id}`;
  if (noticeInSession(manager, fact)) return true;
  pi.sendMessage({
    customType: 'graftling:agent-communication',
    content: `[agents notice from ${alias} · ${event}${execution ? ` · execution ${execution}` : ''}]\n${message}`,
    display: true,
    details: {fact, sender: alias, recipient: session, body: message,
      agent, execution, event, source: 'agents'},
  }, {triggerTurn: true, deliverAs: 'followUp'});
  return false;
}

/**
 * Own the independent core until native shutdown; MCP only connects to it.
 * After the connection line, each host stdout line is one notice for
 * ``onNotice``: true means it is already in the Session (``shown``), otherwise
 * Pi has taken it (``queued``) and ``shown`` reports it later. After ``close``
 * starts, a notice is neither injected nor acknowledged, so the host keeps it
 * held. ``ready`` tells the host once that it may deliver the notices it holds.
 */
export function startAgentHost(ctx, config, onNotice = () => {}, python = process.env.GRAFTLING_PYTHON) {
  return new Promise((resolve, reject) => {
    // Without a config the host uses <ctx.cwd>/.agents-control (572).
    const child = spawn(python,
      ['-I', '-m', 'graftling.pi.host', ...(config ? ['--config', config] : [])], {
        cwd: ctx.cwd, stdio: ['pipe', 'pipe', 'pipe'],
        env: {...process.env, ...sessionEnvironment(ctx, process.env),
          GRAFTLING_PI_NODE: process.execPath,
          GRAFTLING_PI_ENTRY: process.argv[1],
          GRAFTLING_PI_MODEL: ctx.model?.id || '',
          GRAFTLING_PI_PROVIDER: ctx.model?.provider || ''},
      });
    let diagnostic = '', output = '', failure, ready = false, closing = false, announced = false;
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', value => { diagnostic = (diagnostic + value).slice(-8192); });
    const ended = new Promise(done => child.on('close', code => {
      if (code !== 0 || !ready) failure = new Error(diagnostic || 'Independent Pi host failed.');
      if (failure) reject(failure);
      done();
    }));
    child.on('error', error => { failure = error; reject(error); });
    child.stdin.on('error', error => { failure = error; });
    child.stdin.write(JSON.stringify({header: ctx.sessionManager.getHeader(),
      entries: ctx.sessionManager.getEntries().filter(entry => entry.type === 'custom')}) + '\n');
    const notice = line => {
      if (closing) return;
      let document, inSession;
      try {
        document = JSON.parse(line).notice;
        if (typeof document?.id !== 'string') throw new Error('not a notice');
        inSession = onNotice(document) === true;
      } catch (error) {
        // The host keeps a notice that was not shown readable through status.
        diagnostic = (diagnostic + `\nNotice delivery failed: ${error.message}`).slice(-8192);
        if (typeof document?.id === 'string') child.stdin.write(JSON.stringify({ack: document.id, shown: false}) + '\n');
        return;
      }
      child.stdin.write(JSON.stringify(inSession ? {ack: document.id, shown: true} : {ack: document.id, queued: true}) + '\n');
    };
    child.stdout.on('data', value => {
      output += value;
      if (!ready) {
        if (!output.includes('\n')) return;
        try {
          const {connection} = JSON.parse(output.slice(0, output.indexOf('\n')));
          if (typeof connection !== 'string' || !connection) throw new Error('No independent host connection.');
          ready = true;
          output = output.slice(output.indexOf('\n') + 1);
          resolve({connection, ready() {
            if (closing || announced) return;
            announced = true;
            child.stdin.write('{"ready":true}\n');
          }, shown(id) {
            if (!closing) child.stdin.write(JSON.stringify({ack: id, shown: true}) + '\n');
          }, async close() {
            closing = true;
            child.stdin.end();
            await ended;
            if (failure) throw failure;
          }});
        } catch (error) { child.stdin.end(); reject(error); return; }
      }
      let end;
      while ((end = output.indexOf('\n')) >= 0) {
        const line = output.slice(0, end);
        output = output.slice(end + 1);
        notice(line);
      }
    });
  });
}

/**
 * Register the independent agents connection on this Pi extension API.
 * The returned handlers report whether they own the started Session.
 * `server` is the MCP server name it registers; the display renders that
 * server's agents results and those of `displayServers` besides.
 */
export function registerAgents(pi, { server = MCP_SERVER, displayServers = [] } = {}) {
  const independent = new WeakSet();
  let agentHost;
  let readyTimer;
  let sessionManager;
  let displayRegistered = false;
  pi.registerFlag('agents-config', { type: 'string',
    description: 'Independent agents host configuration: data root and optional per-Runtime settings.' });
  // A held notice starts a turn in an idle Session, and Pi refuses a prompt
  // while a turn runs (AgentSession.prompt, print mode has no streamingBehavior).
  // So the host flushes during the Session's first turn; a Session with a UI
  // that starts none by itself gets its held notices after READY_MS.
  pi.on('agent_start', () => { agentHost?.ready(); });
  // A notice is delivered once Pi has written it to the Session, which a quit
  // during the turn would prevent. Handlers run before that write
  // (AgentSession._handleAgentEvent, dist/core/agent-session.js:752 then :760),
  // so look for the entry itself; without one the next host replays the notice.
  const confirmEntry = (host, fact, polls = ENTRY_POLLS) => {
    if (host !== agentHost) return;
    if (noticeInSession(sessionManager, fact)) host.shown(fact.slice('notice:'.length));
    else if (polls > 0) setTimeout(confirmEntry, ENTRY_POLL_MS, host, fact, polls - 1).unref?.();
  };
  pi.on('message_end', event => {
    const fact = event.message?.role === 'custom' ? event.message.details?.fact : undefined;
    if (agentHost && typeof fact === 'string' && fact.startsWith('notice:')) confirmEntry(agentHost, fact);
  });
  const closeHost = async () => {
    clearTimeout(readyTimer);
    // Cleared first: a host that failed to close must not fail the next start too.
    const host = agentHost;
    agentHost = undefined;
    await host?.close();
  };
  return {
    independent,
    async start(ctx) {
      await closeHost();
      independent.delete(ctx.sessionManager);
      if (ctx.hasUI && !displayRegistered) {
        registerAgentDisplay(pi, await loadHostSdk(), [server, ...displayServers]);
        displayRegistered = true;
      }
      if (Object.hasOwn(process.env, 'GRAFTLING_CONNECTION')) {
        if (!process.env.GRAFTLING_CONNECTION) throw new Error('The existing Agent connection is empty; no identity was adopted.');
        independent.add(ctx.sessionManager);
        // This is the official MCP client wiring, not a native model tool.
        // Identity is verified by the owning host's kernel-authenticated pipe.
        pi.registerMcpServer(server, {
          command: process.env.GRAFTLING_PYTHON, args: ['-I', '-m', 'graftling.mcp'],
          cwd: ctx.cwd, exposure: 'direct',
          // The owner's extra tools (graftling.extra_tools), listed by graftling.mcp beside agents.
          env: {GRAFTLING_CONNECTION: process.env.GRAFTLING_CONNECTION,
                ...(process.env.GRAFTLING_EXTRA_TOOLS ? {GRAFTLING_EXTRA_TOOLS: process.env.GRAFTLING_EXTRA_TOOLS} : {})},
        });
        return true;
      }
      const agentsConfig = pi.getFlag?.('agents-config');
      const managed = Object.hasOwn(process.env, 'DSH_SESSION_ID') || Object.hasOwn(process.env, 'DSH_AGENT_SOURCE');
      if (agentsConfig && managed) {
        throw new Error('Standalone agents config cannot replace an existing managed Agent context; retain its owning connection.');
      }
      // Without --agents-config a managed DSH context stays inert, as before 572.
      if (agentsConfig || !managed) {
        if (!agentsConfig) requireProjectDirectory(ctx.cwd);
        const python = graftlingPython();
        independent.add(ctx.sessionManager);
        const session = ctx.sessionManager.getSessionId();
        sessionManager = ctx.sessionManager;
        agentHost = await startAgentHost(ctx, agentsConfig, notice => deliverNotice(pi, notice, session, sessionManager), python);
        try {
          pi.registerMcpServer(server, {
            command: python,
            args: ['-I', '-m', 'graftling.mcp'], cwd: ctx.cwd,
            env: {GRAFTLING_CONNECTION: agentHost.connection}, exposure: 'direct',
          });
        } catch (error) { await closeHost(); throw error; }
        if (ctx.hasUI) readyTimer = setTimeout(() => agentHost?.ready(), READY_MS);
        return true;
      }
      return false;
    },
    close: closeHost,
  };
}

const REVIEW_TITLE = Symbol.for('agents.pi.review-title');

/**
 * Hold every tool call on a native dialog with the owner's reserved title until
 * the owner answers it; only a confirmed dialog lets the call run. Inert unless
 * the owner names the title in GRAFTLING_PI_REVIEW. The variable leaves the
 * environment at once, so no tool's process can learn the title. Pi may load this
 * file more than once in a process, with no module cache, so the title stays on
 * the process itself.
 *
 * The one call let through is the agents tool of `server`, the name
 * registerAgents registers: it is the child's channel to its parent, limited
 * by agents' authority checks, and must work when the reviewer is down. A tool
 * named agents on any other MCP server is reviewed.
 */
/**
 * The tools of `server` the owner declared beside agents (GRAFTLING_EXTRA_TOOLS, 551), as Pi names them.
 * Like agents they are the owner's own entries with their own authorization, so review lets them through.
 */
export function ownerTools(environment = process.env, server = MCP_SERVER) {
  let declared;
  try { declared = JSON.parse(environment.GRAFTLING_EXTRA_TOOLS || '[]'); } catch { declared = []; }
  if (!Array.isArray(declared)) return [];
  return declared.filter(item => typeof item?.name === 'string' && /^[A-Za-z0-9_-]+$/.test(item.name) && item.name !== 'agents')
    .map(item => `mcp__${server}__${item.name}`);
}

export function registerReview(pi, environment = process.env, server = MCP_SERVER) {
  const agentsTool = `mcp__${server}__agents`;
  // The owner's own entries pass without an ask: agents and every extra tool it declared (551).
  const exempt = new Set([agentsTool, ...ownerTools(environment, server)]);
  const title = environment.GRAFTLING_PI_REVIEW || globalThis[REVIEW_TITLE];
  delete environment.GRAFTLING_PI_REVIEW;
  if (!title) return false;
  globalThis[REVIEW_TITLE] = title;
  pi.registerCommand('agents-review-hook', { description: 'The agents review plugin is active.', handler: async () => {} });
  pi.on('tool_call', async (event, ctx) => {
    if (exempt.has(event.toolName)) return undefined;
    const call = JSON.stringify({ tool: event.toolName, call_id: event.toolCallId, input: event.input });
    const confirmed = await ctx.ui.confirm(title, call, { signal: ctx.signal });
    if (confirmed === true) return undefined;
    return { block: true, reason: `agents review did not allow ${event.toolName}; it did not run. Do not retry it; continue with other work or report it as blocked.` };
  });
  return true;
}

export const CONFIRM_TITLE = 'agents: allow a tool beyond the host settings';

/**
 * Ask before the first use of each tool the owner marked as beyond the host's
 * settings (GRAFTLING_PI_CONFIRM_TOOLS, 551). The ask is Pi's own confirm
 * dialog, which the owner passes to this child's parent as a native request;
 * only an explicit allow lets the call run, and it stays allowed for this
 * Session. Every other tool call is untouched.
 */
export function registerToolConfirm(pi, environment = process.env) {
  let names;
  try { names = JSON.parse(environment.GRAFTLING_PI_CONFIRM_TOOLS || '[]'); } catch { names = []; }
  delete environment.GRAFTLING_PI_CONFIRM_TOOLS;
  if (!Array.isArray(names)) return false;
  const pending = new Set(names.filter(name => typeof name === 'string' && name));
  if (pending.size === 0) return false;
  const allowed = new Set();
  pi.on('tool_call', async (event, ctx) => {
    if (!pending.has(event.toolName) || allowed.has(event.toolName)) return undefined;
    const call = JSON.stringify({ tool: event.toolName, call_id: event.toolCallId, input: event.input,
                                  reason: 'beyond the host settings' });
    const confirmed = await ctx.ui.confirm(CONFIRM_TITLE, call, { signal: ctx.signal });
    if (confirmed === true) { allowed.add(event.toolName); return undefined; }
    return { block: true, reason: `${event.toolName} is beyond the host settings and was not allowed; it did not run. Do not retry it; continue with other work or report it as blocked.` };
  });
  return true;
}

/** The server name the owner chose for this child (GRAFTLING_MCP_SERVER), else graftling's. */
export function childServer(environment = process.env) {
  const name = environment.GRAFTLING_MCP_SERVER;
  return typeof name === 'string' && /^[A-Za-z0-9_-]+$/.test(name) ? name : MCP_SERVER;
}

export default function agents(pi) {
  const server = childServer();
  registerReview(pi, process.env, server);
  registerToolConfirm(pi);
  const owner = registerAgents(pi, { server });
  pi.on('session_start', async (_event, ctx) => { await owner.start(ctx); });
  pi.on('session_shutdown', async () => { await owner.close(); });
}
