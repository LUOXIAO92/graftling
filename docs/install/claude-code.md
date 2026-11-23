# agents on Claude Code

This makes a Claude Code Session the host of its child Agents. Back to the
[README](../../README.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/). It provides Python 3.12 or later.
- Claude Code 2.1.299 or later, installed and logged in. Earlier versions are refused
  when a Claude child starts. The real runs used 2.1.312 and 2.1.314.
- A directory you will start Claude Code from. It does not need to be a Git
  repository.

## Install graftling

Once, with uv, as in the [README](../../README.md#install). Every host uses this one
installation:

```sh
uv tool install "git+https://github.com/LUOXIAO92/graftling.git@<tag-or-commit>"
AGENTS_PYTHON="$(uv tool dir)/graftling/bin/python"
```

## Distribution

Two ways attach the host to the installation above. Use one per project:

- **Generated plugin files**: `configuration --write`, in
  [Generate the plugin files](#generate-the-plugin-files), writes the MCP server and the
  plugin into one project, both naming `$AGENTS_PYTHON`. This is the way the other host
  notes assume.
- **The marketplace plugin** `graftling`, below. The marketplace ships inside the
  installation, so the plugin comes from the same place as the Python; nothing is
  written into your project until the host starts.

Nothing is published to a public marketplace, and a checkout of the repository is no
marketplace: add the one inside your installation.

## Install the marketplace plugin

> Seen on a real host only in print mode (`claude -p`), with the marketplace added from a
> local checkout (before 581 moved it into the installation), the plugin installed at
> project scope and `GRAFTLING_PYTHON` exported in the shell: the plugin loaded, its tool
> created a Claude child and `wait` returned it completed. A missing Python showed in
> `host.log` and as a failed server in `claude mcp list`, and a project with both hosts
> refused with `host-busy`. Not yet seen: the marketplace added from a uv installation,
> interactive Claude Code under the plugin, including `/agents-context` and its advice,
> and notices pushed into the Session. Not yet run: `GRAFTLING_PYTHON` under `env` in
> `settings.json`, user scope and the upgrade steps. Tests cover the host and module with
> stand-ins.

1. Point the plugin at the installation's Python with `GRAFTLING_PYTHON`, in the shell
   you start Claude Code from or under `env` in `~/.claude/settings.json`. Print the
   value with `echo "$AGENTS_PYTHON"`:

   ```json
   {"env": {"GRAFTLING_PYTHON": "/absolute/home/.local/share/uv/tools/graftling/bin/python"}}
   ```

   Claude Code keeps its own copy of a marketplace plugin, so the plugin cannot find the
   installation by itself. Without `GRAFTLING_PYTHON` it tries `python3` on `PATH`, which
   only a development checkout installed into that `python3` satisfies. A set
   `GRAFTLING_PYTHON` is never skipped for `python3`.

2. Add the marketplace from the installation and install the plugin:

   ```sh
   claude plugin marketplace add "$("$AGENTS_PYTHON" -c 'import graftling, pathlib; print(pathlib.Path(graftling.__file__).parent)')"
   claude plugin install graftling@graftling --scope project
   ```

   `--scope project` enables it in this project's `.claude/settings.json`. With the
   default user scope, every directory you start Claude Code in gets a host and its
   `.agents-control/`.

3. Start Claude Code in the project, as in [Start it](#start-it). The plugin's MCP server
   is `subagents`; Claude Code calls its tool `mcp__plugin_graftling_subagents__subagents`
   (before 0.9.0: server `agents`, tool `mcp__plugin_graftling_agents__agents`).

On its first start the host writes `.agents-control/claude-host.json`, the same file
`configuration --write` writes: `cwd` is the directory Claude Code started in, `claude`
is the `claude` on `PATH` (an existing value stays), and every other key you set stays
on later starts. Edit it as under [Minimal configuration](#minimal-configuration).

**Another data root.** To keep the data root outside the project, set
`GRAFTLING_DATA_ROOT` to an absolute directory in the environment Claude Code starts
with, for example `export GRAFTLING_DATA_ROOT=/abs/agents-data` in the shell, or under
`env` in `.claude/settings.local.json`. The same variable chooses the data root of the
Codex, Pi and DSH hosts. With it, the plugin's host keeps `claude-host.json`, `host.log`
and everything else in that directory, whose `root` and `channel_file` are always that
directory's, and the project gets no `.agents-control/`. A relative value, or one that
cannot be created, is refused with a message naming the variable. One directory serves
one running Claude Code host at a time; a second refuses with `host-busy`. The variable
does not apply to `configuration --write` material below, which keeps the `root` in its
own `claude-host.json`. Children inherit the variable; a child holds
`GRAFTLING_CONNECTION` to its owner, so its plugin server refuses before writing anything.

If the plugin cannot find a Python that imports `graftling`, it says what to install and
set, in three places: `/mcp` shows the server `plugin:graftling:subagents` failed, with the
message in its error output; `.agents-control/host.log` ends with a line starting
`graftling plugin: the agents host did not start`; `/agents-context` prints the same
advice instead of `graftling <version> at <python>`.

**Both in one project.** If the project's `.mcp.json` already starts the host through
`configuration --write`, that host serves the project. The plugin's host refuses with
`host-busy`, naming the two ways out (`/mcp`, `host.log`, and every call of the plugin's
tool show it), and the plugin's module stays inactive. The module stands aside only when
`GRAFTLING_CLAUDE_HOST_CONFIG` names this project's `.agents-control/claude-host.json`
(the Session's directory or one above it); a value inherited from another configured
project, such as from a shell started in its Claude Code session, is ignored. Keep one: run
`claude plugin disable graftling@graftling --scope local` in that project, or remove the
`graftling` server (`agents` before 0.6.0) from `.mcp.json`, the two `GRAFTLING_CLAUDE_HOST_*` entries from
`.claude/settings.local.json` and `.claude/skills/agents-claude-host/`.

To upgrade, install the new version with uv as in the [README](../../README.md#install),
run `claude plugin marketplace update graftling` and `claude plugin update graftling@graftling`,
then start a new Claude Code process.

## Generate the plugin files

```sh
PROJECT=/absolute/working-directory
"$AGENTS_PYTHON" -m graftling.claude.host configuration \
  --project "$PROJECT" --claude "$(command -v claude)" --write
```

Without `--write` the command only prints what it would write. With it, the command
prints the list of paths and writes:

| Path under the project | What it is |
| --- | --- |
| `.mcp.json` | The `graftling` MCP server, started with the installation's Python, `$AGENTS_PYTHON` |
| `.agents-control/claude-host.json` | The host configuration |
| `.claude/settings.local.json` | Two `env` entries that point the plugin at this Python and configuration |
| `.claude/skills/agents-claude-host/` | The plugin: its manifest, hooks and `host_context.mjs` |

Running it again on a configured project updates only what the command owns: `command`
and `args` of the `graftling` server, the two `env` entries, `cwd` and `claude` in the host
configuration, and the plugin files. Everything else stays, including other keys of the
`graftling` server such as `env`, other servers and `root`, `channel_file`, `settings` and
`runtimes` in the host configuration. The one exception is the server `agents` that this
command wrote before 0.6.0: when it starts `graftling.claude.host`, it is removed, so the
model does not see two sets of tools. An `agents` server that starts anything else stays. Without `--write`, the printed host configuration
shows the `root` and `channel_file` that would be kept.

If you moved the project, a `root` or `channel_file` inside its old directory moves to
the same relative place in the new one. A path outside the old directory stays as it is.

An existing JSON file that does not parse or does not hold an object stops the command
with an error naming the file, and nothing is written. Fix or remove that file.

## Minimal configuration

The generated file is already enough to start Claude children:

```json
{
  "root": "/absolute/working-directory/.agents-control",
  "cwd": "/absolute/working-directory",
  "claude": "/absolute/path/to/claude",
  "channel_file": "/absolute/working-directory/.agents-control/live/channel.json",
  "settings": {}
}
```

`settings` applies to every Claude child unless `create` chooses otherwise. Its keys
are all optional: `model` (default `claude-opus-5-5`), `reasoning_effort`,
`runtime_home`, `instructions`, `read_paths`, `worktree_access` (default `write`),
`permission_mode` (`auto` by default, or `default`), `base_url` and `api_key_env` (the
name of a variable, never its value).

`config.yml` for connections and presets goes in the data root,
`.agents-control/config.yml`. See [Configuration](../../README.md#configuration).

## Start it

```sh
cd "$PROJECT"
claude
```

Use the same `claude` you named in `--claude`. Approve Claude Code's ordinary folder
trust and project MCP server prompts. Start or resume Claude Code from this exact
directory: a host started from a subdirectory refuses, and elsewhere Claude Code does
not load this project's `.mcp.json`.

> Not yet walked on a real host from a uv-only install: these steps run as written
> in a clean environment. The same host ran on real Claude Code in earlier checks.

## See that it worked

- In the Session, `/agents-context` prints the current Session, `MCP subagents
  registered` and the state of notice push, without a model turn. `/mcp` lists `graftling`,
  and the model calls the tool `mcp__graftling__subagents`.
- Ask the model to use the `subagents` tool, not Claude Code's own Task subagents, to create a Claude
  child that answers one word, then to `wait` for it.
  The result shows `state: standby` and `last_result.outcome: completed`.
- On Claude Code 2.1.314 or later, the transcript shows each message the model sends as a
  row `agents → <child>` with the message drawn as Markdown, and the child's reply as
  `<child> · replied`. A pushed notice shows its sender and event in bold, the content as
  Markdown and a dim facts line (execution, outcome, request ID and token). Earlier
  versions show the same exchange as `graftling:` log lines.
- After you quit, `.agents-control/host.log` ends with `agents host recorded its
  children stopped.` If that line is missing, the host could not confirm a stop or
  was killed before it finished, or an Agent started by an earlier host still has a
  running Runtime, and the lines above it say which. If it was killed with a DSH
  child, a DSH watcher line says the child's process group was stopped, or could not
  be, up to about 15 seconds later.

If every `subagents` call returns `host-unavailable`, the host refused to start.
`.agents-control/host.log` holds the reason as one JSON line; search for `did not
start`. Fix the cause and start Claude Code again.

## Children on other Runtimes

Add a `runtimes` object beside `settings`. A Runtime without an entry fails `create`
with `unsupported-runtime`. Each entry is checked when the host starts.

```json
{
  "root": "/absolute/working-directory/.agents-control",
  "cwd": "/absolute/working-directory",
  "claude": "/absolute/path/to/claude",
  "channel_file": "/absolute/working-directory/.agents-control/live/channel.json",
  "settings": {},
  "runtimes": {
    "codex": {
      "adapter_request": {
        "arguments": ["codex"],
        "worktree_path": "/absolute/working-directory",
        "session_parameters": {
          "cwd": "/absolute/working-directory",
          "model": "your-codex-model",
          "developerInstructions": "",
          "config": {
            "default_permissions": "work",
            "permissions": {
              "work": {"extends": ":workspace", "filesystem": {":workspace_roots": {".": "write"}}}
            }
          }
        }
      }
    },
    "dsh": {"model": "deepseek-flash", "sandbox": "read-only", "api_key_env": "DEEPSEEK_API_KEY"}
  }
}
```

`codex` takes the document [`graftling.codex.host --settings`](codex.md) reads. `dsh`
requires `model` and `sandbox` (`read-only` or `workspace-write`) and needs DSH
0.2.0-rc.2 or later.

For Pi children, first install [what a Pi child needs](../../README.md#what-a-pi-child-needs).
Pi's entry requires `provider` and `model`, and `executable` defaults to `pi` on the
host's PATH:

```json
{
  "root": "/absolute/agents-data",
  "cwd": "/absolute/working-directory",
  "claude": "/absolute/path/to/claude",
  "channel_file": "/absolute/working-directory/.agents-control/live/channel.json",
  "settings": {},
  "runtimes": {
    "pi": {
      "provider": "deepseek",
      "model": "deepseek-flash",
      "api_key_env": "DEEPSEEK_API_KEY",
      "sandbox_python": "/absolute/pi-sandbox/venv/bin/python",
      "sandbox_path": "/absolute/pi-sandbox/srt/node_modules/.bin:/absolute/node/bin:/absolute/ripgrep/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    }
  }
}
```

**Pi children need `root` outside the project.** A Pi child must not read the data
root, and its sandbox cannot deny a path inside a readable one, so the host refuses
this layout at start with `invalid-config`. Set `root` as above and move `config.yml`
and any existing `control/`, `native/` and `sessions/` there. `channel_file` and
`host.log` may stay in the project.

> Not yet walked on a real host: Pi and DSH children of this host. Codex children have
> run on a real host.

## Upgrade

1. Install the new version with uv, as in the [README](../../README.md#install).
2. Run `configuration --write` again. **This is required:** an earlier copy of the
   plugin never acknowledges a notice, so its pushed notices repeat once per host
   process.
3. Start a new Claude Code process.

## Limits

- Push needs prompt submission. In print mode (`claude -p`) a notice arrives only as
  `main_notices` on the next `subagents` result.
- The host serves `subagents` calls concurrently, so a long `wait` does not block a call
  Claude Code sends while it runs. Within one turn Claude Code itself runs the calls
  one at a time until it moves a long one to the background (up to 120 seconds, seen
  on 2.1.314); print mode showed no overlap. A call you cancel gets no response.
- Two identical calls in parallel share one association, and the second gets a
  refusal you can retry.
- A stop agents cannot confirm is reported in `host.log` only: the Session has closed
  and cannot see it. Read the state through `status` on the next host. If Claude Code
  kills the host before it finishes, a child can stay `running` with no notice until a
  later host process.
- Only one host can run per project at a time. Another start is refused like the
  cases above.
</content>
