# agents on DSH

This makes a DeepSeek Harness (DSH) main Session the host of its child Agents, through
a DSH plugin. Back to the [README](../../README.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/). It provides Python 3.12 or later.
- DSH 0.2.0-rc.2 or later, with a login or an API key. An older DSH, or one whose
  version cannot be read, is refused. The sources and real runs are DSH `0.2.2-alpha.2`.
- pnpm, for `dsh plugin add`, which runs pnpm in the profile directory. `--patch` does
  not need it.

## Install graftling

Once, with uv, as in the [README](../../README.md#install). Every host uses this one
installation:

```sh
uv tool install --managed-python "git+https://github.com/LUOXIAO92/graftling.git@<tag-or-commit>"
AGENTS_PYTHON="$(uv tool dir)/graftling/bin/python"
```

## Distribution: `dsh plugin add`

The directory `graftling/dsh/` of that installation is a DSH plugin bundle named
`graftling-dsh`. Add it with DSH's own plugin command, once per profile. It is not on a
public market.

```sh
DSH_PACKAGE="$("$AGENTS_PYTHON" -c 'import graftling.dsh, pathlib; print(pathlib.Path(graftling.dsh.__file__).parent)')"
dsh plugin --profile headless add "$DSH_PACKAGE"
```

Any profile works the same way: `headless`, `web`, `tui`, `acp` or `sdk`. DSH runs pnpm,
which links the profile's `node_modules/graftling-dsh` to `$DSH_PACKAGE` (it saves
`"graftling-dsh": "link:<that directory>"`); nothing is copied.

Nothing else is needed: the next Session has the `subagents` tool, with its data in the
project's `.agents-control` ([Data root](#data-root-and-configuration)).

> Walked on a real host (569): `dsh plugin add` of an installed `graftling/dsh`
> directory registered `graftling-dsh` as a `link:` dependency, with DSH 0.2.2-rc.5 and
> pnpm 12.21.0. Not yet walked: a Session of a profile added from a uv installation.

### Which Python the plugin runs

The plugin runs the host and the `subagents` MCP server with the Python of the installation
it belongs to. Nothing is built or downloaded, and nothing is written under the DSH home.

| Case | What happens |
| --- | --- |
| `GRAFTLING_PYTHON` set | Used as given, without a check |
| Not set, plugin added from an installation | That installation's Python: the plugin's real path is `<installation>/lib/pythonX.Y/site-packages/graftling/dsh/`, and it runs `<installation>/bin/pythonX.Y`, else `python3`, else `python` there |
| Not set, a development checkout | `python3`, else `python`, on `PATH`, when it imports this very checkout (an editable install) |
| None of these imports the plugin's own graftling | Refused ([A refused start](#a-refused-start)) with `graftling-dsh: no Python that imports this graftling (...) was found; tried ...`, followed by the `uv tool install` command for the plugin's version and `GRAFTLING_PYTHON` as the other way |

A Python counts only when it imports `graftling` from the plugin's own directory, so the
plugin and the Python package are always one installation. A plugin directory copied away
from its installation, such as one installed from a Git address, is refused this way.

## Local files: `--patch` for one invocation

Nothing is installed. Write a patch file that names the plugin, and pass it for this
one run:

```sh
cat > /absolute/agents-host.patch.yml <<EOF
- insert:
    - id: agents-host
      name: $DSH_PACKAGE/plugin.mjs
EOF
```

Then add `--patch /absolute/agents-host.patch.yml` to the `dsh` command below.

## Data root and configuration

### Default: the project's `.agents-control`

Without `GRAFTLING_DSH_CONFIG` and `GRAFTLING_DATA_ROOT` (unset or empty), each main
Session's host keeps its data in
`<project>/.agents-control`, the other hosts' default for the same project. It is
created when missing (only that directory: the project directory already exists). The
host then has no per-Runtime settings; that is the same as a configuration of
`{"root": "<project>/.agents-control"}`.

`<project>` is the Session's working directory, as DSH records it when it creates the
Session:

- `headless`, and the TUI: the directory `dsh` was started in.
- The web interface: the Workspace folder the Session belongs to; a Session outside any
  Workspace gets the directory `dsh web` was started in.

> Inference (source reading of DSH 0.2.2-rc.5): the list above. The TUI and the web
> interface have not been run with this plugin.

A Session that records no working directory, or one that is not an absolute path to an
existing directory, gets no host, and the Session does not start
([A refused start](#a-refused-start)). The message is
`graftling-dsh: this DSH Session records no usable project directory (...)`, which names
`GRAFTLING_DATA_ROOT` and `GRAFTLING_DSH_CONFIG` as the fix. A project directory where `.agents-control` cannot be
created is refused the same way, with `Cannot create the default data root ...`. A home directory is not special: a Session started in
`~` keeps its data in `~/.agents-control`, as Claude Code started in `~` does.

### Another directory: `GRAFTLING_DATA_ROOT`

`GRAFTLING_DATA_ROOT`, an absolute directory, replaces `<project>/.agents-control` for
every Session of that DSH. It is the one variable that chooses the data root of every
host (Claude Code, Codex, Pi and DSH), and the preferred way to choose it:

```sh
export GRAFTLING_DATA_ROOT=/absolute/agents-data
```

It is created when missing; a relative value, or one that cannot be created, stops the
start with a message naming the variable. It chooses only the directory: per-Runtime
settings still need [`GRAFTLING_DSH_CONFIG`](#explicit-configuration-graftling_dsh_config),
which wins when both are set. Children inherit the variable as they inherit any other;
a DSH child holds `GRAFTLING_CONNECTION`, so its plugin stays inactive
([Turning it off](#turning-it-off)).

### Sharing `.agents-control` with Claude Code

The DSH host and the [Claude Code host](claude-code.md) can serve the same project at the
same time on one `.agents-control`:

- Each main Session sees only the children it created (`status`, `wait`, notices); a
  child of the other host is not found.
- Records are locked per Agent and replaced atomically, the same way several DSH
  Sessions already share one configured root.
- The DSH host's endpoint is a new directory under `live/dsh-host/`; it never writes
  Claude Code's `claude-host.json`, `host.log` or `live/channel.json`. Claude Code's
  one-host-per-project check does not count a DSH host.
- `config.yml` is read by both. A connection one host has no driver for, or cannot run on
  this machine, stops that host's start (`unsupported-runtime` or `runtime-unavailable`,
  naming the connection). The Claude Code host has only Claude children unless its
  configuration adds others; the DSH host has DSH, Claude and Codex drivers by default,
  Pi only with a `pi` section. Declare in a shared `config.yml` only connections both
  hosts can run, or give one host its own root.
- Archives (`save_normalized_sessions`) are per Session ID, so they do not collide.

A Claude Code project whose `claude-host.json` names another `root` keeps its data there;
the DSH host does not read that file. Set `GRAFTLING_DATA_ROOT` to that `root` for DSH,
or point `GRAFTLING_DSH_CONFIG` at a configuration with the same `root`, to share it.
The same `GRAFTLING_DATA_ROOT` for both hosts shares one directory without either file.

### Explicit configuration: `GRAFTLING_DSH_CONFIG`

When `GRAFTLING_DSH_CONFIG` names a file, it is used instead, for every Session of that
DSH, whatever its working directory, and whatever `GRAFTLING_DATA_ROOT` says. It is kept
from earlier releases; use it for per-Runtime settings. `/absolute/dsh-host.json`:

```json
{"root": "/absolute/agents-data"}
```

Allowed keys: `root` (required, absolute), `dsh`, `claude`, `codex` and `pi`. Any other
key stops the start. Per-Runtime settings ([below](#children-on-other-runtimes)) need
this file.

`config.yml` for connections and presets goes in the data root. See
[Configuration](../../README.md#configuration).

### Turning it off

Remove the plugin from the profile: `dsh plugin --profile <profile> remove graftling-dsh`.
Other profiles keep it. The plugin also stays inactive inside a DSH that is itself an
Agent (`GRAFTLING_CONNECTION` or `DSH_AGENT_PACKAGE` set), whatever `GRAFTLING_DATA_ROOT`
or `GRAFTLING_DSH_CONFIG` says. A switch on the web Plugins
page has not been walked.

## Start it

```sh
env -u DEEPSEEK_BASE_URL dsh --profile headless "Your task."
```

No `GRAFTLING_PYTHON` is needed ([Which Python](#which-python-the-plugin-runs)). Add
`GRAFTLING_DATA_ROOT=/absolute/agents-data` for
[another directory](#another-directory-graftling_data_root), or
`GRAFTLING_DSH_CONFIG=/absolute/dsh-host.json` for an
[explicit configuration](#explicit-configuration-graftling_dsh_config). The `env -u`
concerns only this main Session's own endpoint, which DSH takes from that variable when
its profile names none. DSH children ignore that variable.

To continue a `headless` Session, from the same directory:

```sh
env -u DEEPSEEK_BASE_URL dsh --profile headless --session-id "<session id>" "Next task."
```

### A refused start

A refused host start makes the DSH Session fail to start. The host prints one
`{"error": {"code", "message"}}` line, or the plugin refuses before starting it, and
DSH's `agent/created` handler fails with that text. Since the plugin is on by default,
this covers a plugin without its installation's Python, a Session without a usable working directory
and a `.agents-control` that cannot be created. Observed in `headless`: the run prints
only `dsh: graftling-dsh: ...` with the message and exits 1; the task does not run.
Fix the cause the message names, or remove the plugin from the profile
([Turning it off](#turning-it-off)), and start again. The TUI and the web interface have
not been run with a refused start.

> Not yet walked on a real host from a uv-only install: these steps run as written
> in a clean environment. The same host ran on real DSH 0.2.2-alpha.2 with `--patch` in
> earlier checks.

## See that it worked

- The model can call the `subagents` tool, through the MCP server `graftling`: it sees
  `mcp__graftling__subagents`.
- Ask it to use the `subagents` tool, not DSH's own subagents, to create a Claude child that
  answers one word, and to `wait` for it before ending the task.
- A notice appears in the Session as `<agent> · completed` over the answer as Markdown; DSH's
  native card under it holds the model's text `[Graftling notice from <agent> · completed ·
  execution <execution>]`, the answer and a `Facts:` line.
- `status` shows `last_result.notification: delivered` and `state: standby`.

## Children on other Runtimes

`create`'s `runtime` selects the driver. `dsh` and `claude` need nothing more. The
sections below set defaults for each:

```json
{
  "root": "/absolute/agents-data",
  "dsh": {"sandbox": "workspace-write", "api_key_env": "DEEPSEEK_API_KEY"},
  "claude": {"model": "claude-sonnet-5-5"},
  "codex": {
    "adapter_request": {
      "arguments": ["codex"],
      "worktree_path": "/absolute/work",
      "session_parameters": {
        "cwd": "/absolute/work",
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
  "pi": {
    "executable": "/absolute/path/to/pi",
    "provider": "deepseek",
    "model": "deepseek-flash",
    "agent_dir": "/absolute/home/.pi/agent",
    "instructions": "",
    "worktree_access": "read",
    "api_key_env": "DEEPSEEK_API_KEY",
    "sandbox_python": "/absolute/pi-sandbox/venv/bin/python",
    "sandbox_path": "/absolute/pi-sandbox/srt/node_modules/.bin:/absolute/node/bin:/absolute/ripgrep/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  }
}
```

- `dsh`: the sandbox defaults to `read-only`, and the model to the parent's current one.
- `claude`: the keys of the [Claude host's `settings`](claude-code.md#minimal-configuration).
  Children default to read access. Needs Claude Code 2.1.299 or later.
- `codex`: the document [`graftling.codex.host --settings`](codex.md) reads. Without
  it a Codex `create` fails with `invalid-config`.
- `pi`: the whole Pi child request, every field above but `api_key_env` is required, and
  `worktree_path` is the host's working directory. Without it a Pi `create` fails with
  `unsupported-runtime`. It needs [what a Pi child needs](../../README.md#what-a-pi-child-needs),
  and the data root must lie outside the directory the Pi child works in.

Set a model at `create` to choose another one for one child.

> Not yet walked on a real host: Codex, DSH and Pi children of this host. Claude
> children have run on a real host.

## Upgrade

1. Install the new version with uv, as in the [README](../../README.md#install). The
   installation stays in the same directory, so the profile's link keeps pointing at it.
2. Run `dsh plugin add "$DSH_PACKAGE"` again for each profile, so DSH reads the new
   bundle's version and patch; it is required when uv picked another Python minor
   version, which moves `$DSH_PACKAGE`. With `--patch`, use the new `$DSH_PACKAGE`.
3. Start DSH with the same configuration. Reopening a Session binds a new host, which
   delivers what was held, each once.

> Not yet walked on a real host: `dsh plugin add` over an earlier install of this package.

### Moving from the 0.7.0 DSH install

0.7.0 also installed `graftling-dsh` from this repository's GitHub address (the Plugins
page or `dsh plugin add "git+https://github.com/LUOXIAO92/graftling.git#<commit>"`), and
that plugin built its own copy of graftling under `$DSH_HOME/graftling-python`. Both are
gone: the bare address now registers no bundle, and the `&path:/src/graftling/dsh`
subdirectory form gives a plugin without its Python, which refuses to start with the
`uv tool install` message. A profile that already has the 0.7.0 plugin keeps running
it, and its copy of graftling, until you move. To move to the one installation:

1. Remove the plugin from every profile that has it:
   `dsh plugin --profile <profile> remove graftling-dsh`.
2. With every DSH process stopped, delete the environment it built:
   `rm -rf "${DSH_HOME:-$HOME/.dsh}/graftling-python"`.
3. [Install graftling](#install-graftling) with uv and
   [add the plugin](#distribution-dsh-plugin-add) from it to each profile again.

The data root is unchanged, so held notices and children's records stay.

## Limits

- A notice reaches a running parent at its next step, after the current model response
  or tool call.
- A `headless` DSH exits once its task is idle, without waiting for agents children, and
  then its children are stopped. So a parent that needs a child's answer within its task
  calls `wait` before ending it. Later notices arrive when you continue the Session.
- While DSH or the Session is stopped, nothing is delivered and notices are held. On
  resume each arrives once, unless the parent read it through `status` or `wait` first.
- A notice admitted between the `headless` runner's last Session write and its exit may
  not reach the Session file, and it stays `delivered`. This is in upstream DSH.
- An Agent the host cannot confirm stopped is named in a `stop-unconfirmed` line on
  stderr, the host exits non-zero, and the plugin writes that text to the DSH logger.
  The Session does not show it; read the state through `status` on the next host.
- `subagents` calls run one at a time on this host.
- The DSH driver runs the `deepseek-official` provider only.
- `DEEPSEEK_BASE_URL` and `DEEPSEEK_SEARCH_BASE_URL` do not route a DSH child. Declare
  `base_url` on the connection or the DSH settings. With `DEEPSEEK_API_KEY` as that
  connection's key at another origin, web search goes to the same endpoint and DSH's
  Search and Fetch API provider is off. To keep official search beside a gateway, name
  the gateway's key in another variable.
- A native DSH subagent gets no host and no `subagents` tool.
</content>
