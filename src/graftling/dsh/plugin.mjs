/** Independent native context/display hooks and the installed official MCP client. */
import { execFile, spawn, spawnSync } from 'node:child_process';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { basename, dirname, isAbsolute, join } from 'node:path';
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
// The MCP server name graftling registers its subagents tool under: the model sees
// mcp__<this>__subagents. The plugin id above is DSH's own agents service, not the tool.
export const MCP_SERVER = 'graftling';
// Without mcpCommand this is the host row: the installed bundle's own entry.
// mcpEnv: the owner channel variable the MCP server inherits from this DSH.
// mcpServer: the server name the row registers.
export const Config = z.object({ mcpCommand: z.string(), mcpArgs: z.array(String).default([]),
  mcpEnv: z.array(String).default(['GRAFTLING_CONNECTION']), mcpServer: z.string().default(MCP_SERVER) });
// How long a Session that starts no turn by itself waits before taking held notices.
const READY_MS = 2000;
// The Python found for this file's graftling installation, for every Session of this DSH.
let found;

/**
 * The command and extra environment that run ``python`` as a direct child of this DSH (649, as Pi's 648).
 * Off Windows: ``python`` unchanged. On Windows a venv's python.exe is a launcher that starts the real
 * interpreter as its own child, but the host requires DSH's node as its parent and accepts only
 * direct children of it as MCP callers. So a venv's base interpreter is started directly, with
 * __PYVENV_LAUNCHER__ naming the venv's python, as the launcher itself does.
 */
export function directPython(python, environment = process.env, platform = process.platform) {
  if (platform !== 'win32') return { command: python, env: {} };
  // -X utf8: the paths are printed as UTF-8, as read here; -I ignores PYTHONUTF8 (638).
  const found = spawnSync(python, ['-I', '-X', 'utf8', '-c', 'import sys; print(sys.executable); print(sys._base_executable)'],
    { encoding: 'utf8', env: environment, timeout: 20000 });
  const [executable, base] = found.status === 0 ? found.stdout.split(/\r?\n/) : [];
  if (!executable || !base || executable.toLowerCase() === base.toLowerCase()) return { command: python, env: {} };
  return { command: base, env: { __PYVENV_LAUNCHER__: executable } };
}

/** Whether ``candidate`` imports graftling from ``expected`` (its __init__.py), with -I.
 * -X utf8 makes the printed path UTF-8, as Node reads it: -I ignores PYTHONUTF8, and a
 * Windows code page cannot write every path (638). */
function importsHere(candidate, expected, environment) {
  return new Promise(done => execFile(candidate,
    ['-I', '-X', 'utf8', '-c', 'import os, graftling; print(os.path.realpath(graftling.__file__))'],
    { env: environment, timeout: 20000 }, (error, stdout) => done(!error && stdout.trim() === expected)));
}

/**
 * The Python the host and the MCP server run (581): GRAFTLING_PYTHON unchanged when
 * set. Otherwise the interpreters of the installation this file belongs to: DSH's
 * `dsh plugin add <installed graftling/dsh>` links the profile's package to it, so
 * this file's real path is <prefix>/lib/pythonX.Y/site-packages/graftling/dsh/ and
 * <prefix>/bin/ holds its Python (Windows: <prefix>\Lib\site-packages and
 * <prefix>\Scripts\python.exe); then python3 and python on PATH (an editable
 * install). A candidate counts only when it imports graftling from this same
 * directory, so the plugin and the Python package are always one installation.
 * Nothing is built or written. A refusal keeps no result, so the next Session tries
 * again after the fix.
 */
function graftlingPython(environment) {
  if (environment.GRAFTLING_PYTHON) return Promise.resolve(environment.GRAFTLING_PYTHON);
  found ??= (async () => {
    const directory = dirname(realpathSync(fileURLToPath(import.meta.url)));
    const pkg = dirname(directory);
    const site = dirname(pkg);
    const candidates = [];
    if (basename(site) === 'site-packages') {
      const lib = dirname(site);
      if (process.platform === 'win32') {
        // Windows: <prefix>\Lib\site-packages; a venv's Python is <prefix>\Scripts\python.exe.
        const prefix = dirname(lib);
        candidates.push(join(prefix, 'Scripts', 'python.exe'), join(prefix, 'python.exe'));
      } else {
        const prefix = dirname(dirname(lib));
        candidates.push(join(prefix, 'bin', basename(lib)), join(prefix, 'bin', 'python3'), join(prefix, 'bin', 'python'));
      }
    }
    candidates.push('python3', 'python');
    for (const candidate of candidates) {
      if (await importsHere(candidate, join(pkg, '__init__.py'), environment)) return candidate;
    }
    let version = '<version>';
    try { version = JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8')).version; } catch {}
    throw new Error(`graftling-dsh: no Python that imports this graftling (${pkg}) was found; tried ${
      candidates.join(', ')}. Install graftling once with uv: uv tool install --managed-python ` +
      `"git+https://github.com/LUOXIAO92/graftling.git" (append @v${version} to .git to match this plugin), ` +
      'then add graftling-dsh from that install ' +
      '(docs/install/dsh.md). Or set GRAFTLING_PYTHON to the Python graftling is installed in.');
  })();
  return found.catch(error => { found = undefined; throw error; });
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
    : `[subagents notice from ${agent} · ${event}${named}]\n${message}`;
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
    const { command, env: pythonEnv } = directPython(environment.GRAFTLING_PYTHON, environment);
    const child = spawn(command, ['-I', '-m', 'graftling.dsh.host', ...config], {
        cwd: agent.session.header.cwd, stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...environment, ...pythonEnv, GRAFTLING_DSH_NODE: process.execPath, GRAFTLING_DSH_ENTRY: process.argv[1] },
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
      JSON.stringify(cwd) ?? 'none'}), so the subagents tool has no default data root <project>/.agents-control. ` +
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
    try { await (await host)?.close(); } catch (error) { ctx.logger?.error?.(`subagents host: ${error.message}`); }
  };
  ctx.on('agent/created', async ({ agent }) => {
    const id = agent.session.header?.id;
    if (sessionEnvironment(agent).DSH_AGENT_SOURCE !== 'main' || hosts.has(id)) return;
    if (!environment.GRAFTLING_DSH_CONFIG && !environment.GRAFTLING_DATA_ROOT) {
      requireProjectDirectory(agent.session.header.cwd);
    }
    let python;
    const started = graftlingPython(environment).then(found => startAgentHost(agent,
      { ...environment, GRAFTLING_PYTHON: python = found }, notice =>
        ctx.get('agentMessages').receive(noticeMessage(notice, id))));
    hosts.set(id, started);
    let host;
    try {
      host = await started;
      const { command, env: pythonEnv } = directPython(python, environment);
      await attachMcp(agent, MCP_SERVER, command, ['-I', '-m', 'graftling.mcp'],
        { ...sessionEnvironment(agent), ...pythonEnv, GRAFTLING_CONNECTION: host.connection });
      announceReady(agent, host);
    } catch (error) {
      await close(id);
      throw error;
    }
  });
  ctx.on('session/disposed', session => close(session.id));
  ctx.effect(() => () => Promise.all([...hosts.keys()].map(close)));
}
