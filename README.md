# Graftling

**Runtime-Agnostic Hierarchical Multi-Agent Orchestration**

Graftling lets the main Agent of Claude Code, Codex, Pi or DeepSeek Harness (DSH)
start and control child Agents through one tool, `subagents`. A child can run on any of those
four Runtimes and keeps its own native Session there. It needs no project, Task or
Git repository.

The distribution is called `graftling`. It installs two commands, `subagents` and
`subagents-mcp`, and the Python package `graftling`. Before 0.9.0 the tool and the
commands were named `agents` and `agents-mcp`; see [Moving to 0.9.0](#moving-to-090).

- [`PUBLIC_API.md`](PUBLIC_API.md): the stable interface and the version rules.
- [`CHANGELOG.md`](CHANGELOG.md): what changed in each version.
- Host notes: [Claude Code](docs/install/claude-code.md), [Codex](docs/install/codex.md),
  [Pi](docs/install/pi.md), [DSH](docs/install/dsh.md).

The code is in `src/graftling`; `pyproject.toml` at the repository root builds it.
The wheel carries the code, the files it reads at run time, `PUBLIC_API.md` and the
install guides; the README and the changelog stay in the repository.

Run the tests from a checkout with an editable install:
`python -m pip install -e . pytest`, then `python -m pytest`.

Licensed under the [Apache License, Version 2.0](LICENSE).

## How it works

Your main Agent runs inside a **host**: Claude Code, Codex, Pi or DSH with agents
attached. The host owns the children and answers the `subagents` tool. Children report
back to the parent that created them.

The `subagents` command and the `subagents-mcp` server only forward a request to the host
that owns the calling process. Run from a plain shell, or registered by hand in
another MCP client, every request fails with `authority-denied`, including
`discover`. So attaching a host is the first step, and each host registers the
`subagents` tool for you. The model sees it as `mcp__graftling__subagents`, or as
`mcp__plugin_graftling_subagents__subagents` under the Claude Code marketplace plugin.

## Install

Install graftling once, with [uv](https://docs.astral.sh/uv/), straight from GitHub.
It is not on PyPI. Pin a tag or a commit:

```sh
uv tool install "git+https://github.com/LUOXIAO92/graftling.git@<tag-or-commit>"
AGENTS_PYTHON="$(uv tool dir)/graftling/bin/python"
"$(uv tool dir --bin)/subagents" --help
```

uv gets a Python 3.12 or later when it needs one, installs graftling with its only
dependencies, PyYAML and websockets, in its own environment under `uv tool dir`, and
puts the `subagents` and `subagents-mcp` commands on its tool bin directory
(`uv tool dir --bin`). This one installation serves every host: each host's MCP
server and hook plugin come from it, as the table says. `$AGENTS_PYTHON` is that
installation's Python; the host notes use it for every `python -m graftling...`
command. Nothing else installs graftling: no host plugin builds or carries a copy of
its own.

A package that depends on graftling, such as GraphTraj, installs it into that
package's own environment instead, from the same GitHub source. Then that
environment is the one installation: use its Python as `$AGENTS_PYTHON`, and the
plugins find it the same way. Do not install graftling a second time with uv.

Install and log in to each Runtime separately; agents never copies credentials. Then
follow the note for the host you use:

| Your main Agent runs in | Note | How the host is attached to the one installation |
| --- | --- | --- |
| Claude Code | [docs/install/claude-code.md](docs/install/claude-code.md) | `"$AGENTS_PYTHON" -m graftling.claude.host configuration --write` generates the MCP server and the plugin files into your project from it; or the `graftling` plugin from the marketplace inside it (real only in print mode, `claude -p`) |
| Codex | [docs/install/codex.md](docs/install/codex.md) | `"$AGENTS_PYTHON" -m graftling.codex.host` runs a headless parent. No plugin |
| Pi | [docs/install/pi.md](docs/install/pi.md) | `pi install` of its `graftling/pi` directory, or `-e` for one invocation |
| DSH | [docs/install/dsh.md](docs/install/dsh.md) | `dsh plugin add` of its `graftling/dsh` directory, or `--patch` for one invocation. The plugin runs that installation's Python |

To upgrade, install the new tag or commit the same way with `--reinstall`. The
installation stays where it was, so the DSH and Pi plugins keep pointing at it; the
Claude Code notes say what to run again.

Coming from 0.7.0's DSH install from this repository's GitHub address, see
[Moving from the 0.7.0 DSH install](docs/install/dsh.md#moving-from-the-070-dsh-install).

## Use the tool

Ask your main Agent to use the `subagents` tool. It takes three actions: `discover`
lists the features, `describe` gives one feature's input, and `execute` runs it.

```json
{"action": "execute", "feature": "create",
 "arguments": {"runtime": "codex", "instruction": "Summarize README.md.",
               "name": "reader", "model": "your-model", "reasoning_effort": "low"}}
```

| Feature | What it does |
| --- | --- |
| `create` | Starts a child. Requires `runtime` and `instruction`. Optional: `name`, `preset`, `model`, `reasoning_effort`, `workspace`, `extra_fields`, and `configuration`, which every independent host refuses with `invalid-config` |
| `status` | Shows one child, or without `agent` every child you can see |
| `wait` | Waits for a child, up to the `timeout_seconds` you must give. Ends early on an unanswered request |
| `send` | Sends a message to a child; continues it if it is in standby |
| `interrupt` | Stops a child's current work |
| `resume` | Continues a child in its native Session; needs `message` in standby |
| `requests`, `reply` | Read a child's permission requests and answer each once |
| `retire` | Ends a child for good |
| `ask_parent` | Called by a child: asks its parent to decide something or grant resources, and waits for the answer |
| `session_read` | Reads the archived Session of yourself, an ancestor or a descendant by `agent` or `session`: a Python slice of its events (`start`, `stop`; by default the last 5), conversation text unless `content` asks for `tools` or `all`, or a keyword `search` returning hit positions. Credentials are redacted; needs `save_normalized_sessions` on |

`model` must be a known candidate, and agents never silently replaces it. When a
Runtime answers with another model, as with a safeguard fallback, the child ends
`failed` with `RUNTIME_REQUEST_FAILED`, and `last_result.error` names the requested
model, the observed model and the reason. A Claude alias such as `haiku` is the
one stated exception: Claude may start it as the full id its own model catalog
names for it, such as `claude-haiku-5-5`. `model` keeps the alias, and `status`
shows the full id as `resolved_model`. You can also request that full id
directly ([`PUBLIC_API.md`](PUBLIC_API.md) section 2, rule 5). A Claude child may also
run under another permission mode than its `permission_mode`. A narrower one, such as
`default` for `auto`, where Claude asks more, lets it complete, and `status` shows
both modes as `permission_mode` for the latest execution only. A wider or unknown one fails it with
`RUNTIME_REQUEST_FAILED` ([`PUBLIC_API.md`](PUBLIC_API.md) 4.1). Every other code is in
[`PUBLIC_API.md`](PUBLIC_API.md) 1.2.

Inside a host, a process the host started can also call the same request as
`subagents '{"action":"discover"}'`, which prints one JSON document.

## Configuration

agents reads one optional file, `config.yml`, in its data root, and native login
state. It reads no other product's configuration.

Every host's data root is `<project>/.agents-control` by default, where the
project is the Session's working directory, so the hosts of one project share it.
To choose another directory for every host, set one variable before starting the
host:

```sh
export GRAFTLING_DATA_ROOT=/abs/agents-data   # absolute; created when missing
```

This is the preferred way to choose the directory. A host's own setting, kept from
earlier releases, still wins when given:

| Host | Data root, first that applies |
| --- | --- |
| Claude Code | `root` in configuration `--write`'s `<project>/.agents-control/claude-host.json` (its `GRAFTLING_CLAUDE_HOST_CONFIG`); with the plugin, `GRAFTLING_DATA_ROOT` (which then also holds `claude-host.json`); else `<project>/.agents-control` ([Claude Code install](docs/install/claude-code.md)) |
| Codex | `--directory`; `GRAFTLING_DATA_ROOT`; else `<Session cwd>/.agents-control` ([Codex install](docs/install/codex.md)) |
| Pi | `root` in the JSON file `--agents-config` names; `GRAFTLING_DATA_ROOT`; else `<project>/.agents-control` ([Pi install](docs/install/pi.md#data-root-and-configuration)) |
| DSH | `root` in the JSON file `GRAFTLING_DSH_CONFIG` names; `GRAFTLING_DATA_ROOT`; else `<project>/.agents-control` ([DSH install](docs/install/dsh.md#data-root-and-configuration)) |

`GRAFTLING_DATA_ROOT` only chooses the directory; per-Runtime child settings stay
in the host's own file (`--agents-config`, `GRAFTLING_DSH_CONFIG`, Codex
`--settings`). It is an ordinary variable, so children inherit it; a child holds
`GRAFTLING_CONNECTION` to its owner and starts no host of its own.

Reading is strict: an unknown or repeated field or a wrong type stops the read, and
the message names the file and line. Each host also checks every declared connection
when it starts, so a Runtime that has no driver in that host, is not installed or is
not logged in stops the start with its own code.

### Connections

A connection says how to reach a Runtime. Without one, a child uses the host's
settings and the Runtime's own login.

```yaml
connections:
  work-claude:
    runtime: claude
    models:
      - claude-opus-5-5
      - id: claude-sonnet-5-5
        supported_efforts: [low, high]
  deepseek-dsh:
    runtime: dsh
    provider: deepseek-official
    api_key_env: DEEPSEEK_API_KEY
```

`runtime` is required: `claude`, `codex`, `pi` or `dsh`. Optional: `home` (the native
home), `provider`, `base_url`, `api_key_env` (the name of an environment variable,
never a key), `review`, and `models` (without it, agents asks the Runtime, which can
make `create` take up to about 30 seconds).

If several connections declare one Runtime, `create` needs a preset that selects one,
else it fails with `ambiguous-connection`.

### What a child's environment holds

A child receives only its own connection's key. Every other variable whose name marks it
as a credential stays with the host: anything with a `_`-separated part ending in `KEY`,
`TOKEN`, `SECRET`, `PASSWORD` or `CREDENTIAL`, such as `HF_TOKEN`, `AWS_SECRET_ACCESS_KEY`
or `PGPASSWORD`. Ordinary variables such as `PATH` and `HOME` are unchanged. A connection
naming no `api_key_env` keeps the variables that Runtime logs in with, and a Runtime's own
login file is untouched. A reviewer's key is not handed to the child it judges.

To give a child another variable, declare its name in your configuration. A model cannot
ask for one through `create`, which refuses it:

```yaml
pass_env: [GH_TOKEN]              # a child with no connection
connections:
  work-claude:
    runtime: claude
    api_key_env: WORK_KEY
    pass_env: [GH_TOKEN]          # this connection's children; replaces the list above
```

Declaring a reviewer's key does not hand it over. For a DSH child to use official web search
beside a connection that names another key, declare `DEEPSEEK_API_KEY`.

This is not a guard against a model reading what it can reach. Three cases are not protected:

- **A top-level host you start from your own shell.** Its environment is yours.
- **The child's own key.** It can still read that.
- **Plaintext files the child can read**, such as `~/.zshrc` and anything it loads, and shells
  that reload them. Pi's and DSH's shells reload none of these, and DSH removes credential names
  from its shell as well. Claude Code's has not been observed; expect any zsh to read `~/.zshenv`.

**A Codex child's shell.** Codex reloads your dotfiles into its tool shell and writes the result
in plaintext to `$CODEX_HOME/shell_snapshots`. So agents sets Codex's `shell_environment_policy`
to exclude credential names on every Codex child, first start and resume. Exports from
`~/.zshrc` and anything it sources, whether guarded by `[[ -o interactive ]]` or not, then stay
out of the shell and the snapshot (Codex 0.164.0). What this does not stop:

- `~/.zshenv`: zsh runs it after Codex sets the environment, so exports there reach the shell.
- `~/.zprofile` and `~/.zlogin`, before Codex's snapshot is ready: a shell started then is a login
  shell, which runs them. The snapshot itself still leaves them out. Keep keys out of those files.
- The files still run. A command in them still executes; only credential names are filtered from
  what the shell keeps. The model can still read the files.
- A name the globs cannot express: a middle `KEY` part followed by a digit, such as `MY_KEY_2`,
  because that shape must spare Git's `GIT_CONFIG_KEY_0`. Such a name is still removed from what a
  child inherits; only a dotfile's export of it gets through.
- Old plaintext in a native home's `shell_snapshots` from earlier runs. agents does not remove it.

The policy applies to tool shells only. Codex's own process keeps the child's key for model access,
but the child's shell no longer sees it. For a Codex child, a declared credential name is sent to
Codex in the Session's parameters, because the globs have no way to spare one name. agents writes
it nowhere; whether Codex writes it into a Session rollout during a turn has not been verified.
Your own `shell_environment_policy` in the home's `config.toml` is kept: agents adds to your
`exclude` list, and your `set` and other fields stay.

The rule matches names, so a URL holding a credential is not caught, and `SSH_AUTH_SOCK` stays
because Git over SSH needs it. See `PUBLIC_API.md` section 3.1 for the exact rule.

### Presets

A preset names a choice you reuse. `create` selects it with `preset` and still gives
`runtime`.

```yaml
connections:
  work-claude:
    runtime: claude
presets:
  careful:
    connection: work-claude
    model: claude-opus-5-5
    reasoning_effort: high
  quick-codex:
    runtime: codex
    reasoning_effort: low
```

A preset names exactly one of `connection` and `runtime`. An explicit `model` or
`reasoning_effort` on `create` must equal the preset's; a different value is an
error, not an override.

### Archive

```yaml
save_normalized_sessions: true
backfill_history: false
```

With `save_normalized_sessions: true`, every execution of every Runtime writes its
normalized Session to `<data root>/sessions/<sha256 of the Session ID>/session.jsonl`.
The default is `false`. Switching it back stops later saves and keeps what was
written. The format is in [`PUBLIC_API.md`](PUBLIC_API.md) 1.5, and `read_session`
reads it.

### Review connections

Where a Runtime's own approval is missing or unusable, for example through a
third-party provider, declare a reviewer on the connection. agents does not detect
this for you: `review` is your statement that native approval cannot be relied on.
A connection without it keeps native approval and no reviewer ever runs.

```yaml
connections:
  gateway-claude:
    runtime: claude
    review: local-review
  gateway-pi:
    runtime: pi
    review: own-script
reviewers:
  local-review:
    base_url: http://127.0.0.1:1234/v1
    model: tuned-reviewer
    timeout_seconds: 20
  own-script:
    command: [/absolute/reviewer, --strict]
    timeout_seconds: 30
```

A reviewer is either a chat completions endpoint (`base_url`, `model`, optional
`api_key_env`, `json_mode`, `timeout_seconds`) or a local process (`command`, optional
`timeout_seconds`). It answers with `{"decision": "allow" | "deny" | "escalate",
"reason": "..."}`. Only `allow` lets the call run. Anything else, including a bad
answer or a failure, goes to the direct parent.

`allow` does not change the child's native permission mode. A Claude child with
`permission_mode: default` can still raise Claude's own prompt for an allowed call. agents
answers that prompt itself, once, when it names the same tool and input. A prompt that
names a `blocked_path`, asks to leave the sandbox or changes the input goes to the
direct parent as an ordinary permission request, which you answer with `reply`.

`create` calls the reviewer once with a harmless request. If that fails, `create`
fails at once with `review-unavailable` and the cause, and no Agent is recorded. The
reviewer never defaults to the child's own provider.

### What a Pi child needs

A Pi child always runs in a native sandbox, so its host must reach four things beyond
Pi. Installing graftling does not install them:

| Needed | Where it comes from |
| --- | --- |
| A Python that can import `agent_sandbox` (setting `sandbox_python`) | `agent-sandbox` 0.1.0, commit `705d8801356e758819dfbbe4fba7022fe96475f8` of `https://github.com/anonx3247/agent-sandbox.git` |
| `srt` on the child's PATH (setting `sandbox_path`) | npm package `@anthropic-ai/sandbox-runtime` 0.0.49 |
| `node` on the child's PATH | Node.js |
| `rg` on the child's PATH | ripgrep. `srt` does not start without it |

```sh
PI_SANDBOX=/absolute/pi-sandbox
python3 -m venv "$PI_SANDBOX/venv"
"$PI_SANDBOX/venv/bin/python" -m pip install \
  "git+https://github.com/anonx3247/agent-sandbox.git@705d8801356e758819dfbbe4fba7022fe96475f8"
npm --prefix "$PI_SANDBOX/srt" install --ignore-scripts --save-exact \
  @anthropic-ai/sandbox-runtime@0.0.49
```

Then set `sandbox_python` to `$PI_SANDBOX/venv/bin/python`, and `sandbox_path` to
`$PI_SANDBOX/srt/node_modules/.bin`, the directories holding `node` and `rg`, and
`/usr/bin:/bin:/usr/sbin:/sbin`. All values are absolute.

What happens when one of these is missing depends on the host:

- **Claude Code and Codex hosts** check their `runtimes.pi` entry when they start. A
  `sandbox_python` that does not run or cannot import `agent_sandbox`, or `srt` or `node`
  not on the child's PATH, stops the start with `runtime-unavailable`, and the message
  names the setting that fixes it.
- **Pi and DSH hosts** do not check these when they start. The host starts, and the Pi
  child's own start fails: `create` returns a failed result with
  `last_result.outcome: failed`, and the error message holds the cause.

`rg` is outside the Claude Code and Codex check too: without it the child itself fails,
on any host.

The agents data root must also lie outside the directory the Pi child works in,
because the child must not read it. See the note for your host.

> Not yet walked on a real host: these install commands as written. The real runs
> used an equivalent install at the same two pinned sources.

## What works today

**Real** means seen on a real host with the real Runtime. **Tests** means shown only
by automated tests with stand-in Runtimes, so treat it as untried on a real host. A
real run of the whole Runtime × Runtime matrix is not finished yet. Everything marked
Tests is expected to work and has not been seen to.

| Capability | Claude Code host | Codex host | Pi host | DSH host |
| --- | --- | --- | --- | --- |
| Attach as host | Yes, with `configuration --write`. `/agents-context` shows the binding. Real. The marketplace plugin `graftling`: real in `claude -p` at project scope (`create`, `wait`); interactive use untried | Yes, a headless parent that runs one instruction per invocation; `--resume` continues it. Real | Yes, as a Pi extension. Real with `-e`; `pi install` untried | Yes, as a DSH plugin. Real with `--patch`; `dsh plugin add` untried |
| `create`, `status`, `wait`, `send`, `interrupt`, `resume`, `retire` | Yes. Real: `create`, `wait`, `send`, `resume`. Tests: `interrupt`, `retire` | Yes. Real: `create`, `wait`, `status`, `send`, `resume`. Tests: `interrupt`, `retire` | Yes. Real: `create`, `status`, `wait`, `resume`. Tests: `send`, `interrupt`, `retire` | Yes. Real: `create`, `wait`, `send`, `resume`. Tests: `interrupt`, `retire` |
| Choose the child Runtime | `claude` always. `codex`, `dsh`, `pi` with a `runtimes` entry, else `unsupported-runtime` | `codex` always. `claude`, `dsh`, `pi` with a `runtimes` entry, else `unsupported-runtime` | `pi`, `claude` always, `dsh` with a model. `codex` needs a `codex` section, else `invalid-config` | `dsh`, `claude` always. `codex` needs a `codex` section, else `invalid-config`; `pi` needs a `pi` section, else `unsupported-runtime` |
| Choose model or effort per child | Yes. Real on a Codex child: model equal to its default, effort `low` different from it | Yes. Real: a Claude child's model and effort, a DSH child's effort | Yes. Real: a Claude child's model. Tests: effort | Yes. Real: a Claude child's model. Tests: effort |
| Notices to the parent, each once | Pushed into the Main Session, or attached as `main_notices` to the next result. Real: the next-result form. Tests: the push | Host events during the parent's turn; `--resume` delivers what was held. Real | A message in the Pi Session, starting a turn when idle. Real: on a busy parent. Tests: on an idle parent, after a resume | A message through DSH's relay. Real: on a running Session. Tests: holding and delivery on resume |
| Relay of approval requests | One notice per request; `wait` ends early; `requests` and `reply`. Tests | The same. Tests under this host; a real Codex child's refusal reached a test parent through the Python interface. Under `auto_review` Codex decides first and only refusals and failures reach the parent | The same. Real: a Claude child's request, once. Its notice can arrive after the parent has already answered | The same. Tests |
| Review plugin | Children on all four Runtimes, through a connection's `review`. Each adapter is real with a test parent through the Python interface. Tests: under this host | As Claude Code host | As Claude Code host | As Claude Code host |
| Archive | Yes, with `save_normalized_sessions`. Tests | Yes. Real: a Codex child | Yes. Tests | Yes. Tests |
| Children on other Runtimes | Real: Codex. Tests: DSH, Pi | Real: Claude, DSH. Tests: Pi | Real: Claude. Tests: Codex, DSH | Real: Claude. Tests: Codex, Pi |
| Grandchild notices | Go to the child that is the grandchild's parent, on any Runtime, once each; held while it is stopped. Tests | As Claude Code host | As Claude Code host | As Claude Code host |
| Host shutdown | Stops every child this process runs, up to 10 seconds in total. Their ends are held for the next run. Real: a confirmed stop. Tests: an unconfirmed one, reported as `stop-unconfirmed` in `host.log`; a killed host's DSH child, whose process group a watcher stops and reports in `host.log` | The same, reported on stderr and in `stop_unconfirmed` in `--output`. Real: a confirmed stop. Tests: an unconfirmed one | The same, reported on stderr, and the extension's shutdown handler rejects. Tests | The same, reported on stderr and in DSH's logger. Tests |

A Pi child has not yet run under a real host parent. Real Pi children have only run
with a test parent through the Python interface. `ask_parent` from a real Pi child has
not run at all.

The real runs used DSH `0.2.2-alpha.2`, Pi `1.4.0`, `codex-cli` 0.164.0 and Claude
Code 2.1.312 and 2.1.314, and each ran on its own Task's code before later merges. A
real run of this version as a whole is still to come. In particular the stop code
changed in 0.2.2 and 0.2.4, after the real shutdown runs.

## Moving from agent-swarm 0.4

Version 0.5.0 renamed the distribution. Nothing reads the old names, so after
upgrading:

- Install `graftling` and uninstall `agent-swarm`; code imports `graftling` instead of
  `agent_swarm`, and a host plugin registers in the entry point group `graftling.hosts`.
  The `agents` and `agents-mcp` commands and the `agents` tool keep their names.
- Rename your own variables: `AGENT_SWARM_<NAME>` is now `GRAFTLING_<NAME>`, for example
  `GRAFTLING_PYTHON`, `GRAFTLING_DSH_CONFIG`, `GRAFTLING_PI_REVIEW` and the `GRAFTLING_PI_*`
  and `GRAFTLING_DSH_*` launch settings.
- Claude Code: run the host install again. It rewrites `.claude/settings.local.json`
  with the `GRAFTLING_CLAUDE_HOST_` variables; the old `GRAPHTRAJ_CLAUDE_HOST_` lines
  no longer start the host.
- Pi and DSH: reinstall the packages, now named `graftling-pi` and `graftling-dsh`.
- Codex now sees the tool as `mcp__graftling__agents`. Earlier Sessions show agents
  messages under the old names; Session archives written before stay readable.

## Moving to 0.6.0

The MCP server that serves the `agents` tool is now named `graftling` on Claude Code,
Pi and DSH, as it already was on Codex, so the model sees `mcp__graftling__agents`
instead of `mcp__agents__agents`. The tool keeps its name. After upgrading:

- Claude Code host: run `configuration --write` again. It writes the `graftling` server
  and removes the `agents` server it wrote before; restart Claude Code. The marketplace
  plugin keeps its name (`mcp__plugin_graftling_agents__agents`).
- Pi and DSH: reinstall the packages; nothing else changes.
- Review exempts only the `agents` tool of the server the child registered; an `agents`
  tool on any other MCP server is reviewed.
- Sessions created before keep their earlier calls under `mcp__agents__agents`. Archives
  still read that name, and DSH's browser shows those calls in its native Tool card like
  any other. Resuming such a Session: see
  [`CHANGELOG.md`](CHANGELOG.md) 0.6.0.

## Moving to 0.9.0

The MCP tool `agents` is now named `subagents`, and the commands `agents` and
`agents-mcp` are now `subagents` and `subagents-mcp`. There is no alias: the old names
are gone. The model sees `mcp__graftling__subagents` instead of `mcp__graftling__agents`.
After upgrading:

- Claude Code host: run `configuration --write` again, so the project's hook plugin
  matches the new tool, and restart Claude Code; `/agents-context` then shows
  `MCP subagents registered`. The marketplace plugin's server is now `subagents`, so its
  tool is `mcp__plugin_graftling_subagents__subagents` (was
  `mcp__plugin_graftling_agents__agents`).
- Pi and DSH: reinstall the packages from the new installation; nothing else changes.
- Change everything that names the old tool or commands: Claude Code allow rules
  (`permissions.allow`) and `--allowedTools` values, scripts that call `agents` or
  `agents-mcp`, and a create's `tools` or `disallowed_tools` entries. A
  `disallowed_tools` entry naming `mcp__<server>__agents` no longer denies anything.
- Review exempts only the `subagents` tool of the server the child registered. A tool
  named `agents` is reviewed like any other.
- Unchanged: the `tasks` tool, the data root (`.agents-control`), result fields such as
  `"agents"` in `status`, the `/agents-context` command and DSH's `agents` plugin id.
- Sessions created before keep their earlier calls under the old name; they are drawn
  as ordinary tool calls.

## Known limits

- `workspace` on `create` narrows a child to a directory inside the caller's own. It
  is not a sandbox by itself; each Runtime confines the child only as far as its own
  sandbox follows the working directory ([`PUBLIC_API.md`](PUBLIC_API.md) section 2).
- Native Sessions are not interchangeable between Runtimes. A child continues only on
  the Runtime it started on.
</content>
