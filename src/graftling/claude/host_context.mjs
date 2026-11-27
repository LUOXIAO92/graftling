/** Native context and human-only observation for the external subagents entry.
 *
 * This module associates the current native call with its MCP owner while
 * preserving Claude's entire tool/permission chain.
 * It never caches session.start as identity across clear/resume/branch.
 */

// graftling's own MCP tool, the only one whose exchange this module renders. It
// is graftling.tool.TOOL_NAME; graftling.claude.plugin refuses a copy whose
// line differs, so the two cannot drift.
const TOOL = "subagents";
// The tools this module associates. claude_host_plugin() replaces this line in
// the written copy with the tools a package declares beside TOOL.
const TOOLS = [TOOL];
// The plugin whose MCP server serves these tools. null: the project's own
// .mcp.json (configuration --write). The marketplace copy names its plugin. Its
// server key is TOOL (claude-plugin/.claude-plugin/plugin.json), so Claude Code
// calls its tools mcp__plugin_<plugin>_subagents__<tool>.
const PLUGIN = null;
// Without a plugin, the .mcp.json server serving these tools: Claude Code calls
// them mcp__<server>__<tool>. claude_host_plugin() replaces this line too.
const SERVER = "graftling";
const PREFIX = PLUGIN ? `mcp__plugin_${PLUGIN}_${TOOL}__` : `mcp__${SERVER}__`;
const INSTALL = 'Install the graftling wheel into a Python 3.12+ environment '
  + '(python -m pip install graftling-<version>-py3-none-any.whl) and set GRAFTLING_PYTHON '
  + 'to that python, in your shell or under env in ~/.claude/settings.json; then restart Claude Code.';

// The notice text graftling.notice_text() writes: a title line, Markdown
// content, and a last 'Facts: …' line. One prompt can hold several notices.
const NOTICE_TITLE = /^\[Graftling notice from (.+?) · ([^\]]+?)(?: · execution ([^\]\s]+))?\]$/;
const NOTICE_LABELS = {'runtime-error': 'runtime error', 'report-submitted': 'report submitted',
  'execution-budget-exceeded': 'execution budget exceeded'};

function noticeLabel(event) {
  if (NOTICE_LABELS[event]) return NOTICE_LABELS[event];
  return /can_use_tool|approval|permission|request/.test(event) ? 'needs a permission reply' : event;
}

function parseNotices(text) {
  const notices = [];
  for (const line of String(text ?? '').split('\n')) {
    const title = NOTICE_TITLE.exec(line);
    if (title) notices.push({from: title[1], event: title[2], execution: title[3], lines: []});
    else if (notices.length) notices.at(-1).lines.push(line);
  }
  return notices.map(({from, event, execution, lines}) => {
    while (lines.length && !lines.at(-1).trim()) lines.pop();
    const facts = lines.length && lines.at(-1).startsWith('Facts: ') ? lines.pop().slice(7) : '';
    const shown = execution && !facts.includes(execution) ? [`execution \`${execution}\``, facts] : [facts];
    return {from, label: noticeLabel(event), body: lines.join('\n').trim(), facts: shown.filter(Boolean).join(' · ')};
  });
}

function noticeFrames(session, document) {
  // Member events queued for this Main arrive with its next tool result.
  const frames = [];
  for (const notice of Array.isArray(document.main_notices) ? document.main_notices : []) {
    const [parsed] = typeof notice.text === 'string' ? parseNotices(notice.text) : [];
    if (parsed) { frames.push({...parsed, to: session}); continue; }
    const body = notice.event;
    const sender = typeof body?.alias === 'string' ? body.alias : 'graftling';
    const text = typeof body?.message === 'string' ? body.message : JSON.stringify(notice.event);
    frames.push({from: sender, to: session, label: noticeLabel(body?.event ?? 'event'), body: text});
  }
  return frames;
}

function noticePrompt(notices) {
  // One Main input; the full structured notices stay in the queue history.
  // ``text`` is the shared notice Markdown the host wrote (533).
  return notices.map((notice) => {
    if (typeof notice.text === 'string') return notice.text;
    const body = notice.event ?? {};
    const execution = body.execution ? ` · execution ${body.execution}` : '';
    return `[Graftling notice from ${body.alias ?? 'graftling'} · ${body.event ?? 'event'}${execution}]\n${body.message ?? JSON.stringify(body)}`;
  }).join('\n\n');
}

// One frame: {from, to, body} and, for a notice, label and facts.
function exchangeFrames(session, event, result, seen) {
  if (result.deny || result.isError) return [];
  const content = result.result ?? result.text;
  const text = typeof content === 'string' ? content : Array.isArray(content)
    ? content.filter((block) => block.type === 'text').map((block) => block.text).join('\n') : '';
  let document;
  try { document = JSON.parse(text); } catch { return []; }
  if (!document) return [];
  const frames = [];
  if (document.error || typeof document.agent !== 'string') return cut(noticeFrames(session, document));
  // The name the Main gave the child says who it is; its id is the fallback.
  const who = typeof document.result?.name === 'string' && document.result.name ? document.result.name : document.agent;
  const outgoing = event.action === 'create' ? event.instruction : event.message;
  if (['create', 'send', 'resume'].includes(event.action) && typeof outgoing === 'string'
      && document.result?.resumed !== false) {
    frames.push({from: session, to: who, body: outgoing, outgoing: true});
  }
  const last = document.result?.last_result;
  const incoming = last?.last_message || last?.error?.message;
  const key = JSON.stringify([session, document.agent, last?.id]);
  if (last?.id && document.execution === last.id
      && typeof incoming === 'string' && incoming && !seen.has(key)) {
    frames.push({from: who, to: session, body: incoming, label: last?.last_message ? 'replied' : 'failed'});
    seen.add(key);
  }
  frames.push(...noticeFrames(session, document));
  return cut(frames);
}

function cut(frames) {
  return frames.map((frame) => frame.body.length <= 10000 ? frame
    : {...frame, body: frame.body.slice(0, 9800) + '\n\n… Display truncated; full text remains in the tool input/result.'});
}

// The ui.log fallback where no placed row is drawn: one native row per line.
function frameLines(frame) {
  return [`┌ subagents · ${frame.from} → ${frame.to}`, ...frame.body.split(/\r?\n/).map((line) => '│ ' + line),
    ...(frame.facts ? ['│ ' + frame.facts] : []), '└'];
}

// A placed row: who, then the content drawn as Markdown, like Claude's replies.
function drawFrames($, e, frames) {
  const {Box, Text, Markdown} = $.ui.resolve(e);
  const body = (text) => Markdown ? Markdown({text}) : Text({children: [text]});
  return Box({flexDirection: 'column', children: frames.map((frame, index) => Box({
    key: String(index), flexDirection: 'column', marginBottom: index < frames.length - 1 ? 1 : 0, children: [
      frame.outgoing
        ? Text({children: [Text({dimColor: true, children: ['subagents → ']}), Text({bold: true, children: [frame.to]})]})
        : Text({children: [Text({bold: true, children: [frame.from]}),
          Text({dimColor: true, children: [frame.label ? ` · ${frame.label}` : '']})]}),
      body(frame.body || '(no text)'),
      ...(frame.facts ? [Text({dimColor: true, children: [frame.facts]})] : []),
    ]}))});
}

// Show frames as transcript rows Claude does not read; ui.log where none is drawn.
async function showFrames($, state, frames) {
  for (const frame of frames) {
    let placed = null;
    const id = `agents-${state.run}-${state.rowCount += 1}`;
    state.rows.set(id, frame);
    try { placed = await $.ui.place({id}); } catch { placed = null; }
    if (placed?.isPlaced) continue;
    state.rows.delete(id);
    // Native ui.log is a single-line surface: embedded newlines become
    // replacement glyphs. Keep the same readable body, one native row each.
    for (const line of frameLines(frame)) await $.ui.log(line);
  }
}

// The Python this copy runs. The plugin copy takes GRAFTLING_PYTHON, else
// python3 on PATH, as its MCP server's bin/graftling-host does. Windows (OS is
// Windows_NT there) names it python, as a venv's Scripts\python.exe is named.
async function pluginPython($) {
  return (await $.env.get('GRAFTLING_PYTHON'))
    || ((await $.env.get('OS')) === 'Windows_NT' ? 'python' : 'python3');
}

// Whether configuration --write material serves this Session's project, so the
// plugin copy stands aside: GRAFTLING_CLAUDE_HOST_CONFIG must name
// <dir>/.agents-control/claude-host.json for the Session cwd or an ancestor.
// A value inherited from another configured project (a shell or tool started
// there) is foreign: 'foreign' is returned and the plugin copy works normally.
async function projectMaterial($) {
  const config = await $.env.get('GRAFTLING_CLAUDE_HOST_CONFIG');
  if (!config) return false;
  let directory = String(await $.session.cwd()).replace(/\/+$/, '');
  for (;;) {
    if (config === `${directory}/.agents-control/claude-host.json`) return true;
    const cut = directory.lastIndexOf('/');
    if (cut < 0) return 'foreign';
    directory = directory.slice(0, cut);
  }
}

// On Windows a venv's Scripts\python.exe is CPython's venv launcher: it starts the
// base python.exe as its child, so the host would see the launcher, not Claude Code,
// as this call's parent and refuse it. There the base interpreter is started itself:
// -I keeps the working directory and PYTHON* variables out of sys.path, and BOOT adds
// only the venv's site-packages (venv, the argument after it) before running the host.
const BOOT = 'import runpy, site, sys, sysconfig; venv = sys.argv.pop(1); '
  + "site.addsitedir(sysconfig.get_path('purelib', vars={'base': venv, 'platbase': venv})); "
  + "sys.argv[0] = 'graftling.claude.host'; runpy.run_module('graftling.claude.host', run_name='__main__', alter_sys=True)";

// The argv start that runs graftling.claude.host with `python` (647).
async function hostCommand($, python) {
  const module = [python, '-m', 'graftling.claude.host'];
  if ((await $.env.get('OS')) !== 'Windows_NT') return module;
  // <venv>\Scripts\python.exe -> <venv>; a python without pyvenv.cfg there is no venv.
  const venv = String(python).replace(/[\\/]+[^\\/]+[\\/]+[^\\/]+$/, '');
  if (venv === String(python) || !(await $.fs.exists(`${venv}/pyvenv.cfg`))) return module;
  const home = /^\s*home\s*=\s*(.+?)\s*$/m.exec(await $.fs.read(`${venv}/pyvenv.cfg`))?.[1];
  const base = home && `${home.replace(/[\\/]+$/, '')}${home.includes('\\') ? '\\' : '/'}python.exe`;
  if (!base || !(await $.fs.exists(base))) {
    throw new Error(`graftling: ${venv}/pyvenv.cfg names no base interpreter `
      + `(home = ${home ?? 'missing'}), so the host cannot be reached without the venv launcher.`);
  }
  return [base, '-I', '-c', BOOT, venv];
}

async function context($, document) {
  let command;
  if (PLUGIN) {
    // GRAFTLING_DATA_ROOT holds the plugin host's configuration when set (577).
    const dataRoot = await $.env.get('GRAFTLING_DATA_ROOT');
    command = [...await hostCommand($, await pluginPython($)), 'context',
      '--project-from', await $.session.cwd(), ...(dataRoot ? ['--data-root', dataRoot] : [])];
  } else {
    const python = await $.env.get('GRAFTLING_CLAUDE_HOST_PYTHON');
    const config = await $.env.get('GRAFTLING_CLAUDE_HOST_CONFIG');
    if (!python || !config) throw new Error('Native subagents host configuration is missing.');
    command = [...await hostCommand($, python), 'context', '--config', config];
  }
  let result;
  try {
    result = await $.process.run(command, {stdin: JSON.stringify(document)});
  } catch (error) {
    if (!PLUGIN) throw error;
    throw new Error(`graftling plugin: ${command[0]} did not start (${String(error)}). ${INSTALL}`);
  }
  if (result.exitCode !== 0) {
    if (PLUGIN && /No module named '?graftling/.test(result.stderr)) {
      throw new Error(`graftling plugin: ${command[0]} cannot import graftling. ${INSTALL}`);
    }
    throw new Error('Native agents association failed: ' + result.stderr);
  }
  return JSON.parse(result.stdout);
}

// /agents-context's line on the plugin copy's Python: its graftling version, or what to install.
async function pythonReport($) {
  const python = await pluginPython($);
  try {
    const result = await $.process.run([python, '-c', 'import graftling; print(graftling.__version__)']);
    if (result.exitCode === 0) return `graftling ${result.stdout.trim()} at ${python}`;
    return `graftling plugin: ${python} cannot import graftling. ${INSTALL}`;
  } catch (error) {
    return `graftling plugin: ${python} did not start (${String(error)}). ${INSTALL}`;
  }
}

// Deliver member events into the Main's turn or its idle Session through
// Claude's own prompt submission. Reading claims the notices, so a tool result
// never shows them again; a failed submission returns them to the queue.
// Without that host capability the notices stay queued for the next result.
function startPush($, state) {
  if (state.pushing) return;
  state.pushing = true;
  schedulePush($, state);
}

function schedulePush($, state) {
  const run = () => retryEnds($, state).then(() => pushNotices($, state))
    .finally(() => schedulePush($, state));
  try {
    $.clock.setTimeout(run, 5000);
  } catch {
    setTimeout(run, 5000);
  }
}

// Esc aborts the host calls of the interrupted hook, so its per-call end cannot
// run there. Later hooks end it; only a failure outside that hook is shown.
async function retryEnds($, state) {
  for (const ending of state.ends.splice(0)) {
    try { await context($, ending); }
    catch (error) {
      state.ends.push(ending);
      if (!state.endFailureShown) {
        state.endFailureShown = true;
        try { await $.ui.log('subagents context cleanup failed: ' + String(error)); } catch {}
      }
    }
  }
}

// One push at a time per process: the timer and the re-check after the last
// running tool call share it, so a resumed Session or an overlapping
// completion never starts a second loop.
async function pushNotices($, state) {
  if (state.inflight || state.active > 0 || state.stopped) return;
  state.inflight = true;
  let claimed = [];
  let session;
  try {
    session = await $.session.id();
    const pending = await context($, {operation: 'notices', session});
    claimed = pending.notices ?? [];
    if (claimed.length) {
      await $.prompt.submit({text: noticePrompt(claimed)});
      const ids = claimed.map((notice) => notice.id).filter((id) => typeof id === 'string');
      claimed = [];
      state.pushed = true;
      state.pushError = null;
      // The notices are in the Main now: this failing is no failed submission.
      try { await context($, {operation: 'acknowledge', session, position: 0, ids}); }
      catch (error) { state.pushError = String(error); }
    }
  } catch (error) {
    state.pushError = String(error);
    // Only a missing interface stops pushing; notices then go with the next
    // result. A host error (HooksError) is retried on the next tick.
    if (error?.name === 'TypeError' && /not a function|undefined/.test(error.message)) state.stopped = true;
  } finally {
    if (claimed.length && session) {
      const ids = claimed.map((notice) => notice.id).filter((id) => typeof id === 'string');
      try { await context($, {operation: 'requeue', session, ids}); }
      catch (error) { state.pushError = String(error); }
    }
    state.inflight = false;
  }
}

// tool.call carries the request fields on the event, tool.check under input.
function callRequest(event) {
  if (event.input && typeof event.input === 'object') return event.input;
  const request = {};
  for (const key of ['action', 'query', 'feature', 'schema', 'arguments']) {
    if (key in event) request[key] = event[key];
  }
  return request;
}

export function register(on) {
  const displayed = new Set();
  const state = {inert: false, foreignConfig: false, active: 0, pushing: false, inflight: false, stopped: false, pushed: false, pushError: null, ends: [],
    // Placed rows this process drew; ``run`` keeps a restarted process's ids new.
    rows: new Map(), rowCount: 0, run: Date.now().toString(36)};

  on('session.start', async ($, event, next) => {
    // One host per project: configuration --write material wins over the plugin.
    // Decided once per process, so /agents-context, push and every hook agree.
    const material = PLUGIN ? await projectMaterial($) : false;
    if (material === true) {
      state.inert = true;
      return next(event);
    }
    state.foreignConfig = material === 'foreign';
    await $.command.register({name: 'agents-context',
      description: 'Show current native Session and agents registration without a model turn', immediate: true});
    startPush($, state);
    return next(event);
  });

  on('command.run', {command: 'agents-context'}, async ($) => {
    const session = await $.session.id();
    const tools = await $.tool.list();
    await $.ui.log(`subagents · current Session ${session}`);
    if (PLUGIN) await $.ui.log(await pythonReport($));
    if (state.foreignConfig) {
      await $.ui.log('GRAFTLING_CLAUDE_HOST_CONFIG names another project\'s host; the plugin ignored it');
    }
    for (const name of TOOLS) {
      await $.ui.log(tools.some((tool) => tool.name === `${PREFIX}${name}`)
        ? `MCP ${name} registered` : `MCP ${name} missing`);
    }
    await $.ui.log(state.pushed ? 'notice push: delivered through prompt submission'
      : state.pushError ? `notice push unavailable (${state.pushError}); notices arrive with the next result`
      : 'notice push: waiting for a notice');
    return {};
  });

  for (const name of TOOLS) {
    const tool = `${PREFIX}${name}`;
    // Every tool shares one association; only agents has an exchange to render.
    on('tool.call', {tool}, async ($, event, next) => {
      // A standing-aside plugin copy leaves its host's refusal to answer.
      if (state.inert) return next(event);
      // Calls may overlap. Each ends only its own association, named by the
      // exact facts tool.check prepared; the host still consumes each once.
      state.active += 1;
      const ending = {operation: 'end', tool: name, request: callRequest(event)};
      try {
        ending.session = await $.session.id();
        const result = await next(event);
        const request = name === TOOL && event.action === 'execute'
          ? {action: event.feature, ...event.arguments} : {};
        await showFrames($, state, exchangeFrames(ending.session, request, result, displayed));
        return result;
      } catch (error) {
        return {deny: String(error)};
      } finally {
        try {
          if (ending.session) await context($, ending);
          await retryEnds($, state);
        } catch { state.ends.push(ending); }
        state.active -= 1;
        // Re-check at once: a notice queued during the calls need not wait a tick.
        if (state.pushing) pushNotices($, state);
      }
    }).catch(async ($, event, next) => next.called ? next(event)
      : {deny: 'The native subagents context hook failed; no call was authorized.'});

    on('tool.check', {tool}, async ($, event, next) => {
      const result = await next(event);
      if (result.decision === 'deny' || state.inert) return result;
      // agentId is an official host field, never a model argument. Native
      // subagents must use an actual child binding, not inherit Main authority.
      if (event.agentId) return {decision: 'deny', reason: 'This MCP owner is bound to the Main conversation.'};
      try {
        await context($, {operation: 'prepare', tool: name, session: await $.session.id(),
          cwd: await $.session.cwd(), agent: event.agentId ?? null, request: event.input});
        return result; // Preserve allow/ask and the original reason/ceiling.
      } catch (error) {
        return {decision: 'deny', reason: String(error)};
      }
    }).catch(async () => ({decision: 'deny', reason: 'Current native agents association is unavailable.'}));
  }

  // Rows this mod placed: who, and the content as Markdown. The validator
  // refuses reading the plugin's name as a value, so a row is ours by its id,
  // which holds this process's start time.
  on('ui.render', {component: 'Placed'}, async ($, e, next) => {
    const frame = state.rows.get(e.props?.id);
    return frame ? drawFrames($, e, [frame]) : next(e);
  });

  // A notice prompt: Claude reads the full text; the user sees who sent it,
  // the content as Markdown and the facts dim (533). Any prompt that holds a
  // notice title is drawn this way; others are Claude Code's drawing.
  on('ui.render', {component: 'UserMessage'}, async ($, e, next) => {
    if (state.inert) return next(e);
    const notices = parseNotices(e.props?.text);
    return notices.length ? drawFrames($, e, notices) : next(e);
  });

  on('session.end', async ($, event, next) => {
    if (state.inert) return next(event);
    try { await context($, {operation: 'end'}); state.ends = []; }
    catch (error) { $.ui.log('subagents context cleanup failed: ' + String(error)); }
    return next(event);
  });
}
