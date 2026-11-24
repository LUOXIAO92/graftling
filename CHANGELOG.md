# Changelog

`graftling` follows semantic versioning on its [public interface](PUBLIC_API.md);
section 7 there holds the rules. Each version is marked compatible or incompatible
with the version before it.

**Nothing has been published yet.** There is no tag and no PyPI release. The version
numbers mark points in the repository's `dev` history. Before 0.2.4 several fixes
merged without raising the number, so two commits with the same number can differ. A
wheel names only a version, so build it from a commit you know. 0.2.4 is the first
version that identifies everything listed under it.

The numbers in parentheses are the project's Task numbers.

## Unreleased

### Changed

- The install command is `uv tool install --managed-python
  "git+https://github.com/LUOXIAO92/graftling.git@<tag-or-commit>"` (597) in the
  README, every install note, the Claude Code plugin's start-failure message and the
  DSH plugin's missing-Python message. Without the option uv may build the tool
  environment on a Conda or system Python it finds first, and the environment
  breaks when that interpreter is upgraded or removed. An existing installation
  moves to a uv-managed Python with `uv tool uninstall graftling` and the new
  command. No code or interface changes.

## 0.9.0 (incompatible)

The MCP tool `agents` is renamed `subagents`, a rename section 7 lists as
incompatible, so the minor version rises. There is no alias: the old names are
neither served nor listed. A GraphTraj that serves the renamed tool needs
`graftling>=0.9.0,<0.10`.

### Changed

- The MCP tool is `subagents` (591). `TOOL_NAME` is `"subagents"`; `tools/list`
  returns `subagents` and `serverInfo.name` is `subagents`. The model sees
  `mcp__graftling__subagents` (generally `mcp__<server>__subagents`) on Claude Code,
  Codex, Pi and DSH. The review exemption, the display, the Claude hook and its
  `PostToolUse` matcher, the Claude child's startup check, Pi's `--tools` owner entry
  and the Codex parent's guidance all use the new name; the tool's description now
  says that a host's own native subagents (Codex `spawn_agent`, Claude's Task tool)
  are not this tool. A tool named `agents` is now reviewed like any other.
- The commands `agents` and `agents-mcp` are now `subagents` and `subagents-mcp` (591).
- The Claude Code marketplace plugin's MCP server key is `subagents` (591): its tool
  is `mcp__plugin_graftling_subagents__subagents` and `/mcp` shows
  `plugin:graftling:subagents`. The plugin is still named `graftling`.
- The human-facing labels say `subagents` too (591): the display frames
  `subagents · <sender> → <recipient>` (Claude Code transcript rows and log,
  `subagents → <child>`; Pi), notice titles `[subagents notice from ...]`, the prefix of a
  message relayed into a child's turn `[subagents · <sender> → <recipient>]`, host log
  lines `subagents host ...` and `subagents-call ...` (including the plugin launcher's
  `graftling plugin: the subagents host did not start`), the review texts
  `subagents review of tool ...` and `subagents review did not allow ...`, and the Pi
  confirmation title `subagents: allow a tool beyond the host settings`.
- The reviewer connectivity probe at `create` is now `runtime: subagents`,
  `tool: subagents/connectivity-check` (was `agents`, `agents/connectivity-check`). A
  reviewer that answers the probe specially, by its tool, must match the new value (591).
- The reserved extra-tool name is `subagents` only; `agents` may now name an extra
  tool (591).
- The tool name has one source: `TOOL_NAME` in Python, and one `TOOL` constant in each
  of `claude/host_context.mjs`, `pi/extension.mjs` and `dsh/review.mjs`, which tests
  compare with it. `claude_host_plugin` and the marketplace copy derive the hook's tool
  line from `TOOL_NAME` and raise `RuntimeError` when a line they rewrite is missing,
  instead of writing an unchanged copy (591).

### Migration

- Claude Code host: run `configuration --write` again and restart Claude Code, so the
  project's hook plugin handles `mcp__graftling__subagents`; `/agents-context` then
  shows `MCP subagents registered`. Marketplace plugin users update the plugin.
- Pi and DSH: reinstall the packages from the upgraded installation.
- Change every place that names the old tool or commands: Claude Code allow rules
  (`permissions.allow` in a settings file, such as `mcp__graftling__agents`),
  `--allowedTools` values, scripts calling `agents` or `agents-mcp`, and a create's
  `tools` and `disallowed_tools` entries (a child's own allow or deny list). A
  `disallowed_tools` entry naming `mcp__<server>__agents` names no tool any more, so
  it **silently stops denying**; replace it with `mcp__<server>__subagents`.
- Unchanged: the `tasks` tool, the data root (`.agents-control`, `control/agents`),
  result fields such as `{"agents": [...]}` from `status`, DSH's `agents` plugin id and
  service, the DSH patch rows `agents-host`, `agents-mcp` and `agents-review`, the
  notice `source: "agents"`, `run_summary`'s `agents_calls`, and the `/agents-context`
  command.
- Anything that matched the old labels or the probe in logs, transcripts or a reviewer
  (for example a test reading `host.log` for `agents host ...`, or a reviewer allowing
  `agents/connectivity-check`) must match the new text.
- Sessions and archives created before keep their earlier calls under the old name;
  they are drawn as ordinary tool calls, not as exchanges.

## 0.8.0 (incompatible)

Three changes are incompatible by section 7, so the minor version rises. 581 removes
0.7.0's released install route: graftling-dsh from the repository's GitHub address
(the root `package.json` and `dsh/plugin_python.py`) and the repository-root Claude
marketplace (see Removed). 576 changes a default with observable behaviour: a Claude
Code child created without `tools` gets Claude Code's probed default tool set instead
of 0.7.0's restricted one (see Changed). 585 leaves a user's own `pi --no-session`
Session without the `agents` tool (see Fixed). Everything else adds optional
variables, arguments or result fields, accepts more than before, or fixes behaviour
toward `PUBLIC_API.md`. A GraphTraj that relies on 576's default tools needs
`graftling>=0.8.0,<0.9`; the 0.7 range `graftling>=0.7.0,<0.8` does not include 0.8.0.

### Added

- One variable, `GRAFTLING_DATA_ROOT`, chooses the data root of every host
  (Claude Code plugin, Codex, Pi, DSH) in place of `<project>/.agents-control`.
  It must be absolute and is created when missing. A host's own setting still
  wins and is unchanged: configuration `--write` material and `serve --config`,
  Codex `--directory`, Pi `--agents-config`, `GRAFTLING_DSH_CONFIG`. The Codex
  host's `--directory` is now optional: without it and the variable, the data
  root is `<Session cwd>/.agents-control`, as for the other hosts. Children
  inherit the variable; a child holding `GRAFTLING_CONNECTION` starts no host,
  and the Claude Code plugin's server in such a child now refuses before
  writing `claude-host.json` (577).
- graftling-dsh works without `GRAFTLING_DSH_CONFIG` (570): a DSH main Session's
  data root is then `<Session cwd>/.agents-control`, the Claude Code host's default
  for the same project; the host starts without `--config` and creates it when
  missing. A Session without a usable `cwd` gets no host and its `agent/created`
  handler names the variable. A set variable is unchanged.
- Pi works once installed (572): without `--agents-config` the extension's data root
  is `<Session cwd>/.agents-control`, created when missing; without
  `GRAFTLING_PYTHON` it uses the first Python of its own installation, then `python3`
  or `python` on `PATH`, that imports graftling, and otherwise stops naming
  `GRAFTLING_PYTHON`. In a DSH-managed context without the flag it stays inert. A Pi
  child (`GRAFTLING_CONNECTION`) is unchanged.

### Changed

- One installation, from one source (581): install graftling once with
  `uv tool install "git+https://github.com/LUOXIAO92/graftling.git@<tag-or-commit>"`;
  every host's MCP server and hook plugin come from it. The install notes
  (README, `docs/install/*`) use it in place of building a wheel.
- graftling-dsh without `GRAFTLING_PYTHON` runs the Python of the installation
  it belongs to (the environment whose site-packages holds the plugin's real
  path; `dsh plugin add` links the profile's package there), then `python3` or
  `python` on `PATH` that imports this same graftling, as the Pi extension does
  (572). Otherwise the start is refused with the `uv tool install` command and
  `GRAFTLING_PYTHON`. An explicit `GRAFTLING_PYTHON` is still used unchanged.
- The Claude Code marketplace moved into the installed package
  (`graftling/.claude-plugin/marketplace.json`, plugin `graftling/claude-plugin/`,
  now in the wheel), so the marketplace plugin is added from the installation.
  Its missing-Python message names the `uv tool install` command.
- An observable default change (576): a Claude child created without `tools` now has
  Claude Code's unrestricted default built-in set (without a login) instead of 0.7.0's
  restricted one. On Claude Code 2.1.330 the probed set adds Bash, WebFetch, CronCreate,
  Monitor and Workflow and drops Glob and Grep, which the restricted set had. Under the
  user's login on the real host the child had Bash but not WebFetch, which account-level
  availability withheld (see Fixed).
- `tool_check` reports `default_tools_not_offered`: probed default tools a child
  without `tools` does not have, other than disallowed ones, such as tools the
  login's account-level availability withholds (576).

### Removed

- 569's install of graftling-dsh from the repository's GitHub address
  (incompatible for that route): the root `package.json` and
  `dsh/plugin_python.py`, which built a second graftling under
  `$DSH_HOME/graftling-python`. The repository-root Claude marketplace
  (`.claude-plugin/marketplace.json`) is gone too. To move a 0.7.0 DSH profile:
  `dsh plugin --profile <profile> remove graftling-dsh`, delete
  `$DSH_HOME/graftling-python` with DSH stopped, install with uv and
  `dsh plugin add` the installed `graftling/dsh` directory
  (`docs/install/dsh.md`, "Moving from the 0.7.0 DSH install").

### Fixed

- A Pi subagent child no longer starts a second graftling host (585). The Pi extension
  starts no host, creates no data root and reports no error when `PI_SUBAGENT_CHILD=1`,
  `PI_SUBAGENT_DEPTH` is greater than 0, or the Session has no persistent Session file,
  with or without `--agents-config` and `GRAFTLING_DATA_ROOT`. Before, a child of Pi's
  bundled subagent example (`pi --no-session`) started its own host and its result
  carried `Extension error ...: The Pi native Session association is unavailable.`
  `GRAFTLING_CONNECTION` children and DSH-managed contexts are unchanged. Side effect:
  a user's own `pi --no-session` Session no longer has the `agents` tool.
- A Claude child no longer fails with `RUNTIME_REQUEST_FAILED` "Claude did not confirm
  the requested permission mode." when Claude reports a narrower mode than requested
  (578). On the real host, `claude-haiku-4-5-20251001` children requested `auto` and
  were reported as `default`. 0.7.0 failed them, although the turn had already started
  and, when it was short, finished its work while the input closed; a longer turn was
  killed after 5 s. Now a narrower mode (`default`, `manual`, `plan` or `dontAsk` for
  `auto`; `plan` or `dontAsk` for `default`) lets the run complete. Both modes are
  kept: the `permissions` record and outcome add `narrower: true`, the diagnostics add
  the warning `permission_mode_narrower`, and `status` adds `permission_mode` for the
  latest execution only. A wider,
  not comparable (`acceptEdits` against `auto`) or unknown mode still fails. The turn
  is now interrupted at that system init instead of running on while the input closes.
  See `PUBLIC_API.md` 4.1.
- A Claude child requested by a catalog alias such as `haiku` no longer fails at
  start with `RUNTIME_REQUEST_FAILED` when Claude reports the full id its catalog
  resolves the alias to (`resolvedModel`, `claude-haiku-5-5`). `model` keeps the
  alias, and `status` adds `resolved_model`. Any other model still fails the child.
  A full id that a catalog entry resolves to, such as `claude-haiku-5-5`, is now a
  candidate of the native Claude login instead of `invalid-model`. Claude entries
  from `discover_models` carry `resolved_model` (574).
- A Claude Code child created without `tools` has Bash again (576). 0.7.0 passed no
  `--tools`, and the child's `--restricted` then removed the tools that run commands
  or code (Bash and the like) and WebFetch; `--tools default` does not bring them back.
  Now `ClaudeExecution.tool_arguments()` passes `--tools` with
  `ClaudeExecution.default_tool_set()`: the default built-in set of the child's own
  Claude Code, read at run time by starting the same executable once without
  `--restricted`, with no settings sources, MCP servers or credential, a closed base URL
  and an empty graftling-owned config directory, stopped at its system init before any
  model call. That is Claude Code's default set without a login: account-level
  availability under the user's login can remove tools from it (on the real host
  WebFetch was absent from every logged-in session and present only in the no-login
  probe). Such tools are still passed in `--tools`, Claude Code does not offer them,
  and the child reports them in `tool_check`'s `default_tools_not_offered`. The set
  is cached in the data root's private state
  (`claude-default-tools/`); nothing is hard-coded. If it cannot be read, the child does
  not start and the create fails `RUNTIME_REQUEST_FAILED`, naming the cause and
  suggesting create's `tools`. A subclass whose `tool_arguments()` returns its own flags
  without calling the base never probes. `--restricted`, the mandatory sandbox and the
  runtime-home and private-state denies are unchanged.
- Corrected documentation (576): a Claude child does not read user, project or local
  settings files (`--restricted`), unlike what 0.7.0's notes said; managed settings and
  graftling's own `--settings` apply. A user's own allow or deny rules reach a child
  only through create's `tools` and `disallowed_tools`.

## 0.7.0 (incompatible)

One change is incompatible by section 7: a Claude Code child no longer gets the fixed
tool set `--tools Bash,Read,Write,Edit,Glob,Grep` by default, but Claude Code's own
default tools under the host's settings files (551). That changes a default with
observable behaviour, so the minor version rises. Everything else adds public names,
optional arguments, result fields or exports, or applies only to the new arguments.
A GraphTraj that uses 551's names (`extra_tool_table`, `apply_tools`, a create's
`tools` and `disallowed_tools`, the children's `mcp_server`) needs
`graftling>=0.7.0,<0.8`; the 0.6 range `graftling>=0.6.3,<0.7` does not include 0.7.0.

- A child's tools are no longer hard-coded (551). `create` and `config.yml` presets
  accept optional `tools` (the native names the child may have) and `disallowed_tools`;
  a create's `tools` replaces the preset's and the denies are united; the choice is
  recorded as `selection.tools` and reaches the driver as `prepare(..., tools=)`. A
  driver whose `prepare` lacks `tools` refuses such a create with `unavailable`.
  Omitted, the child keeps its host's own tools: on Claude Code no `--tools` flag (the
  incompatible change above; the settings' deny rules still win and an unallowed use
  still asks the parent through `can_use_tool`), on Codex the captured config, on Pi and
  DSH the host's tools. Narrowing needs no authorization; nothing grants a use beyond
  the host's settings. Each Runtime package publishes `apply_tools`. An entry the
  Runtime cannot express or does not know is refused with `unavailable` (Codex, DSH,
  Pi when it provably does not know it) or, on Claude Code, checked after start and
  reported by `status` as `tool_check` (`tools_not_offered`,
  `disallowed_still_offered`, `disallowed_unconfirmed`). Enabling what the host
  disables fails `authorization-required` where the Runtime offers no approval.
- A host can serve its own MCP tools to an independent child beside `agents` (551):
  `(descriptor, callback)` pairs through `configure_independent(extra_tools=)`,
  `agents_connection(extra_tools=)`, `CodexManagedExecution(extra_tools=)`,
  `CodexDriver(extra_tools=factory)`, `PiExecution.extra_tools` and DSH's
  `bind_run(extra_tools=)`; the child's `graftling.mcp` lists them from
  `GRAFTLING_EXTRA_TOOLS`. New public name `extra_tool_table`. The child calls them
  without an approval ask, like `agents`, unless `disallowed_tools` names them.
- Every Runtime's child declares `mcp_server` (default `graftling`), the name its MCP
  server is registered under (551). Pi passes it to the child's extension as
  `GRAFTLING_MCP_SERVER`; `pi/extension.mjs` exports `childServer`. Codex writes the
  owner's entry under that name. Without a consumer's own name nothing changes.
- graftling-dsh installs from the repository itself (569). A root `package.json`
  (no install scripts) makes the repository the plugin bundle, so DSH's
  `dsh plugin add` and its Plugins page take the GitHub address. Without
  `GRAFTLING_PYTHON`, a plugin running from the repository builds its own Python
  environment from its sources under the DSH home on first start (offline, once per
  content and base Python); PyYAML and websockets come from the base Python or one
  named pip command. A plugin installed from the package directory is unchanged.

## 0.6.3 (compatible)

Every change below adds a public name, field, parameter or export, or changes only what a
host shows; none removes or renames a public name, result or archive format, so the
release is compatible by section 7 and raises the patch version. GraphTraj's range
`graftling>=0.6.0,<0.7` includes it; a GraphTraj that uses 555's names needs
`graftling>=0.6.3,<0.7`.

- The repository uses a conventional layout (560). `pyproject.toml`, `README.md`,
  `CHANGELOG.md` and `PUBLIC_API.md` are at the repository root and the install guides
  in `docs/install/`; `src/graftling/` holds the code and the files it reads at run
  time. Build a wheel with `pip wheel .` from the root (no longer `./src/graftling`).
  The wheel's modules, data files, entry points (`agents`, `agents-mcp`) and public
  interface are unchanged; it no longer carries `README.md` or `CHANGELOG.md`.
  `PUBLIC_API.md` and the install guides are still shipped as `graftling/PUBLIC_API.md` and
  `graftling/install/*.md`, because GraphTraj's tests read the installed copies.
- `agents send` that reaches an execution which already closed input at its turn end, but
  has not reported that end yet, goes to the next execution of the same Session (546).
  Claude, Codex and DSH close input at the turn end and report the end only after
  archiving; a send in that window was refused with `runtime-input-closed` while the Agent
  still showed active. `AgentControl.send` now waits, at most
  `input_closed_wait_seconds` (60 s), for that end, but only when nothing is unanswered, no
  stop is requested and the refusal does not say the turn is still running; the result's
  `delivery` is then `next-execution` with `closed_execution` and `execution`, and the
  refused receipt records the same. Any other refusal returns at once, rejected, as before.
  DSH refuses such a send as `runtime-input-closed` instead of `operation-unavailable`; Pi
  queues natively and is unchanged. Error codes and messages are unchanged.
- DSH places a relayed agents notice in the Session's next-turn inbox instead of the
  next-step inbox (545), so a notice that reaches a running turn opens a turn of its own
  after it ends and shows as that turn's block; a parent that needs a child's answer within
  the running turn calls `wait`. An idle parent is unchanged.
- One call returns every input problem, and `execute` can take its arguments from a JSON
  file (555). Envelope validation, a feature's argument schema and the flat `agents`
  function now report all problems at once: an `invalid-input` error keeps its `code` and
  readable `message` (the problems joined by `; `, unchanged for a single one) and gains
  `errors`, a list of `{path, reason}`. An unmatched `anyOf` reports the one alternative of
  the value's type, else names each alternative's problems. New public names:
  `ValidationErrors` (a `ValueError` carrying `errors`, raised by `validate_schema` and
  `validate_envelope`), `schema_errors` and `read_envelope_arguments`. The envelope gains
  `arguments_file`: a UTF-8 JSON object file whose fields inline `arguments` replace. The
  host supplies the directory it may come from (`handle_request(..., files=)`; agents'
  own hosts pass the caller's verified workspace); the path must resolve,
  links followed, to a regular file of at most 1 MiB inside it, and without a directory it
  is refused. Errors name the given path and a JSON position, never the content.

- The DSH relay service `agentMessages` (`dsh/communication.mjs`) declares the services it reads
  (`static inject = ['sessionQuery', 'agents']`) instead of relying on the context it is
  installed in (561). With DSH 0.2.2-rc.5 a context without them refused
  `cannot get property "sessionQuery" without inject`; the bundled `plugin.mjs` already injects
  both, so its path is unchanged. Same cordis code for DSH 0.2.2-alpha.2 (4.0.6-alpha) and
  rc.5 (4.1.0): a class's `static inject` is read by `Inject.resolve(plugin.inject)` in both,
  so the declaration is supported from 0.2.2-alpha.2 on. Compatible: no public name changes.
- DSH shows an agents notice as who · event over its Markdown (538). The DSH host relays
  `notice_text` (its notice line gains `text`); graftling-dsh's browser module draws the
  notice title's sender and event over the content through DSH's `MarkdownText`, prints an
  equal agent/session pair once and leaves out a recipient that is the open conversation.
  DSH's own card below keeps the model's text.

- The Claude Code Main shows agents traffic as who and what (533). A pushed notice's text
  is the new public `notice_text(event)`, the same for every Runtime: the unchanged title
  `[Graftling notice from <alias> · <event>[ · execution <id>]]`, then the content as
  Markdown (an end's `last_agent_message` and `error` verbatim instead of the whole end
  record as escaped JSON), then one `Facts: …` line with execution, outcome, request ID
  and token, method, report, budget key and error code. `permissions`, the repeated
  session and `terminal_confirmed` are no longer in the text; they stay in the record and
  `status`. Each queued Claude notice gains the field `text`, which the plugin submits.
  On Claude Code 2.1.314 or later the plugin draws that notice (bold sender and event,
  Markdown, dim facts) and each `create`/`send`/`resume` exchange as one transcript row
  (`agents → <child>`, `<child> · replied`) with the content as Markdown, instead of one
  `ui.log` line per text line; it falls back to those lines where no row is drawn. A row
  names the child by its `name` where the result has one. Compatible under section 7: a
  new public name, a new field, the title unchanged; the pushed body is model input that
  section 7 does not fix, and its facts keep what a Main needs to act. Codex and Pi still
  show their own text (Tasks 535, 536 can call `notice_text`).
- The Pi Main draws an agents notice as `[agents · <sender> · <event>]` over its Markdown
  body (536) instead of the `[graftling:agent-communication]` label over the notice title
  and body; the model input is unchanged. New public export `registerNoticeDisplay(pi, sdk,
  customType)` of `graftling/pi/extension.mjs` draws any custom message type whose
  `details` carry `sender`, `event` and `body` that way; `registerAgentDisplay` uses it.
  Compatible: a new export; a message without those details keeps Pi's default drawing.
- `CodexAppServer.send_host_event(thread_id, event, text=None)` takes an optional `text`, the
  tool output the Codex host shows and its model reads; without it the output stays the
  event's JSON, so graftling's own Codex host is unchanged. GraphTraj's interactive Codex Main
  passes `notice_text(event)` (535). Compatible under section 7: a new optional parameter.
- `send_host_event(..., *, delegation_from=None)`: when given, the output goes as Codex's own
  delegation output (`send_message_to_thread`, namespace `codex_app`, `<codex_delegation>` with
  `& < >` escaped), which the Codex TUI draws as "Sent by Codex from task <delegation_from>"
  over the rendered Markdown (535, found by 542, probed on Codex 0.171.0). Compatible: a new
  keyword-only parameter; without it nothing changes.

## 0.6.2 (compatible)

- graftling keeps its runtime directories out of the caller's git work tree (526). When
  a data root sits inside a git repository, DSH's per-turn changes card (which snapshots
  the work tree with `git add --all`) listed the rewritten session archive as a file
  edit. graftling now writes a `.gitignore` holding `*` into the graftling-named
  subdirectories it writes (`sessions/`, `control/`, `live/`, `native/`) when they exist:
  never at the caller's root, never over an existing file, and a failed write is
  ignored. Archive content and format, public names and results are unchanged, so the
  change is compatible (section 7 of the public interface). GraphTraj's range
  `graftling>=0.6.0,<0.7` includes it.
- DSH's browser shows agents calls in its own native Tool card (527). The `graftling-dsh`
  client module no longer registers a `tool.call.toolview` view for
  `mcp__graftling__agents` or the older `mcp__agents__agents`, so both now show as the
  collapsible card DSH uses for every other tool (title, then the call's input and
  output when expanded), as `mcp__graphtraj__agents` already did; the one-line
  `agents · … · …` row and its *Details* button are gone. The module keeps the sender and
  recipient it adds to incoming agent messages, and no longer asks DSH to load
  `@deepseek-ai/dsh-client-ui-tool` first, which only that view used. The browser view is
  not part of the public interface; nothing a model, host or archive sees changes.

## 0.6.1 (compatible)

- A native Session lookup reads only archive headers (518). `SessionArchive.native_header`,
  which `find_session` uses for every `session_read` by native ID and Claude collection
  uses when a Session starts, decoded the whole first line of every archive under the
  root to take its header. That line also holds the archive's resolved observations, so
  each lookup decoded them all: in one store of 1490 archives (13 GB, 5.09 GB of it on
  first lines) a lookup took 30.7 s CPU, now 0.17 s. The header is now decoded from the
  line's compact prefix (`{"archiveVersion":1,"header":...}`); any other shape, or a
  header that does not decode there, is read whole as before, with the same errors.
  Opening an archive still reads and validates all of it. No public name, argument,
  result, error or archive format changes; the results are the same, only faster, so
  the change is compatible (section 7 of the public interface). GraphTraj's range
  `graftling>=0.6.0,<0.7` includes it.

## 0.6.0 (incompatible)

graftling's own MCP server is named `graftling` on every Runtime (513). Claude Code,
Pi and DSH called it `agents`; Codex already used `graftling` (394). The model now sees
the tool as `mcp__graftling__agents` instead of `mcp__agents__agents`. The tool name
`agents` and the marketplace plugin's `mcp__plugin_graftling_agents__agents` stay.
A name the model and hooks see changes, so the minor version rises. See the README's
*Moving to 0.6.0*.

- Everything that recognised the tool by the name `mcp__agents__agents` now uses the
  server the Session actually registered: the review exemption on Claude, Pi and DSH,
  the Claude `PostToolUse` display hook and init check, the Claude Code host plugin's
  hooks and the Pi display. The review exempts only that one server's `agents` tool.
  Before, the `agents` tool of any server named `agents` was exempt, whoever registered
  it; now an `agents` tool of any other server is reviewed. A DSH child without the agents plugin row
  exempts nothing.
- A consumer that serves the tool under its own server name tells graftling where it
  registers it: `ClaudeExecution.mcp_server` and `DshExecution.mcp_server` (default
  `graftling`, like `CodexManagedExecution.mcp_server`), the DSH row setting `mcpServer`,
  `registerAgents(pi, {server})` and `registerReview(pi, environment, server)`,
  `claude_host_plugin(..., server=)` and `display_response(..., server=)` (PUBLIC_API.md
  1.4). `registerAgents`' `displayServers` now names servers in addition to the one it
  registers; its default is `[]` instead of `['agents']`.
- The Claude Code host's `configuration --write` writes the `.mcp.json` server
  `graftling` and removes the `agents` server it wrote before, when that entry starts
  `graftling.claude.host`. The marketplace plugin still stands aside for either entry.
- Old records: the DSH browser tool view renders both `mcp__graftling__agents` and
  `mcp__agents__agents` rows, so Sessions recorded before still show their agents calls.
  Archives never keyed on the name and read as before.
- Resuming a Session created under the old name continues the same native Session, now
  with the tool `mcp__graftling__agents`. Its earlier calls stay recorded as
  `mcp__agents__agents`, and a call the model makes under that old name finds no tool.
  Claude Code's MCP documentation says a resumed conversation needs the same server name
  for its MCP tools; graftling serves only `graftling`, so a resumed Claude Session reaches
  agents only through the new name. A Claude Code host project that still runs an `agents`
  server (no `configuration --write` since upgrading) keeps that name until the command
  runs again. The behaviour of a real resumed Session after the rename has not been
  observed on any Runtime.
- GraphTraj's range `graftling>=0.5.2,<0.6` excludes this version; GraphTraj adopts it in
  its own change (514), declaring its server names through the points above.

## 0.5.2 (patch release by the user's decision of 2026-11-17)

Everything below since 0.5.0 ships as 0.5.2. The change of 477 is incompatible by
section 7 of the public interface; by the user's decision it is released as a patch,
not a minor version. GraphTraj relies on 466 and 477, so its range becomes
`graftling>=0.5.2,<0.6`.

- Tool descendants are judged only on a settled read (498). Before, a read that was
  unreadable or empty counted as this execution's. So another execution's process
  sampled in its exec window was recorded, and its later tool shell was signalled when
  this execution stopped. A tool sampled as a fork copy of a Runtime without the marker
  was never recorded. Now kernel facts decide: darwin P_EXEC and csops platform binary;
  Linux PF_FORKNOEXEC, which is not run on a real host. A fork copy carrying the marker,
  or copying a platform image, is provisional. A recorded entry that reads readable
  without the marker is another execution's even before its exec completes: it is
  dropped with what was recorded beneath it, and stop excludes them all. Stop signals this execution's processes and their fork copies
  (subshells); `stop_tool_processes` never signals a process whose read is not settled
  and lists it under `remaining`. Recording can lag one
  sample. A process that never reads settled (setuid; Linux `env -i`) is not recorded.

- The Claude Code host's receiver carries `defers_push`: it only queues a root
  child's notice, which a result or the mod hands over later (494). Before, the core
  took the push before calling it, so a Main's `wait` arriving while it queued the
  end showed the body as `queueing` and did not take it, and the same result's
  `main_notices` (or the mod's next claim) brought the end a second time. When a
  `defers_push` receiver's answer settles the end, `push_deferred` and `push_holder`
  are dropped in that write.
- A `wait` that times out drops its `waiters` mark in the same record write that
  decides the timeout (487). Before, an end arriving in between was reserved for the
  departed wait, released as `held` and pushed at once, so a deferring receiver
  (a running Task member's child end) could not hold it for the parent's next `wait`.
- **Incompatible, released in 0.5.2 (477; see section 7 of the public interface).**
  `status`, `wait` and `retire` always return the child's stored
  `last_result.last_message`. Before, once the body had been delivered (or might
  have been: `pending`, `delivered`, `unconfirmed`), it was shown as `null`, and the
  Codex, Pi and DSH hosts and GraphTraj's Task member path also hid it while it was
  `queueing`. `notification` still reports the delivery state. In exchange, a reply
  is not pushed to a parent that is waiting for it: while the parent's own `wait`
  runs on the child's running execution it is recorded there (`waiters`), the end
  is recorded with the new `notification` value `waiting` instead of being pushed,
  and the wait settles it `delivered` under the record lock when it returns. So
  "create, then wait" gives the parent the reply once, in the wait result. An end
  nobody waited for is pushed once, as before; a wait that starts after the push
  claimed the end shows the body and the push still delivers it once. A wait that
  leaves without its reserved end, or whose process died, gives it back `held`, and
  it is pushed once (without `hold_notices`, at the next read) or delivered by the
  held-notice rules. `settle_shown` no longer relies on a hidden body: a `queueing`
  end is settled only when no push is under way and the host is not flushing it.
  The version number is not raised here.
  Correction 1: a wait that starts after the end also takes it while its push has not
  been taken. The end is written with `push_deferred`; new `AgentControl.take_push`
  and `push_deferred`, and a receiver attribute `defers_push`. GraphTraj holds a
  running Task member's child end until the member's own wait takes it or its turn
  ends (then one push); on the real host the member's wait came after the child's end
  and the end was pushed as well.
  Correction 2: the end records `push_holder`; an untaken end whose holder process died
  is held again and pushed once by the next read with a receiver. GraphTraj's member
  Worker makes that read right after the member's turn ends.
  Correction 3: the hold no longer blocks `finished()` (a single-threaded caller hung):
  a receiver may return `"deferred"` and settle later with `AgentControl.finish_push`.
  GraphTraj holds on a daemon thread bounded by `MEMBER_HOLD_MAX_SECONDS` (3600 s); the
  end is also sent once when the member's Worker is gone.
- New feature `session_read` in every entry (MCP `agents`, the `agents` CLI, host
  functions), 466. It reads one archived Session by `agent` or `session` without a file
  path: a Python slice (`start`, `stop`, negative from the end; by default the last 5)
  of the conversation text, or with `content` `tools` or `all` also tool calls and
  results or every archived record; `search` returns hit positions and snippets to slice
  around, plus `outside_view`, the count of matching archived events only a wider
  `content` shows (the scope of grepping the JSONL), and `repeat`, which `content` to
  search again with. Credentials and control material are redacted, before searching
  too. An unrecognized caller is refused before any lookup. By default a caller reads its
  own Session and its ancestors' and descendants'; a host replaces that with
  `AgentControl(session_reads=...)`. New public names `find_session`, `read_events`,
  `redact_session` in `graftling.sessions`, and `JsonlSessionStore.events()` and
  `.revision` (PUBLIC_API 1.2, 1.3). GraphTraj plugs in its project-wide policy, and its
  `tasks` `session_read` now forwards to this reading as a transitional entry.
- A Codex child whose owning process's main thread has already returned now ends with its
  own outcome instead of `RUNTIME_WORKER_FAILED` "cannot schedule new futures after
  interpreter shutdown" (473). The owner's blocking calls no longer use an executor, which
  refuses work once interpreter shutdown begins. New public name `in_thread` (section 1.3)
  runs one blocking call on its own thread for code on the Codex owner's loop.
- A DSH execution's tool processes are reclaimed the same way (463). DSH starts each
  Bash tool in its own session and on shutdown stops only those groups, so a process a
  tool moved into yet another session (`setsid`) outlived both a normal end and a
  killed owner. The `dsh web` service now gets this execution's `GRAFTLING_MEMBER_RUN`
  marker, its descendants are recorded as for Claude, and both drivers record
  `tool_marker` and `tool_descendants`. After a normal end or stop the owner stops the
  survivors once the service has exited and keeps the report in
  `dsh-termination-<execution>.yml` and the result's `escaped_processes`; reconciling an
  `owner-exited` execution stops them as for Claude.
- New public names `read_tool_descendants` and `stop_tool_processes` (section 1.3), so a
  host can stop a killed owner's recorded tool processes the way owner-exited
  reconciliation does (460). The functions themselves are unchanged.
- A Codex child's tool processes are now stopped after its owner or its service ends
  (462). Codex runs a tool shell in its own session below its app-server and stops it
  when its stdin closes, but a killed app-server leaves the shell and its command
  running. The Codex owner now starts its owned app-server with the execution's own
  `GRAFTLING_MEMBER_RUN` value (the parent's is still removed, 450) and sets it for
  tool shells, records the app-server's descendants as 459 does, and stops what is left
  once the app-server has exited, on its own thread so the end is not delayed
  (`codex-termination-<turn>.yml`, possibly written after the end is reported). The Codex driver and GraphTraj's agents
  driver record `tool_marker` and `tool_descendants`, so reconciling an `owner-exited`
  Codex execution stops them too. Limits are 459's; in addition a user-started
  `CodexHost` and a service reached through a proxy or an existing WebSocket are not
  covered, and Pi and DSH executions are unchanged.
- A Pi execution's tool processes are now stopped the same way as a Claude
  execution's (461): Pi starts its bash tool detached as `/bin/bash` and stops the
  running tool's process group itself when it shuts down; a background job left by an
  earlier tool call, or any tool process when Pi is killed without shutting down, is
  stopped through the recorded descendants. The Pi driver and GraphTraj's agents driver
  now record `tool_marker` and `tool_descendants` for Pi too.
- A Claude execution's tool processes that are Apple platform binaries are now stopped
  too (459): Claude Code's `/bin/zsh` Bash tool shell and commands such as
  `/bin/sleep`, whose environment macOS hides from the 457 marker scan. While the
  Runtime runs, its owner records the Runtime's descendants about once a second (pid,
  process group, uid, start time) in a file whose absolute path is the new process fact
  `tool_descendants`. Reconciling an `owner-exited` execution, and the owner's own
  reclaim after a normal end or stop, stop the recorded processes still alive with the
  same uid and start time, and their current descendants, along with the marked ones,
  and list them in `escaped_processes`. An unreadable record or a failed scan appears
  in its `error` field. Recorded command lines (the record file and `escaped_processes`)
  are redacted and cut to 300 characters; redaction covers only recognizable
  credentials. A readable descendant without this execution's marker (another
  execution's child Runtime, a Runner Worker) is not recorded or followed. Known limits:
  a process started and detached between two samples (about one second) is found only
  by the marker, so not when it is a platform binary; a process below a readable
  process without the marker is left alone by design; Windows is not covered. Older
  records, and Codex (until 462), Pi and DSH executions, are unchanged. The macOS pid listing also
  retries when its buffer was full, instead of returning a cut-short list.
- Reconciling an owner-exited Claude execution now stops the tool processes it left
  (457). The Claude driver and GraphTraj's agents driver record the execution's
  `GRAFTLING_MEMBER_RUN` value as the process fact `tool_marker`. The call that ends
  the execution `owner-exited` stops same-user processes carrying exactly that value
  and records them as `escaped_processes`. Before, a killed owner left the child's
  Bash tool shell and its command running, since they run outside the Runtime's
  process group. Older records, and Codex, Pi and DSH executions, are unchanged. On
  macOS this does not reach Apple platform binaries, whose environment cannot be read:
  Claude Code's `/bin/zsh` tool shell and commands such as `/bin/sleep` stay running.
- A process that runs `create` now keeps the execution's owner until it reports its end
  (452). The Claude, Codex, Pi and DSH drivers start that owner as a non-daemon thread, so
  a `create` answered on a daemon thread no longer loses its end when the process exits.
- The Agent document gains `configuration`, the configuration a driver applied at
  `create` (455). A driver may return `applied` (`configuration`, `model`,
  `reasoning_effort`) from `prepare`; the core records it and fills `model` and
  `reasoning_effort` only where `config.yml` chose none. GraphTraj's driver reports
  the role preset it resolved, so a child created with `configuration` (or the
  Runtime's own preset) no longer shows `null` for its model. Other drivers and older
  records show `null` as before.
- An end found by reconciliation now notifies the parent once without `hold_notices`
  too (451). When a call finds a running execution whose owner and Runtime are gone,
  it ends it `failed` with error `owner-exited` and records it `held` in the same write.
  A control with a receiver claims it and calls the receiver once, after the record
  lock is released, as for any other failed end. Before, only `hold_notices` hosts
  recorded such an end, and other hosts notified nobody. `last_result.notification`
  can now be `held` for such an end on a host without `hold_notices`, a value the
  field already had. GraphTraj's Runner Worker now checks a running Task member's
  children every 15 seconds, so such an end reaches the member within about that time.
  A Task member that is not running holds the end, and its next run receives it once;
  a read never resumes it. The claimed notice names the end's `error` for a host
  receiver, as `finished()` does.
- Each end notice names the execution it reports (449). The Codex host event gains
  `execution`, the same ID as the end's `last_result.id`; the Claude Code queue record,
  the Pi `details` and the DSH notice already held it. The text shown to the model ends
  its title with `· execution <id>` on the Claude Code, Pi and DSH hosts, for example
  `[Graftling notice from <alias> · completed · execution <id>]`. A new field and a longer
  title are compatible under section 7; a reader matching the title up to the event is
  unaffected.
- A Claude Code marketplace package (418): `.claude-plugin/marketplace.json` at the
  repository root lists the plugin `graftling` in `src/graftling/claude-plugin/`. It
  starts the `agents` host without `configuration --write`, finds its Python through
  `GRAFTLING_PYTHON` and then `python3` on `PATH`, and says what to install when neither
  imports `graftling`. `graftling.claude.host serve --project` and
  `context --project-from` are its entries. A project with `configuration --write`
  material keeps that host; the plugin's host refuses there. Seen on a real host in
  `claude -p` only: installed from a local marketplace at project scope, it created a
  Claude child and `wait` returned it completed. Interactive use, `/agents-context` and
  notice push are untried.

## 0.5.0 (incompatible)

The distribution is renamed Graftling (394). Every renamed name below is part of the
public interface, so the minor version rises. Old names are not read any more; the
README's *Moving from agent-swarm 0.4* lists the steps. Entries below 0.5.0 use the new
names; the distribution was then called `agent-swarm` and imported as `agent_swarm`.

- Distribution and Python package `graftling`; the host entry point group is
  `graftling.hosts`. The `agents` and `agents-mcp` commands and the `agents` tool keep
  their names. The Pi and DSH packages are `graftling-pi` and `graftling-dsh`.
- Environment variables: `AGENT_SWARM_<NAME>` became `GRAFTLING_<NAME>` (for example
  `GRAFTLING_CONNECTION`, `GRAFTLING_PYTHON`, `GRAFTLING_DSH_CONFIG`, `GRAFTLING_PI_REVIEW`),
  and the variables that carried the GraphTraj prefix became `GRAFTLING_NATIVE_RUNTIME`,
  `GRAFTLING_NATIVE_SESSION`, `GRAFTLING_NATIVE_SESSION_FILE`, `GRAFTLING_CLAUDE_HOST_PYTHON`,
  `GRAFTLING_CLAUDE_HOST_CONFIG` and `GRAFTLING_DSH_PACKAGE`. Run the Claude Code host
  install again so `.claude/settings.local.json` holds the new names.
- Native names: Codex's MCP server, `clientInfo` and `toolOutput` are `graftling`, so the
  Codex model sees `mcp__graftling__agents`; Pi's custom message type is
  `graftling:agent-communication`; Claude's hook callbacks are `graftling_start`,
  `graftling_collect_child` and `graftling_agents_display`; the Claude host's notice title
  is `[Graftling notice from <alias> · <event>]`, `graftling` when a notice names no sender.
  A resumed native Session keeps the entries it already holds under the old names.
- graftling no longer names a consumer's variables. A consumer declares its own channel
  variable and name prefix in the new entry point group `graftling.environment`
  (`PUBLIC_API.md` 1.4); without one, children lose only graftling's own channel. New
  public attributes let a consumer keep its own names: `ClaudeExecution.connection_env`,
  `CodexManagedExecution.mcp_server`, `registerAgents(pi, {displayServers})` and the DSH
  plugin's `mcpEnv` setting.
- `PiExecution` reads the project root from the request field `project_root`
  (default `worktree_path`); it read GraphTraj's `harness_root` before.
- Codex refuses every dynamic tool call, as it did for the retired names: no
  dynamic tool is registered.
- GraphTraj adopts it in the same change: its range becomes `graftling>=0.5.0,<0.6`, and
  it declares its own names through the points above, so its behaviour is unchanged.
- Version 4 Session archives stay readable: their `graphtraj` namespace is still read as
  `extra_fields.graphtraj`.

## 0.4.0 (incompatible)

Classified under `PUBLIC_API.md` section 7 as a new rejection of a request that used to
succeed, so the minor version rises. GraphTraj adopts it in the same change: its range
becomes `graftling>=0.4.0,<0.5`.

- A child Agent's `create` now refuses a new Agent whose connection uses a key the child
  does not hold, with `authority-denied` (440). A child holds its own connection's key
  (DSH's `DEEPSEEK_API_KEY` when it names none) and its connection's `pass_env`. The
  message names the key and says to add it to `pass_env` of the child's connection in
  your own configuration. A grandchild on the child's own connection is unaffected, and
  so are creates by a host or a GraphTraj Task member. Before, the owner ran the create
  with its own environment, so a child could start a grandchild on a key it was never given.
- GraphTraj: a DSH child started by `agents` can create a grandchild again (440). Its
  `agents` tool ran GraphTraj's own MCP entry, which ignored the child's channel and ran the
  create inside the MCP server as the Task member. DSH strips credential names from an MCP
  server's environment, so the launch failed with `RUNTIME_CREDENTIAL_MISSING`. The child
  now runs `graftling.mcp`, and its create runs in its owner as the child.

## 0.3.1 (compatible)

- A Codex host you start yourself (`CodexHost`) again runs its own Codex with your
  environment: 0.3.0 had applied the child rule and the credential shell exclude to it
  too, so its tool shell lost names such as `GH_TOKEN`. Children it starts still get the
  0.3.0 rule and exclude. This matches the README and `PUBLIC_API.md` 3.1, which leave a
  top-level host's environment to the user, so it is a compatible fix. (439)

## 0.3.0 (incompatible)

Classified under `PUBLIC_API.md` section 7 as a default that changes observable
behaviour, so the minor version rises. GraphTraj adopts it in the same change: its
range becomes `graftling>=0.3.0,<0.4`.

- A child Agent now receives only its own connection's key (433). Every launch path of
  all four Runtimes and of GraphTraj's Runner members calls one function,
  `graftling.child_environment`, on first start and on resume. Other credential-class
  variables, chosen by `graftling.is_credential_name`, no longer reach the child.
- `pass_env`, a list of variable names, declares more variables on a connection or at the
  top level of `config.yml` for a child with no connection. It is user configuration only:
  `create` still refuses it as an unexpected parameter. It never brings back a reviewer's
  key. GraphTraj takes it on a connection provider and on a role in `roles.yml`.
- A Codex child's tool shell now excludes credential names through
  `shell_environment_policy`, including exports reloaded from dotfiles and written to Codex's
  shell snapshot (433). `~/.zshenv` is not stopped, and `~/.zprofile` and `~/.zlogin` are not
  stopped before the snapshot is ready. The child's own key stays with Codex's process and leaves
  its shell. A declared credential name travels to Codex in the Session parameters.
- A GraphTraj inline role on a new dispatch refuses `pass_env` and `api_key_env` with
  `ROLE_NOT_CONFIGURED`; select a `connection:` instead (433). A retained Batch keeps loading.
- A Pi connection naming no key keeps the variable its home's `models.json` or `auth.json` names
  for the selected provider (433).
- **What a user may need to change:** a child that used an unrelated key from its parent's
  environment must now declare it. That covers tools in its shell such as `gh` with
  `GH_TOKEN`, a Codex MCP server with `bearer_token_env_var`, and DSH's official web search
  beside a connection naming another key (`pass_env: [DEEPSEEK_API_KEY]`).

## 0.2.20 (compatible)

- A Bash `cd` no longer ends a Claude child or loses its result (437). A native helper
  that finished while the child's working directory was moved raised `Hook working
  directory differs from collection entry`, and the execution failed with
  `RUNTIME_WORKER_FAILED`. The execution now compares the hook's Session with the Session
  it owns and ignores the hook's working directory. This is stricter: a hook naming another
  Session now fails the execution and archives nothing, where it used to be accepted.
  `hook_source` and `collect_hook_event` gain a `native_id` keyword for this; without it
  they keep comparing the working directory.
- A Claude host's `agents` call no longer fails with `Native Session/cwd/request is invalid`
  after a `cd` inside the project (437); the call still runs with the project's directory.
  A `cd` outside the project is still refused, with `authority-denied` and a message naming
  both directories and how to return.

## 0.2.19 (compatible)

- One blocked call no longer holds a closing Pi or DSH host (434). With another process
  holding one Agent's record lock, or a driver's `interrupt` that never returns, the other
  children are still interrupted and seen to end, only that Agent is named in the
  `stop-unconfirmed` line, and the process ends within 20 seconds of its pipe closing instead
  of when the lock is released. A model request in flight at the close and the notice flusher
  can add up to 3 seconds each; that request's caller is told the operation is unconfirmed.
  The line no longer says every other Agent stopped when a `status` went unanswered and no
  Agent was seen stopped.
  New: `worker_connection` and `agents_connection` take `close_seconds` (default: unchanged),
  and `AgentControl.visible_agents` names a caller's Agents without taking a record lock.
  The Pi host's stdin loop no longer writes a shown notice's record itself, so a held lock
  cannot stop it reading the close. A notice still locked when the host ends stays `held`, and
  the next host pushes it again.
  Claude and Codex hosts are unchanged.

## 0.2.18 (compatible)

- A Pi child that ends by itself before its first prompt, for example because a sandbox
  tool is missing, now fails with `RUNTIME_START_FAILED`, and the message opens with
  `Pi did not start:` and Pi's stderr (438). It used to read `RUNTIME_SHUTDOWN_FAILED`,
  `Pi native stop is unconfirmed`. That stop is still not certified.
- Esc on a running Claude host call no longer leaves that call's association prepared or
  shows `agents context cleanup failed` (438). A later hook ends it. A failure outside the
  interrupted hook is still shown, once.
- The `invalid-model` error for a model off the list now says to list its id under
  `models` of a connection in `config.yml` (438).
- The README states what a reviewer's `allow` means for a Claude child with
  `permission_mode: default`. `PUBLIC_API.md` warns that a `write` Pi child can read a token
  held in a remote URL in the repository's `config` (438).

## 0.2.17 (compatible)

- A DSH child no longer leaves `dsh web` and `graftling.mcp` running when the process
  that owns it dies before closing it (436). Every `dsh web` now comes with one small
  watcher process, with no inherited environment, that stops the process group if its
  owner ends without closing it, and writes `stopped` or `could not stop` to the owner's
  stderr: `host.log` in a Claude Code host. It keeps that stderr open until it ends,
  about 15 seconds at most. A normal close behaves as before and writes nothing.
- `agents host recorded its children stopped.` is no longer written while an Agent
  started by an earlier host still has a running Runtime (436). The host names those
  Agents in `host.log` instead. Its exit code does not change.

## 0.2.16 (compatible)

- A refused `reply` on a Claude child no longer makes the agents tool of a Pi, DSH or Codex
  host stop answering (435). The call returns its failure at once, with the code and the
  reason, and the request stays pending, so a reply in the right form still answers it. The
  refusal now names the form: a `claude/can_use_tool` request takes `{"behavior": "allow"}`
  or `{"behavior": "deny"}`. Any other unexpected failure while the channel handles a call,
  including a result it cannot serialise, is also answered as an error, with the traceback
  on the host's stderr.
- `send` to a Claude child that refuses the input now records the delivery as `rejected`
  and returns the Runtime's code and reason, as on Pi, Codex and DSH. It was recorded as
  `unknown` (435).

## 0.2.15 (compatible)

- A DSH launch can name extra writable directories (428). `DshExecution` adds the
  request's `additional_writable_roots` to the `sandbox-policy` row as DSH's own
  `additionalWritableRoots`. A launch that names none, which includes every independent
  DSH child, writes the same row as before. The package does not choose these
  directories; GraphTraj's member launch does, which is why GraphTraj now needs this
  version.

## 0.2.14 (compatible)

- The Codex install note's example could leave the parent using Codex's own `spawn_agent`,
  with `operations: []` and a last message saying the child had answered (432). The
  example now gives the literal `create` and `wait` calls and excludes `spawn_agent`. The
  `agents` tool description says what it is for, and `graftling.codex.host` adds a
  passage after the parent's `developerInstructions` naming `agents` as the way to make
  children. Children's settings are unchanged, and Codex's native tools stay available.
- A Codex host run now shows when a child did not come through `agents` (432). Stderr ends
  with `{"run_summary": {"agents_calls": N, "native_subagents": M}}`, with a note when N is
  0. A `collabAgentToolCall` `spawnAgent` item prints one `{"native_subagent": ...}` line and
  adds an entry to the new `native_subagents` in `--output` and `CodexHost.run`. stdout and
  the exit status are unchanged: a native subagent is not a failure. Which tool the model
  chooses can only be observed in a real run, and no real run has yet shown that item.
- The Claude Code, DSH and Pi install notes' examples now name the `agents` tool (432).
  This is the same wording risk; no bypass was seen on those hosts.

## 0.2.13 (compatible)

- A DSH connection that names no `api_key_env`, beside a reviewer whose `api_key_env` is
  `DEEPSEEK_API_KEY`, is now refused at `create` with `ROLE_CONFIG_INVALID` when no key
  file of the child's own exists (430). The child never receives the reviewer's key and
  DSH's default key is that same variable, so such a child could only fail at start with
  `session/model-unavailable`. The message names the reviewer, the variable and the fixes.
  A file that exists but holds no DeepSeek key still fails at start, because it is not read.
- A Pi connection with no `api_key_env`, beside a reviewer whose key is the only one that
  authenticates its model, is now refused at `create` with `invalid-config` (430). Such a
  child could only fail at its first prompt with `No API key found`. `create` asks Pi's own
  model listing with and without the reviewer's variable, so no credential is read and a
  login in the connection's home is not refused. The message names the reviewer, the
  variable, the provider and the fixes.

## 0.2.12 (compatible)

- A Codex parent no longer receives one child answer twice when its `wait` or `status`
  meets the host event carrying that answer (431). While the event is on its way, or a
  held end is being sent on `--resume`, these results show `notification: queueing` and
  `last_message: null`, as on the Pi and DSH hosts. An event that could not be written
  leaves the end `held`, so the next `status` or `--resume` still gives the answer.

## 0.2.11 (compatible)

- A Pi child with `worktree_access: write` can now commit in a linked worktree (429). It
  had write access to the Git common directory but could not read it, and Git also stopped
  on the `~/.gitconfig` the sandbox denies. The child now reads that directory, so it can
  read every branch's objects and refs, other worktrees' Git metadata and the repository's
  `config` and `hooks`, but no other worktree's working files. Its environment sets
  `GIT_CONFIG_GLOBAL` to the null device and carries the `user.name` and `user.email` the
  host's Git reports, as the `GIT_AUTHOR_*` and `GIT_COMMITTER_*` variables. Without a value
  no variable is added, and Git reports the missing identity. A `read` child is unchanged.

## 0.2.10 (compatible)

- A resumed Claude Session with a full archive no longer raises `Unresolved observations
  remain` for good because its transcript holds a record outside the conversation, such
  as an attachment (427). Only conversation messages the resume really left out now count
  as missing. A resume with no archive, and a message really missing, still make
  `messages()` raise. An archive that already holds this false gap stays pending, because
  it cannot be told from a real one.
- A Claude first start that reads a half-written last line no longer loses its messages
  (427). A resume that reads one, including a `SessionStart` hook with `source=resume`,
  now counts that line as history instead of collecting the whole history as new. A bad
  line before the last is still reported.

## 0.2.9 (compatible)

- Reading a Claude transcript while a line is still being written no longer leaves a
  resumed archive stuck (426). The failed read stayed pending once the line completed,
  and `messages()` raised `Unresolved observations remain` for good. The next successful
  read now clears it, and the partial line stays in `observations()`. History that was
  really left out, and a line that never completes, still make `messages()` raise.
- A read that failed and later cleared no longer hides a later gap in the same archive.
- A failed read now appears under the archive locator `read-gap:<n>`, not `history-gap`.
  An archive saved by an earlier version recovers its old read failures on the next
  successful read, unless history is missing from that resume too.

## 0.2.8 (compatible)

- A resumed Claude execution no longer loses its reply from the archive (422). A reply
  that reached the transcript before the history boundary was read counted as history,
  and `messages()` raised `Unresolved observations remain`. The boundary is now measured
  before Claude's hook is answered, so what Claude writes afterwards is always new. Only
  the controlled test peer has shown this; a real Claude would need the same delay.
- Records written after the hook, including off-chain ones, are now collected however
  late the archive's first read runs. A late read used to drop them as history. A resume
  without delay and a first start archive the same content as before.

## 0.2.7 (compatible)

- A standalone Pi attachment under `.graphtraj` is now copied (424). agents read it as
  missing because it named that directory itself; it no longer does.
- New keyword `private_paths` on `PiArchive` and new attribute `archive_private_paths` on
  `PiExecution`: further directories kept out of attachment copies. The data root,
  `<project>/.codex` and `~/.codex` stay protected, and the rule is now in the public
  interface (1.5).
- A `.codex` that is itself a link now stays protected. It used to be compared unresolved,
  so its files were copied if the link's target lay inside the authorized directory.
- GraphTraj requires `graftling>=0.2.7,<0.3` because it passes these to keep its own
  `.graphtraj` protected.

## 0.2.6 (compatible)

- A failure while closing up no longer replaces the failure already on its way to the
  parent (421). A DSH child that could not start used to report an empty
  `RUNTIME_TRACE_FAILED` on a resume with session saving on; it now reports
  `RUNTIME_START_FAILED` with DSH's own words. The second failure goes to the child's
  `stderr.log`, and a stop that could not be confirmed still gives
  `terminal_confirmed: false`.
- The same holds for Claude and Pi. Codex keeps `RUNTIME_SHUTDOWN_FAILED` for a service
  that will not exit, and its message now ends with the original failure.
- A DSH Trace that is empty after a completed turn reports `RUNTIME_TRACE_FAILED` with
  text; its message used to be empty.
- GraphTraj relies on this for its members, so it now requires `graftling>=0.2.6,<0.3`.

## 0.2.5 (compatible)

- `graftling.claude.host configuration --write` on a configured project (419) now
  updates only what the command owns: `command` and `args` of the `agents` MCP server,
  the two `GRAFTLING_CLAUDE_HOST_*` variables, `cwd`, `claude` and the plugin files.
  Other keys of the `agents` entry, such as `env`, were lost before and are now kept.
- The same re-run no longer resets `root` and `channel_file`, so a host with Pi children
  still starts afterwards. If the project itself moved, a path inside its old directory
  moves with it. Without `--write`, the printed configuration shows the kept paths.
- An existing JSON file that does not parse or does not hold an object now raises
  `ValueError` naming it, and nothing is written.
- New public names `write_adoption` and `kept_host_paths` in `graftling.claude`.
  GraphTraj's Claude host imports them, so GraphTraj now requires
  `graftling>=0.2.5,<0.3`.

## 0.2.4 (compatible)

- New public names `stop_owned` and `exit_if_owned_unconfirmed` (415). GraphTraj's
  Claude host imports them, and commits numbered 0.2.3 may lack them, so GraphTraj now
  requires `graftling>=0.2.4,<0.3`.
- Claude Code and Codex host shutdown (415): each child's stop is tried on its own, so
  one failed or blocked interrupt no longer leaves the others running. A stop still
  unconfirmed after 10 seconds is named in one `stop-unconfirmed` line, and the process
  exits non-zero. Before, the Codex host exited 0 and recorded nothing, and Claude Code
  hosts raised an error naming no Agent.
- The Codex host's `--output` document gains `stop_unconfirmed`.
- `interrupt_owned` no longer raises when one Agent's interrupt fails, and returns
  while one is blocked.
- This README, `CHANGELOG.md` and the host notes under `install/`, all in the wheel
  (391).

## 0.2.3 (compatible)

- The review plugin now covers Pi and DSH children (387). A Pi or DSH connection with
  `review` was refused with `invalid-config`, and is now accepted. Both adapters hold
  the tool call until it is answered.

## 0.2.2 (compatible)

- Public names for building a host or a consumer on agents (400): stoppable calls, the
  owner connection, the progressive envelope, MCP server parts, notices, processes and
  locks, Runtime executions, and per-Runtime packages. JavaScript exports. `import
  graftling` stopped loading the `graftling.codex` package.
- A package can declare a tool beside `agents` in its own Claude Code host:
  `claude_host_plugin` and `NativeCalls` with `tools` (400).
- `request_notices="decisions"` applies request notices to `ask_parent` only (400).
- A Pi child that is itself a parent receives its children's ends and permission
  requests, once each, held while it is stopped (410). `ExecutionEvents.return_notice`
  is new for that.
- A Pi child's key is off the command line and out of files, and its command reaches
  `srt` as one quoted word, so paths with spaces work (412).
- A DSH child no longer inherits `DEEPSEEK_BASE_URL` or `DEEPSEEK_SEARCH_BASE_URL`, and
  a failed DSH turn's message includes the native error (411). A setup that relied on
  those variables must declare `base_url`.
- `discover_models` for DSH follows the connection's `base_url` and `api_key_env`, and
  for Pi no longer writes into the home. So does Pi's keyless `pi_destination` (413).
- A DSH connection with `DEEPSEEK_API_KEY` at another origin sends web search to that
  endpoint, not to official search (413). To keep official search beside a gateway,
  name the gateway's key in another variable.
- Pi and DSH host shutdown with an unanswered request no longer abandons the other
  children or hangs (414). 10 seconds in total, all interrupts first. An Agent still
  unconfirmed is named in a `stop-unconfirmed` line, with a non-zero exit.

## 0.2.1 (compatible)

- Review plugin core and the Claude and Codex adapters (386): `reviewers`, a
  connection's `review`, the Agent document's `review`, and the error
  `review-unavailable`.
- Host notice holding (403): `last_result.notification` gains `held`, and
  `AgentControl(hold_notices=...)` with `held_notices`, `settle_notice`, `settle_shown`,
  `notice_open` and `close_notices`. All four hosts enable it, so a notice for a stopped
  parent is delivered once when it next runs. On the Claude Code host, a body shown by
  `wait` or `status` is then `delivered`, so a second read shows `last_message: null`.
  Adopting this means running `configuration --write` again.
- A Codex child that is itself a parent receives its children's notices (403).
- The Claude Code and Codex hosts accept a `pi` entry in `runtimes` (408). A Pi child
  needs the data root outside the worktree.

## 0.2.0 (incompatible)

- **Incompatible:** the normalized Session archive moves to `header.version` 5 (384).
  Readers older than 0.2.0 refuse it. Version 4 files stay readable and are rewritten
  whole as version 5 on their next change.
- Archives work in every independent host, with no refusal (384). `extra_fields`
  namespaces are new, on `create` and in the archive.
- Pi host, as a Pi extension (382), and DSH host, as a DSH plugin (383). The DSH gate is
  now a floor: 0.2.0-rc.2 or later.
- The Claude Code and Codex hosts accept a `dsh` entry in `runtimes` (383).
- Approval layers (385): `workspace` on `create`, `ask_parent`, the `reply` checks, and
  the relay of Codex's `auto_review` refusals to the direct parent.
- The Claude Code host serves `agents` calls concurrently (404). The code
  `call-cancelled` comes with it.

## 0.1.2 (compatible)

- The Claude Code and Codex hosts run children on each other's Runtime through
  `runtimes` (381).
- The notice event `request` and `AgentControl(request_notices=...)` (381).
- A Codex model mismatch at start or resume, or a `model/rerouted` notification, ends
  the child `failed` with `RUNTIME_REQUEST_FAILED` under every host, naming both models
  and the reason (381, a fix).

## 0.1.1 (compatible)

- `create` takes `preset`, `model` and `reasoning_effort`. `config.yml` gains
  `connections` and `presets`. `discover_models` and `read_connections` are new. The
  Agent document gains `preset`, `connection`, `model` and `reasoning_effort` (380).

## 0.1.0

- The first numbered version: the public interface and these version rules (379),
  around the existing `agents` tool, its CLI and MCP entries, and the independent
  Claude Code and Codex hosts. Each host ran children on its own Runtime only.
- Merged later without a bump (388, fixes): `send` with `reports_only: true` is rejected
  with `not-implemented` by agents' own backend, which used to ignore the field and
  start new work, and envelope validation errors use the coded error structure.
</content>
