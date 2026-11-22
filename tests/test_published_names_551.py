"""551's host tool table and per-Runtime tool choice are published names (PUBLIC_API.md 1.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

import graftling
import graftling.codex
import graftling.dsh
import graftling.pi
from graftling import AgentsError, AgentsResult, extra_tool_table

CONTRACT = Path(graftling.__file__).with_name('PUBLIC_API.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('package, name', [
    (graftling, 'extra_tool_table'),
    (graftling.codex, 'apply_tools'),
    (graftling.pi, 'apply_tools'),
    (graftling.dsh, 'apply_tools'),
])
def test_each_name_is_exported_and_listed(package: object, name: str) -> None:
    """The name is in the package's ``__all__``, resolves, and the contract names it."""
    assert name in package.__all__
    assert callable(getattr(package, name))
    assert f'`{name}' in CONTRACT


def test_the_pi_extension_export_is_listed() -> None:
    """``childServer`` is in the contract's JavaScript table and exported by the file."""
    row = next(line for line in CONTRACT.splitlines() if line.startswith('| `graftling/pi/extension.mjs` |'))
    assert '`childServer`' in row
    source = Path(graftling.__file__).with_name('pi').joinpath('extension.mjs').read_text(encoding='utf-8')
    assert 'export function childServer(' in source


def test_extra_tool_table_refuses_the_agents_name() -> None:
    """The published table keeps its checks: ``agents`` cannot be an extra tool."""
    descriptor = {'name': 'agents', 'description': 'x', 'inputSchema': {'type': 'object'}}
    with pytest.raises(ValueError):
        extra_tool_table([(descriptor, lambda arguments: AgentsResult({}))])


def test_codex_apply_tools_refuses_a_name_codex_cannot_express() -> None:
    """A built-in Codex has no switch for is refused through the published name."""
    with pytest.raises(AgentsError):
        graftling.codex.apply_tools({}, {'tools': None, 'disallowed_tools': ['shell']})
