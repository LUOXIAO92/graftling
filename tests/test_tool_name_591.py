"""The MCP tool is ``subagents`` everywhere, from one source (591).

The guard below fails on any production line that names the tool's old name
``agents`` as a quoted literal or inside a native name (``__agents``), unless the
line is one of the listed look-alikes: names that are spelled ``agents`` but are
not the tool (data directories, result keys, DSH's own service, an old server key).
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

import graftling
from graftling.tool import TOOL_NAME

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src' / 'graftling'

# A quoted 'agents', "agents" or `agents` (not RST ``agents`` prose), or the old
# name inside a native tool name or plugin server prefix.
OLD_NAME = re.compile(r"(?<![`'\"])(['\"`])agents\1(?![`'\"])|__agents\b|_agents__")

# (file relative to the repository root, a fragment of the allowed line): every
# look-alike that stays ``agents``. Each entry must still match a line.
ALLOWED = [
    # Data directories: control/agents/*.json, live/agents/*.lock.
    ('src/graftling/control.py', '"control" / "agents"'),
    ('src/graftling/control.py', "'control' / 'agents'"),
    ('src/graftling/control.py', '"live" / "agents"'),
    ('src/graftling/control.py', "'live' / 'agents'"),
    # The status result key {"agents": [...]}.
    ('src/graftling/control.py', 'Outcome({"agents": '),
    ('src/graftling/control.py', 'result["agents"]'),
    ('src/graftling/codex/host.py', "found.get('agents', [found])"),
    ('src/graftling/dsh/host.py', "found.get('agents', [found])"),
    ('src/graftling/pi/host.py', "found.get('agents', [found])"),
    # The notice event label.
    ('src/graftling/codex/host.py', "'source': 'agents'"),
    ('src/graftling/pi/extension.mjs', "source: 'agents'"),
    # The .mcp.json server key configuration --write wrote before 0.6.0, removed on re-run.
    ('src/graftling/claude/host.py', "OLD_SERVER = 'agents'"),
    # DSH's own agents service and plugin id, and its patch row.
    ('src/graftling/dsh/plugin.mjs', "export const name = 'agents';"),
    ('src/graftling/dsh/plugin.mjs', "inject = ['tools', 'sessionQuery', 'agents']"),
    ('src/graftling/dsh/communication.mjs', "inject = ['sessionQuery', 'agents']"),
    ('src/graftling/dsh/execution.py', "{'id': 'agents', 'name': self.request['plugin']}"),
]

# Human-facing labels and log prefixes that named the old tool (591): display frames,
# notice titles, host log lines, review texts. None is allowed any more.
OLD_LABEL = re.compile(r"\bagents (·|→|host\b|review|notice|context)|(?<![-\w])agents-call\b|\[agents\b"
                       r"|'agents: |agents/connectivity-check")


def production_files() -> list[Path]:
    """Every shipped source file: Python, JavaScript, manifests and the plugin's start script."""
    files = [ROOT / 'pyproject.toml', PACKAGE / 'claude-plugin' / 'bin' / 'graftling-host']
    for path in sorted(PACKAGE.rglob('*')):
        if (path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts
                and path.suffix in {'.py', '.mjs', '.json', '.yml', '.toml'}):
            files.append(path)
    return files


def old_name_lines() -> list[tuple[str, int, str]]:
    """Return ``(file, line number, line)`` for each production line naming the old tool name."""
    found = []
    for path in production_files():
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if OLD_NAME.search(line):
                found.append((path.relative_to(ROOT).as_posix(), number, line.strip()))
    return found


def test_no_production_line_names_the_old_tool() -> None:
    """A half rename fails here: only the listed look-alikes keep ``agents``."""
    unexpected = [hit for hit in old_name_lines()
                  if not any(hit[0] == path and fragment in hit[2] for path, fragment in ALLOWED)]
    assert unexpected == []


def test_every_allowed_look_alike_is_still_there() -> None:
    """The allow-list holds no entry that no longer matches, so it cannot grow stale."""
    hits = old_name_lines()
    stale = [(path, fragment) for path, fragment in ALLOWED
             if not any(hit[0] == path and fragment in hit[2] for hit in hits)]
    assert stale == []


def test_no_production_line_shows_an_old_label() -> None:
    """Display frames, notice titles, log lines and the review probe say subagents."""
    found = [(path.relative_to(ROOT).as_posix(), number, line.strip())
             for path in production_files()
             for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
             if OLD_LABEL.search(line)]
    assert found == []


HEADER = '[subagents · child-1 → parent-1]\nthe body'


def test_a_relayed_message_has_the_exact_header() -> None:
    """A child's end or notice reaches its parent's turn as ``[subagents · <child> → <parent>]``."""
    from types import SimpleNamespace
    import threading

    from graftling.claude.binding import bind_run
    from graftling.codex.driver import _receive_children

    receivers, sent = [], []
    run = SimpleNamespace(call=lambda request: None, archive=None, extra_fields=None, agent='parent-1',
                          settings={'completion_message': {'agent': 'child-1', 'body': 'the body'}},
                          events=SimpleNamespace(receive_children=receivers.append,
                                                 continue_from_child=lambda event, archive: False))
    event = {'parent': 'parent-1', 'session': 'child-session', 'agent': 'child-1', 'body': 'the body'}
    claude = SimpleNamespace(configure_independent=lambda **options: None, lock=threading.Lock(), accepting=True,
                             stopping=threading.Event(), done=threading.Event(), session='s', execution_id='e',
                             operate=sent.append)
    bind_run(claude, run)
    assert claude.prompt == HEADER
    assert receivers[-1](event) and sent[-1]['instruction'] == HEADER
    _receive_children(SimpleNamespace(session='s', execution_id='e', operate=sent.append), run)
    assert receivers[-1](event) and sent[-1]['instruction'] == HEADER


def test_every_relayed_message_header_has_its_spaces() -> None:
    """Each producer (Claude, Codex, Pi) writes ``[subagents · {child} → {parent}]``, spaced like the frames."""
    sources = [(path, line) for path in production_files() if path.suffix == '.py'
               for line in path.read_text(encoding='utf-8').splitlines() if '[subagents ·' in line]
    assert len(sources) == 5
    for path, line in sources:
        assert re.search(r'\[subagents · \{[^}]+\} → \{run\.agent\}\]', line), (path, line)


def test_the_label_guard_sees_each_old_label() -> None:
    """The label pattern catches each label 0.8.0 showed, and not the names that stay."""
    for line in ("f'┌ agents · {sender} → {recipient}'", "['agents → ']", "'agents host stopped'",
                 '`agents review of tool "x"`', '`[agents notice from ${a}`', "f\"[agents · {a} → {b}]\"",
                 "' agents-call finished'", "'agents: allow a tool beyond the host settings'",
                 'build_request("agents", "agents/connectivity-check"', "'agents context cleanup failed: '"):
        assert OLD_LABEL.search(line), line
    for line in ("'subagents host stopped'", "'agents-review'", "'agents-mcp'", "'/agents-context'",
                 '.agents-control', "'agents-review-hook'", "{'id': 'agents'}", "source: 'agents'"):
        assert not OLD_LABEL.search(line), line


def test_the_guard_sees_the_forms_a_half_rename_leaves() -> None:
    """The pattern catches each form the old name took, and not prose or the new name."""
    for line in ("name == 'agents'", 'TOOLS = ["agents"]', "f'mcp__{server}__agents'",
                 'mcp__plugin_${PLUGIN}_agents__', "tool === `agents`"):
        assert OLD_NAME.search(line), line
    for line in ("name == 'subagents'", "f'mcp__{server}__subagents'", 'the ``agents`` backend',
                 "the agents host", "'agents-review'", "'agents-mcp'", '.agents-control'):
        assert not OLD_NAME.search(line), line


def test_the_tool_name_is_subagents_and_public() -> None:
    """``TOOL_NAME`` is the one Python source and the public name."""
    assert TOOL_NAME == graftling.TOOL_NAME == 'subagents'
    assert graftling.tool_descriptor()['name'] == TOOL_NAME
    from graftling.mcp import tool_documents
    assert [tool['name'] for tool in tool_documents()] == [TOOL_NAME]


@pytest.mark.parametrize('relative, pattern', [
    ('claude/host_context.mjs', r'^const TOOL = "([^"]*)";$'),
    ('claude-plugin/hooks/host_context.mjs', r'^const TOOL = "([^"]*)";$'),
    ('pi/extension.mjs', r"^export const TOOL = '([^']*)';$"),
    ('dsh/review.mjs', r"^export const TOOL = '([^']*)';$"),
])
def test_each_module_holds_one_tool_constant_equal_to_tool_name(relative: str, pattern: str) -> None:
    """Each .mjs names the tool once, as a constant equal to ``TOOL_NAME``."""
    source = (PACKAGE / relative).read_text(encoding='utf-8')
    assert re.findall(pattern, source, re.MULTILINE) == [TOOL_NAME]
    assert source.count(f"'{TOOL_NAME}'") + source.count(f'"{TOOL_NAME}"') == 1


def test_standalone_plugin_server_key_and_cli_names() -> None:
    """The marketplace plugin's server key is the tool's name; the commands are subagents and subagents-mcp."""
    manifest = json.loads((PACKAGE / 'claude-plugin' / '.claude-plugin' / 'plugin.json').read_text())
    assert list(manifest['mcpServers']) == [TOOL_NAME]
    scripts = re.search(r'^\[project\.scripts\]\n((?:.+\n)+)', (ROOT / 'pyproject.toml').read_text(), re.MULTILINE)
    assert scripts is not None
    assert scripts.group(1).split('\n')[:2] == ['subagents = "graftling.cli:main"',
                                                'subagents-mcp = "graftling.mcp:main"']


def test_the_hook_rewrite_refuses_a_template_it_would_not_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """A template whose tool line differs from TOOL_NAME, or whose TOOLS line moved, raises instead of
    being written unchanged."""
    from graftling.claude import plugin

    module = plugin._module()
    assert plugin.TOOL_LINE in module.splitlines() and plugin.TOOLS_LINE in module.splitlines()
    written = plugin.claude_host_plugin(Path('/p'), 'n', 'd', tools=['tasks'], server='graphtraj')
    hook = written['/p/hooks/host_context.mjs']
    assert 'const TOOLS = [TOOL, "tasks"];' in hook.splitlines() and 'const SERVER = "graphtraj";' in hook
    monkeypatch.setattr(plugin, 'TOOL_LINE', 'const TOOL = "agents";')
    with pytest.raises(RuntimeError):
        plugin._module()
    monkeypatch.setattr(plugin, 'TOOL_LINE', f'const TOOL = {json.dumps(TOOL_NAME)};')
    monkeypatch.setattr(plugin, 'TOOLS_LINE', 'const TOOLS = ["agents"];')
    with pytest.raises(RuntimeError):
        plugin.claude_host_plugin(Path('/p'), 'n', 'd')
