"""A Codex host event's tool output: the caller's text when given, else the event's JSON (535)."""
import asyncio
import json

from graftling import notice_text
from graftling.codex.app_server import CodexAppServer

EVENT = {'source': 'graphtraj', 'alias': 'agent-1', 'event': 'completed', 'execution': 'e' * 32,
         'message': '## Reply\n\n- `agents`\n\n> quoted'}


def submitted(*text: str) -> dict:
    """Return the turn/start parameters send_host_event writes, without a Codex process."""
    calls = []
    server = object.__new__(CodexAppServer)

    async def call(method: str, params: dict, **options: object) -> dict:
        calls.append((method, params, options))
        return {'turn': {'id': 'turn-1', 'status': 'inProgress', 'items': []}}

    server._call = call
    asyncio.run(server.send_host_event('thread-1', dict(EVENT), *text))
    [(method, params, options)] = calls
    assert method == 'turn/start' and options == {'wait_for_session': True}
    assert params['threadId'] == 'thread-1' and params['input'] == []
    assert params['toolOutput']['name'] == 'graftling'
    return params


def test_text_is_the_tool_output_the_main_reads() -> None:
    text = notice_text(EVENT)
    assert submitted(text)['toolOutput']['output'] == text
    assert '\n## Reply\n\n- `agents`\n\n> quoted' in text


def test_without_text_the_output_stays_the_event_json() -> None:
    assert json.loads(submitted()['toolOutput']['output']) == EVENT
