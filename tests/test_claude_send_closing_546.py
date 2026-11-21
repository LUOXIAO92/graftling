"""546: a send that reaches a Claude execution while it closes goes to the next execution.

The real ClaudeDriver and ClaudeExecution run the controlled CLI peer (claude_cli_fixture.py). Test-only widening:
``ClaudeExecution._close`` (the process shutdown after the turn ended and input closed) is held until the send was
made, so the send lands in the window between ``accepting = False`` and the reported end every time.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from graftling.claude.execution import ClaudeExecution
from test_claude_review import Harness, native  # noqa: F401  (fixture)


def test_a_send_while_claude_closes_goes_to_the_next_execution(
        tmp_path: Path, native: None, monkeypatch: pytest.MonkeyPatch) -> None:
    closing, proceed = threading.Event(), threading.Event()
    original = ClaudeExecution._close

    def held_close(self) -> None:
        closing.set()
        proceed.wait(10)
        original(self)

    monkeypatch.setattr(ClaudeExecution, '_close', held_close)
    harness = Harness(tmp_path, reviewed=False)
    try:
        created = harness.invoke('create', runtime='claude', instruction='first')
        agent, first = created['agent'], created['execution']
        assert closing.wait(10), 'the first turn did not end'
        status = harness.invoke('status', agent=agent)['result']
        assert status['state'] == 'active' and status['execution'] == first  # the window: input closed, not ended

        threading.Timer(0.5, proceed.set).start()
        sent = harness.call({'action': 'send', 'agent': agent, 'message': 'second'})
        assert not sent.failed, sent.document
        delivery = sent.document['result']['delivery']
        assert delivery['delivery'] == 'next-execution' and delivery['closed_execution'] == first
        assert delivery['execution'] == sent.document['execution'] != first
        assert sent.document['session'] == created['session']

        waited = harness.wait(agent)
        assert waited['state'] == 'standby' and waited['last_result']['id'] == delivery['execution']
        assert 'second' in waited['last_result']['last_message']
        assert waited['last_result']['outcome'] == 'completed'
    finally:
        proceed.set()
        harness.close()
