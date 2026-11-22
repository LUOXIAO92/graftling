# agents on Pi

This makes a Pi Session the host of its child Agents, through a Pi extension. Back to
the [README](../../README.md).

## Prerequisites

- Python 3.12 or later.
- Pi, installed, with a model selected and logged in. There is no version floor in
  agents; the sources and real runs are Pi `1.4.0`.
- A Pi child also needs [its sandbox tools](../../README.md#what-a-pi-child-needs). A
  Claude, Codex or DSH child of a Pi host does not.

## Install the wheel

In a checkout of the repository, or skip the `pip wheel` line and give `pip install`
the path of a wheel you were given:

```sh
AGENTS_VENV=/absolute/agents-venv
python3 -m venv "$AGENTS_VENV"
WHEELS="$(mktemp -d)"
"$AGENTS_VENV/bin/python" -m pip wheel --no-deps -w "$WHEELS" .
"$AGENTS_VENV/bin/python" -m pip install "$WHEELS"/graftling-*.whl
```

## Distribution: `pi install`

The installed directory `graftling/pi/` is a Pi package. Install it with Pi's own
command, from the agents environment Pi should use. It is not on a public market.

```sh
PI_PACKAGE="$("$AGENTS_VENV/bin/python" -c 'import graftling.pi, pathlib; print(pathlib.Path(graftling.pi.__file__).parent)')"
pi install "$PI_PACKAGE"       # for you; add -l for this project only
```

`pi install -l` writes only the project's `.pi/settings.json`. Pi loads that
declaration once the project is trusted (`--approve` trusts it for one run).

> Walked in a sandbox with an isolated `PI_CODING_AGENT_DIR` and no model login (Pi 1.4.0, 572):
> `pi install -l` of this directory from a wheel install, then a Session start. The
> start served `<project>/.agents-control/live/pi-host`. Not yet walked: a full turn
> with the `agents` tool after `pi install` (Task 572's `start` step), and `pi install`
> without `-l`.

## Local files: `-e` for one invocation

Nothing is installed; the extension loads for this one run:

```sh
pi -e "$PI_PACKAGE/extension.mjs"
```

## Data root and configuration

With no flag, the data root is `<project>/.agents-control`. It is created when
missing. The project is the Session's working directory (`ctx.cwd`). In Pi 1.4.0
that is the directory `pi` starts in, because Pi has no option to set it. This is
the Claude Code host's default, and the DSH plugin's, so the hosts share one data
root and each Main sees only its own children.

A Session whose working directory is not an absolute, existing directory gets no
`agents` tool. A data root that cannot be created stops the start too. In both
cases the message names `--agents-config`.

Inside a DSH-managed context (`DSH_SESSION_ID` or `DSH_AGENT_SOURCE` set), the
extension stays inert without the flag. In a Pi child of an agents host
(`GRAFTLING_CONNECTION` set), it uses the owner's connection, as before.

`--agents-config <file>` selects an explicit configuration instead, which works
as before.

### Minimal explicit configuration

`/absolute/pi-host.json`:

```json
{"root": "/absolute/agents-data"}
```

Allowed keys: `root` (required, absolute), `pi`, `claude`, `codex` and `dsh`. Any other
key stops the start.

`config.yml` for connections and presets goes in `root`. See
[Configuration](../../README.md#configuration).

## Start it

```sh
pi                                          # data root <project>/.agents-control
pi --agents-config /absolute/pi-host.json   # an explicit configuration
```

Add `-e "$PI_PACKAGE/extension.mjs"` if you did not `pi install`.

**Which Python.** `GRAFTLING_PYTHON`, when set, names the Python that has
`graftling`. Without it the extension looks for the Python of the installation it
was loaded from:

- the interpreter beside the site-packages holding the extension
  (`<prefix>/lib/pythonX.Y/site-packages/graftling/pi/` gives `<prefix>/bin/pythonX.Y`,
  `python3`, `python`);
- then `python3` and `python` on `PATH`, which covers an editable install from a
  checkout.

A candidate is used only when `python -I` imports `graftling` from that same
directory. This needs no network. If none does, the start stops with a message
naming `GRAFTLING_PYTHON`.

A refused start shows as Pi's `Extension error (<path>/extension.mjs): <message>`. A
host refusal adds one `{"error": {"code", "message"}}` line. In Pi 1.4.0 the Session
still starts and runs its prompt, without the `agents` tool. Before 572 this guide
said the Session start fails; in a real run it did not.

**Turning it off.** The package is the switch:

- `pi remove -l "$PI_PACKAGE"` for a project install. It needs `--approve` in a
  project that is not trusted. Use `pi remove "$PI_PACKAGE"` for a personal install.
- `pi --no-extensions` for one run.
- `pi config` can disable the extension (not walked).

No variable turns the default off. In the 572 sandbox, a start after
`pi remove -l --approve`, and a start with `--no-extensions`, created no
`.agents-control`.

> Walked from a wheel install in a sandbox (Pi 1.4.0, isolated `PI_CODING_AGENT_DIR`,
> no model login, 572), with no flag and no variable:
>
> - the start served `<project>/.agents-control/live/pi-host` with the Python it found;
> - a copy of the package with no interpreter gave the `GRAFTLING_PYTHON` message;
> - `--agents-config` with `GRAFTLING_PYTHON` served the explicit root.
>
> Not yet walked: a model turn that creates a child this way (Task 572's `start` step).

## See that it worked

- The model can call the `agents` tool, through the MCP server `graftling`: it sees
  `mcp__graftling__agents`.
- Ask it to use the `agents` tool to create a Claude child that answers one word. A message
  appears in the Pi Session titled `[agents notice from <agent> · completed · execution <execution>]`.
- `status` then shows `last_result.notification: delivered`, with the child's answer
  still in `last_message`.

Print mode works too, but a Session with no UI delivers held notices only once a turn
has started.

## Children on other Runtimes

`create`'s `runtime` selects the driver. `claude` needs nothing more. A `pi` child
needs its sandbox settings, and `dsh` a model. The sections below set defaults for each:

```json
{
  "root": "/absolute/agents-data",
  "claude": {"model": "claude-sonnet-5-5"},
  "dsh": {"model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY"},
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
    "sandbox_python": "/absolute/pi-sandbox/venv/bin/python",
    "sandbox_path": "/absolute/pi-sandbox/srt/node_modules/.bin:/absolute/node/bin:/absolute/ripgrep/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  }
}
```

- `claude`: the keys of the [Claude host's `settings`](claude-code.md#minimal-configuration).
  Children default to read access. Needs Claude Code 2.1.299 or later.
- `dsh`: `model` is required. Children default to the `read-only` sandbox. Needs DSH
  0.2.0-rc.2 or later.
- `codex`: the document [`graftling.codex.host --settings`](codex.md) reads. Without
  it a Codex `create` fails with `invalid-config`.
- `pi`: overrides of the child's defaults. Without it a child inherits the parent's
  provider, model and Pi. It also takes `sandbox_python`, the Python that has
  `agent_sandbox`, and `sandbox_path`, which must reach `srt` and `node`, and `rg`.
  Children default to `worktree_access: read`. The data root must lie outside the
  directory the Pi child works in, so keep `root` outside the directory you start Pi
  from.

Set a model at `create` to choose another one for one child.

> Not yet walked on a real host: Codex, DSH and Pi children of this host. Claude children
> have run on a real host.

## Upgrade

1. Install the new wheel into the same environment, as above.
2. If you used `pi install`, run it again with the same directory, so Pi loads the new
   extension. With `-e` there is nothing else to do.
3. Start Pi with the same configuration. Resuming the same Session binds a new host,
   which delivers what was held.

> Not yet walked on a real host: `pi install` over an earlier install of this package.

## Limits

- A notice to a Pi parent that is mid-turn is taken at the end of that turn. A
  permission-request notice can therefore arrive after the parent has answered the
  request, and then costs one more model turn. The real run saw this once.
- On `session_shutdown` (quit, reload, new, resume or fork), the host stops every child
  within 10 seconds in total. An Agent it cannot confirm stopped is named in a
  `stop-unconfirmed` line on stderr, the host exits non-zero, and the extension's
  shutdown handler rejects with that text.
- Held notices belong to their Session: a new or forked Session does not get them.
- `agents` calls run one at a time on this host.
- A connection without `base_url` support on Pi: declare the endpoint as a provider in
  Pi's own `models.json` in that connection's home.
- An extension in a connection's home can change a call's input after review, and a
  `mcp.json` server named `graftling` there gets the name the review exempts. That home is
  your own trust decision.
</content>
