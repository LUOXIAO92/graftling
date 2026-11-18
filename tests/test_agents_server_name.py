"""The MCP server name agents is registered under, and the review that lets only its tool through (513).

Pi and DSH run their real extension and review modules under Node with
recording stand-ins for the Runtime; no model or real Runtime starts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import graftling
from graftling.dsh.settings import prepare
from test_dsh_standalone_child import dsh, launch_patches  # noqa: F401 - pytest fixture

SOURCE = Path(graftling.__file__).resolve().parent

# Pi: what registerAgents registers and what registerReview lets through.
PI = r'''
const module = await import(process.argv[1]);
const options = JSON.parse(process.argv[2]);
const servers = [], asked = [], handlers = {};
const pi = {
  registerFlag() {}, registerCommand() {}, on: (event, handler) => { handlers[event] = handler; },
  registerMcpServer: name => servers.push(name),
};
const owner = module.registerAgents(pi, options.server ? {server: options.server} : undefined);
await owner.start({hasUI: false, cwd: process.cwd(), sessionManager: {}});
module.registerReview(pi, {GRAFTLING_PI_REVIEW: 'review-title'}, ...(options.server ? [options.server] : []));
const ctx = {ui: {confirm: async (title, call) => { asked.push(JSON.parse(call).tool); return false; }}};
const blocked = [];
for (const toolName of options.tools) {
  const answer = await handlers.tool_call({toolName, toolCallId: toolName, input: {}}, ctx);
  if (answer?.block) blocked.push(toolName);
}
console.log(JSON.stringify({servers, asked, blocked}));
'''

# DSH: which calls the review row turns into a review ask.
DSH = r'''
const { apply } = await import(process.argv[1]);
const config = JSON.parse(process.argv[2]);
let hook;
apply({ on: (event, handler) => { hook = handler; } }, config);
const asked = [];
for (const name of JSON.parse(process.argv[3])) {
  const decision = await hook({ name, callId: name, arguments: {} }, async () => ({ kind: 'allow' }));
  if (decision.kind === 'ask') asked.push(name);
}
console.log(JSON.stringify(asked));
'''

TOOLS = ['mcp__graftling__agents', 'mcp__agents__agents', 'mcp__other__agents', 'mcp__graphtraj__agents', 'Bash']


def node(script: str, *arguments: str, **environment: str) -> object:
    """Run ``script`` as an ES module under Node and return its JSON output."""
    executable = shutil.which('node')
    if executable is None:
        pytest.skip('Node is required')
    result = subprocess.run([executable, '--input-type=module', '-e', script, *arguments],
                            env={**os.environ, **environment}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('server', [None, 'graphtraj'])
def test_pi_registers_its_server_and_reviews_every_other_agents_tool(server: str | None) -> None:
    """graftling by default, a consumer's name when given; only that server's agents call skips review."""
    name = server or 'graftling'
    found = node(PI, (SOURCE / 'pi/extension.mjs').as_uri(), json.dumps({'server': server, 'tools': TOOLS}),
                 GRAFTLING_CONNECTION='/fake/connection', GRAFTLING_PYTHON='/fake/python')
    assert found['servers'] == [name]
    assert found['asked'] == found['blocked'] == [tool for tool in TOOLS if tool != f'mcp__{name}__agents']


@pytest.mark.parametrize('server', ['graftling', 'graphtraj', None])
def test_dsh_review_row_lets_through_only_the_registered_servers_agents_tool(tmp_path: Path, server: str | None) -> None:
    """The row's mcpServer names the one agents tool not reviewed; without it every call is reviewed."""
    config = {'readyFile': str(tmp_path / 'ready'), **({'mcpServer': server} if server else {})}
    asked = node(DSH, (SOURCE / 'dsh/review.mjs').as_uri(), json.dumps(config), json.dumps(TOOLS))
    assert asked == [tool for tool in TOOLS if tool != f'mcp__{server}__agents']


@pytest.mark.parametrize('mcp', [True, False])
def test_dsh_review_row_names_the_server_the_plugin_row_registers(tmp_path: Path, dsh: dict, mcp: bool) -> None:
    """Both rows carry mcp_server; with no agents server registered, review names none."""
    settings = {**prepare(dsh, tmp_path), 'review': {'command': ['reviewer']},
                **({'mcp': {'command': '/python', 'args': ['-m', 'graftling.mcp']}} if mcp else {})}
    _, patches, _ = launch_patches(settings, tmp_path)
    rows = {row['insert'][0]['id']: row['insert'][0]['config'] for row in patches if 'insert' in row}
    assert rows['agents-review'].get('mcpServer') == ('graftling' if mcp else None)
    assert ('agents-mcp' in rows) == mcp and rows.get('agents-mcp', {}).get('mcpServer', 'graftling') == 'graftling'
