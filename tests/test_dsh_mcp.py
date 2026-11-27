"""Progressive MCP evidence, independent from official UI/model acceptance."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from graftling.dsh.settings import dsh_entry

def test_installed_dsh_official_mcp_client() -> None:
    """Use installed official client initialize/list/call over actual stdio."""
    node, dsh = shutil.which('node'), shutil.which('dsh')
    if node is None or dsh is None:
        pytest.skip('Installed DSH and Node are required for native MCP evidence.')
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run([
        node, str(root / 'tests/fixtures/dsh_mcp.mjs'), str(dsh_entry(dsh)),
        sys.executable, str(root / 'tests/fixtures/dsh_mcp_server.py'), str(root / 'src'),
    ], capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence['tools'] == ['mcp__graftling__subagents']
    assert evidence['call']['result']['caller'] == 'native-parent'


def test_delivered_plugin_native_scopes(tmp_path: Path) -> None:
    """The installed client publishes one scoped tool and preserves source context."""
    node, dsh = shutil.which('node'), shutil.which('dsh')
    if node is None or dsh is None:
        pytest.skip('Installed DSH and Node are required for native MCP evidence.')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        node, str(root / 'tests/fixtures/dsh_mcp_plugin.mjs'), str(dsh_entry(dsh)),
        str(root / 'src/graftling/dsh/plugin.mjs'), sys.executable,
        str(root / 'tests/fixtures/dsh_mcp_server.py'),
    ], cwd=tmp_path, env={**os.environ,
        'GRAPHTRAJ_CLI_CONNECTION': 'controlled-unused', 'GRAPHTRAJ_DSH_AGENTS_ONLY': '1',
        'GRAPHTRAJ_HARNESS_ROOT': str(tmp_path), 'PYTHONPATH': str(root / 'src'),
    }, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence['childSourcePreserved']
    assert evidence['browserEntry'] == 'graftling-dsh'


@pytest.mark.parametrize("registration_order", ["ready", "delayed"])
def test_browser_module_keeps_native_tool_card(registration_order: str) -> None:
    """agents calls keep DSH's native Tool card; incoming messages gain true attribution."""
    node, dsh = shutil.which('node'), shutil.which('dsh')
    if node is None or dsh is None:
        pytest.skip('Installed DSH slots and Node are required for the browser projection fixture.')
    slots = subprocess.run([node, '-e', "require('module').createRequire(process.argv[1])"
                            ".resolve('@deepseek-ai/dsh-client-ui-slots')", str(dsh_entry(dsh))],
                           capture_output=True, text=True, timeout=10)
    if slots.returncode != 0:
        pytest.skip('Installed DSH does not provide @deepseek-ai/dsh-client-ui-slots.')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        node, str(root / 'tests/fixtures/dsh_client_view.mjs'),
        str(root / 'src/graftling/dsh/client.mjs'), str(dsh_entry(dsh)),
        registration_order,
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
