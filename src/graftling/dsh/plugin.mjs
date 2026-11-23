/** Independent native context/display hooks and the installed official MCP client. */
import { execFile, spawn } from 'node:child_process';
import { existsSync, realpathSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { installCommunication } from './communication.mjs';

// The user's own DSH sets no package variable: resolve from its real entry file.
const require = createRequire(process.env.DSH_AGENT_PACKAGE || process.env.GRAFTLING_DSH_PACKAGE ||
  realpathSync(process.argv[1]));
const mcpClient = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-mcp-client')));
const { default: z } = await import(pathToFileURL(require.resolve('@deepseek-ai/schemastery')));
const { TypertRemoteService, Remote } = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-typert-protocol')));
const { createUserMessage } = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-llm')));
export const name = 'agents';
export const inject = ['tools', 'sessionQuery', 'agents'];
// The MCP server name graftling registers its agents tool under: the model sees
// mcp__<this>__agents.
export const MCP_SERVER = 'graftling';
// Without mcpCommand this is the host row: the installed bundle's own entry.
// mcpEnv: the owner channel variable the MCP server inherits from this DSH.
// mcpServer: the server name the row registers.
export const Config = z.object({ mcpCommand: z.string(), mcpArgs: z.array(String).default([]),
  mcpEnv: z.array(String).default(['GRAFTLING_CONNECTION']), mcpServer: z.string().default(MCP_SERVER) });
// How long a Session that starts no turn by itself waits before taking held notices.
const READY_MS = 2000;
// True when this file runs inside graftling's repository, as DSH installs it from the
// repository root's package.json: the Python sources are here to build from.
const SOURCES = existsSync(fileURLToPath(new URL('../../../pyproject.toml', import.meta.url)));
// The pending or found Python built from SOURCES, for every Session of this DSH.
let built;

/** DSH's own home rule (dsh-home-paths resolveDshHome): a non-blank DSH_HOME, else ~/.dsh. */
function dshHome(environment) {
  const named = environment.DSH_HOME;
  if (!named?.trim()) return join(homedir(), '.dsh');
  return resolve(named === '~' || named.startsWith('~/') ? join(homedir(), named.slice(1)) : named);
}

/**
 * The Python the host and the MCP server run: GRAFTLING_PYTHON unchanged when set;
 * else, from graftling's repository, the environment plugin_python.py builds once
 * under the DSH home (python3 on PATH runs it). A refusal keeps no result, so the
 * next Session tries again after the fix.
 */
function pluginPython(environment) {
  if (environment.GRAFTLING_PYTHON) return Promise.resolve(environment.GRAFTLING_PYTHON);
  if (!SOURCES) {
    return Promise.reject(new Error('The agents DSH host needs GRAFTLING_PYTHON, the Python that has graftling ' +
      'installed (docs/install/dsh.md).'));
  }
  built ??= new Promise((found, refused) => execFile('python3',
    ['-I', fileURLToPath(new URL('plugin_python.py', import.meta.url)), dshHome(environment)], { env: environment },
    (error, stdout, stderr) => {
      if (!error) return found(stdout.trim());
      built = undefined;
      refused(new Error(error.code === 'ENOENT'
        ? 'graftling-dsh: there is no python3 on PATH; graftling needs Python 3.12 or later. Put one first on ' +
          'PATH as python3 and start DSH again. Or set GRAFTLING_PYTHON to a Python that has graftling installed ' +
          '(docs/install/dsh.md).'
        : stderr.trim() || error.message));
    }));
  return built;
}

/** Preserve durable native source; fork ancestry alone is not delegation. */
export function sessionEnvironment(agent) {
  const header = agent?.session.header;
  if (!header) return {};
  const child = header.origin === 'subagent' || (header.delegationDepth || 0) > 0;
  const main = header.version === 4 && typeof header.id === 'string' &&
    header.origin === undefined && typeof header.isSeeded === 'boolean';
  return { DSH_SESSION_ID: header.id, DSH_AGENT_SOURCE: child ? 'child' : main ? 'main' : 'unknown' };
}

/** The relay request that admits one host notice to its DSH main Session once. */
export function noticeMessage(notice, session) {
  const { id, agent, event, message, execution } = notice;
  const named = execution ? ` · execution ${execution}` : '';
  // 538: the host's notice_text (title with the execution, Markdown content, Facts), which graftling-dsh's
  // browser module draws as who · event over Markdown; the older title only for a host that sends none.
  const body = typeof notice.text === 'string' && notice.text ? notice.text
    : `[agents notice from ${agent} · ${event}${named}]\n${message}`;
  return { id: `agents-notice:${id}`, sessionId: session, senderSessionId: notice.session || agent,
    sender: agent, recipient: session, body };
}

/**
 * Start the independent control owner for one native main Session.
 * After the connection line, each host stdout line is one notice for
 * ``onNotice``; it is acknowledged with whether the Session admitted it.
 * ``ready`` tells the host once that it may deliver the notices it holds.
 */
export function startAgentHost(agent, environment, onNotice) {
  return new Promise((resolve, reject) => {
    const config = environment.GRAFTLING_DSH_CONFIG ? ['--config', environment.GRAFTLING_DSH_CONFIG] : [];
    const child = spawn(environment.GRAFTLING_PYTHON, ['-I', '-m', 'graftling.dsh.host', ...config], {
        cwd: agent.session.header.cwd, stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...environment, GRAFTLING_DSH_NODE: process.execPath, GRAFTLING_DSH_ENTRY: process.argv[1] },
      });
    let diagnostic = '', output = '', failure, ready = false, closing = false, announced = false;
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', value => { diagnostic = (diagnostic + value).slice(-8192); });
    const ended = new Promise(done => child.on('close', code => {
      if (code !== 0 || !ready) failure = new Error(diagnostic || 'Independent DSH host failed.');
      if (failure) reject(failure);
      done();
    }));
    child.on('error', error => { failure = error; reject(error); });
    child.stdin.on('error', error => { failure = error; });
    child.stdin.write(JSON.stringify({ header: agent.session.header, model: agent.model?.current()?.model }) + '\n');
    const notice = async line => {
      if (closing) return;
      let document, shown = false;
      try {
        document = JSON.parse(line).notice;
        if (typeof document?.id !== 'string') throw new Error('not a notice');
        shown = (await onNotice(document))?.accepted === true;
      } catch (error) {
        // The host keeps a notice that was not admitted readable through status.
        diagnostic = (diagnostic + `\nNotice delivery failed: ${error.message}`).slice(-8192);
      }
      if (typeof document?.id === 'string' && !closing) child.stdin.write(JSON.stringify({ ack: document.id, shown }) + '\n');
    };
    child.stdout.on('data', value => {
      output += value;
      if (!ready) {
        if (!output.includes('\n')) return;
        try {
          const { connection } = JSON.parse(output.slice(0, output.indexOf('\n')));
          if (typeof connection !== 'string' || !connection) throw new Error('No independent host connection.');
          ready = true;
          output = output.slice(output.indexOf('\n') + 1);
          resolve({ connection, ready() {
            if (closing || announced) return;
            announced = true;
            child.stdin.write('{"ready":true}\n');
          }, async close() {
            closing = true;
            child.stdin.end();
            await ended;
            if (failure) throw failure;
          } });
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
 * Let the host deliver what it holds once this Session's own start is over.
 * A relay message wakes an idle Session, and a wake before the runner hands
 * over its task would run a turn ahead of the runner's permission settings
 * (``dsh-headless`` ``run``). So wait for the first turn, or for ``limit``
 * milliseconds in a long-lived profile that starts none by itself.
 */
function announceReady(agent, host, limit = READY_MS) {
  const deadline = Date.now() + limit;
  const check = () => {
    if (agent.status === 'running' || Date.now() >= deadline) host.ready();
    else setTimeout(check, 50).unref?.();
  };
  check();
}

/** Attach the MCP server ``server`` to one native Agent. */
function attachMcp(agent, server, command, args, env) {
  return agent.ctx.plugin(mcpClient, {
    transport: 'stdio', serverName: server, command, args,
    cwd: agent.session.header.cwd, env,
    toolCallTimeoutMs: 300000, failOnStartupError: true,
  }).await();
}

export function apply(ctx, config) {
  installCommunication(ctx, { TypertRemoteService, Remote, createUserMessage });
  if (config.mcpCommand === undefined) return applyHost(ctx);
  ctx.on('agent/created', async ({ agent }) => {
    await attachMcp(agent, config.mcpServer ?? MCP_SERVER, config.mcpCommand, config.mcpArgs, {
      ...sessionEnvironment(agent),
      ...Object.fromEntries((config.mcpEnv ?? ['GRAFTLING_CONNECTION'])
        .filter(name => process.env[name]).map(name => [name, process.env[name]])),
      ...(process.env.PYTHONPATH ? { PYTHONPATH: process.env.PYTHONPATH } : {}),
    });
  });
}

/**
 * Without GRAFTLING_DSH_CONFIG or GRAFTLING_DATA_ROOT the host's data root is
 * <Session cwd>/.agents-control, so the Session must record its project
 * directory; refuse with the fix otherwise.
 */
function requireProjectDirectory(cwd) {
  let directory = false;
  try { directory = typeof cwd === 'string' && isAbsolute(cwd) && statSync(cwd).isDirectory(); } catch {}
  if (!directory) {
    throw new Error(`graftling-dsh: this DSH Session records no usable project directory (cwd: ${
      JSON.stringify(cwd) ?? 'none'}), so the agents tool has no default data root <project>/.agents-control. ` +
      'Set GRAFTLING_DATA_ROOT to an absolute data root directory, or GRAFTLING_DSH_CONFIG to a host ' +
      'configuration naming its data root (docs/install/dsh.md).');
  }
}

/**
 * The installed bundle's host row: each native main Session of this DSH owns
 * one ``graftling.dsh.host`` while it is live, on GRAFTLING_DSH_CONFIG's data
 * root when set, else on GRAFTLING_DATA_ROOT, else on the Session cwd's
 * .agents-control. Inert inside a DSH that is already an Agent, whatever
 * those variables say.
 */
function applyHost(ctx) {
  const environment = process.env;
  if (Object.hasOwn(environment, 'GRAFTLING_CONNECTION') || Object.hasOwn(environment, 'DSH_AGENT_PACKAGE')) return;
  const hosts = new Map();
  const close = async id => {
    const host = hosts.get(id);
    hosts.delete(id);
    try { await (await host)?.close(); } catch (error) { ctx.logger?.error?.(`agents host: ${error.message}`); }
  };
  ctx.on('agent/created', async ({ agent }) => {
    const id = agent.session.header?.id;
    if (sessionEnvironment(agent).DSH_AGENT_SOURCE !== 'main' || hosts.has(id)) return;
    if (!environment.GRAFTLING_DSH_CONFIG && !environment.GRAFTLING_DATA_ROOT) {
      requireProjectDirectory(agent.session.header.cwd);
    }
    let python;
    const started = pluginPython(environment).then(found => startAgentHost(agent,
      { ...environment, GRAFTLING_PYTHON: python = found }, notice =>
        ctx.get('agentMessages').receive(noticeMessage(notice, id))));
    hosts.set(id, started);
    let host;
    try {
      host = await started;
      await attachMcp(agent, MCP_SERVER, python, ['-I', '-m', 'graftling.mcp'],
        { ...sessionEnvironment(agent), GRAFTLING_CONNECTION: host.connection });
      announceReady(agent, host);
    } catch (error) {
      await close(id);
      throw error;
    }
  });
  ctx.on('session/disposed', session => close(session.id));
  ctx.effect(() => () => Promise.all([...hosts.keys()].map(close)));
}
