# agents on Codex

This runs a Codex Session as the host of its child Agents. Back to the
[README](../../README.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/). It provides Python 3.12 or later.
- Codex, installed and logged in. There is no version floor in agents; the real runs
  used `codex-cli` 0.164.0, and some behaviour below is stated for that version.

## Install graftling

Once, with uv, as in the [README](../../README.md#install). Every host uses this one
installation:

```sh
uv tool install --managed-python "git+https://github.com/LUOXIAO92/graftling.git"
AGENTS_PYTHON="$(uv tool dir)/graftling/bin/python"
```

`AGENTS_PYTHON` is only a shell variable for the commands below; graftling never reads
it, so it need not be set permanently.

## Distribution

There is no Codex plugin and nothing to install into Codex. The host is a command of
the one installation, `"$AGENTS_PYTHON" -m graftling.codex.host`. It starts its own Codex service and
one parent Session. That Session sees the `subagents` tool (`mcp__graftling__subagents`) because the host registers it
for that Session.

So it **cannot** attach to a Codex you already have open, in the terminal or an app.
You cannot talk to the parent while it runs: it takes one instruction per invocation,
and `--resume` gives it the next one.

## Local files

Nothing is generated into a project. You write one settings file. The host's data
goes in its data root: `--directory` when you pass it, else `GRAFTLING_DATA_ROOT`
when set (the variable that chooses the data root of every host), else
`<Session cwd>/.agents-control`, where the Session cwd is the settings'
`adapter_request.session_parameters.cwd`. That default is the one the Claude Code,
Pi and DSH hosts use for the same project. Only that directory itself is created; a
Session cwd that is missing, relative or not a directory, or a relative
`GRAFTLING_DATA_ROOT`, is refused with a message naming `--directory` and the variable.

## Minimal configuration

`native-settings.json`, with your model and an absolute working path:

```json
{
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
}
```

`session_parameters.config` can also hold Codex's own provider, reasoning and network
options. A top-level `connection` may set `runtime_home`, `base_url` or `api_key_env`
(the name of a variable, never its value). Codex's own `auto_review` reviews approvals
unless your settings name another reviewer.

`config.yml` for connections and presets goes in the data root. See
[Configuration](../../README.md#configuration).

The host adds a short passage after your `developerInstructions`, for the parent only.
It says that `subagents` is how this host creates and controls children, and that Codex's
own `spawn_agent` is for when the user names it. Your text comes first and is what
children get. No native tool is removed.

## Run it

```sh
export GRAFTLING_DATA_ROOT=/absolute/agents-data   # or omit for /absolute/work/.agents-control
"$AGENTS_PYTHON" -m graftling.codex.host \
  --settings native-settings.json \
  --output /absolute/agents-data/last-run.json \
  'Use only the MCP tool subagents, not spawn_agent. Make these tool calls one at a time, in this order. Where an argument says <agent from 1>, use the "agent" field returned by call 1.
1. {"action": "execute", "feature": "create", "arguments": {"runtime": "codex", "instruction": "Do not use any tool. Reply with exactly: PONG"}}
2. {"action": "execute", "feature": "wait", "arguments": {"agent": "<agent from 1>", "timeout_seconds": 240}}
A notice from agents may arrive at any time and changes nothing. Then reply with the answer of the child, and stop.'
```

| Flag | Meaning |
| --- | --- |
| `--settings <file>` | Required. The settings above |
| `--directory <dir>` | Optional. The data root and the parent's retained state; wins over `GRAFTLING_DATA_ROOT`. Kept from earlier releases; the variable is the preferred way |
| `--output <file>` | Optional. Writes the result document as JSON |
| `--resume` | Continue the retained parent Session in the same data root, found as above |
| `<instruction>` | Required. The parent's instruction |

Without `--resume`, a directory that already holds a parent Session is refused with
`session-exists`. A second host on the same directory is refused with `turn-running`.
A refused start prints `{"error": {"code", "message"}}` and exits `1`.

If the parent itself needs a native approval, the request prints to stderr as
`{"native_request": ...}` and the host reads one JSON response from stdin.

> Not yet walked on a real host from a uv-only install: these steps run as written
> in a clean environment. The same host ran on real Codex 0.164.0 in earlier checks.

## See that it worked

- `--output`'s `operations` lists a `create` and a `wait` for the child, and stderr's last
  line is `{"run_summary": {"agents_calls": 2, "native_subagents": 0}}`. These two show
  the parent used `subagents`. Its last message does not.
- stderr starts with `{"parent_session": ..., "source": "thread/start"}`.
- stderr shows each exchange, as `┌ <parent session> → <agent>` for the instruction
  and `┌ <agent> → <parent session>` for the child's reply.
- stdout ends with the parent's last message, and the exit status is 0.
- `--output`'s `deliveries` lists each notice sent, and `stop_unconfirmed` is an empty
  list.

**`operations: []` means the parent made no `subagents` call in that run, whatever its last
message says.** `run_summary` then has `agents_calls: 0` and a note saying so. Any child
the parent reports creating in that run is not a `subagents` child: it is not in `status`,
and graftling did not approve or record it.

A `{"native_subagent": ...}` line on stderr, with an entry in `--output`'s
`native_subagents`, means the parent started Codex's own subagent. This is not a failure
and the exit status does not change, because you may want one. To ask for that on purpose,
name it in the instruction: `Use spawn_agent to ...`. The line comes from a notification
in Codex 0.164.0's protocol schema that no real run here has shown yet, so a run without
it can still have used `spawn_agent`. Trust `operations`, not the absence of this line.

Which tool the model chooses can only be observed in a real run. The instruction above
and the host's passage make `subagents` the plain choice, and tests check that those literal
calls work, but neither forces the choice.

`deliveries` shows that a notice was submitted, not that the model read it. A notice
that arrives after the parent's turn ended is held, and the next `--resume` delivers it.

## Children on other Runtimes

Add a top-level `runtimes` object to the settings file. A Runtime without an entry
fails `create` with `unsupported-runtime`. All children run in
`adapter_request.worktree_path`, and each entry is checked when the host starts.

```json
{
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
  },
  "runtimes": {
    "claude": {"model": "claude-sonnet-5-5"},
    "dsh": {"model": "deepseek-flash", "sandbox": "read-only", "api_key_env": "DEEPSEEK_API_KEY"},
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

`claude` takes the keys of the [Claude host's `settings`](claude-code.md#minimal-configuration)
and needs Claude Code 2.1.299 or later. `dsh` requires `model` and `sandbox` and needs
DSH 0.2.0-rc.2 or later. `pi` requires `provider` and `model`, and needs
[what a Pi child needs](../../README.md#what-a-pi-child-needs).

**Pi children need a data root outside `worktree_path`** (`--directory` or
`GRAFTLING_DATA_ROOT`; the default `<Session cwd>/.agents-control` is inside it). A
Pi child must not read the data root, and its sandbox cannot deny a path inside a
readable one, so the host refuses that layout at start with `invalid-config`.

> Not yet walked on a real host: Pi children of this host. Claude and DSH children have
> run on a real host.

## Upgrade

Upgrade the uv installation as in the [README](../../README.md#install) (`uv tool upgrade graftling`
when installed without a tag). Nothing else is generated,
so there is nothing to regenerate. Use `--resume` with the same data root to
continue the parent; its children stay listed by `status`.

## Limits

- It is a headless parent: one instruction per invocation, no attaching to an open
  Codex.
- Under `auto_review`, Codex decides its children's approvals itself. Only a refusal
  or a reviewer failure reaches the parent, which can `accept` one retry or `decline`.
  The retry goes through auto review again. The accept path is shown by tests only.
- `subagents` calls run one at a time on this host.
- A stop agents cannot confirm is reported on stderr as `{"error": {"code":
  "stop-unconfirmed", ...}}` and in `stop_unconfirmed` in `--output`, and the host
  exits non-zero. The parent cannot see it, because its run has ended. A run that
  raises writes no `--output`, so only the stderr line names those Agents.
- A notice not yet sent when a resumed turn ends waits for the next `--resume`.
</content>
