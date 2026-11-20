"""A Codex host event's tool output: the caller's text when given, else the event's JSON (535)."""
import asyncio
import json
from xml.sax.saxutils import unescape

from graftling import notice_text
from graftling.codex.app_server import CodexAppServer

EVENT = {'source': 'graphtraj', 'alias': 'agent-1', 'event': 'completed', 'execution': 'e' * 32,
         'message': '## Reply\n\n- `agents`\n\n> quoted'}


def submitted(*text: str, **options: str) -> dict:
    """Return the turn/start parameters send_host_event writes, without a Codex process."""
    calls = []
    server = object.__new__(CodexAppServer)

    async def call(method: str, params: dict, **options: object) -> dict:
        calls.append((method, params, options))
        return {'turn': {'id': 'turn-1', 'status': 'inProgress', 'items': []}}

    server._call = call
    asyncio.run(server.send_host_event('thread-1', dict(EVENT), *text, **options))
    [(method, params, call_options)] = calls
    assert method == 'turn/start' and call_options == {'wait_for_session': True}
    assert params['threadId'] == 'thread-1' and params['input'] == []
    return params


def test_text_is_the_tool_output_the_main_reads() -> None:
    text = notice_text(EVENT)
    assert submitted(text)['toolOutput'] == {'name': 'graftling', 'output': text}
    assert '\n## Reply\n\n- `agents`\n\n> quoted' in text


def test_without_text_the_output_stays_the_event_json() -> None:
    output = submitted()['toolOutput']
    assert output['name'] == 'graftling' and 'namespace' not in output
    assert json.loads(output['output']) == EVENT


def test_delegation_from_draws_codex_delegation_cell() -> None:
    """535/542 F2: Codex draws 'Sent by Codex from task <id>' over Markdown for exactly this tool output."""
    text = '> **bold** & `a<b>`'
    output = submitted(text, delegation_from='agent-1&<x>')['toolOutput']
    assert output == {'name': 'send_message_to_thread', 'namespace': 'codex_app', 'output': (
        '<codex_delegation>\n  <source_thread_id>agent-1&amp;&lt;x&gt;</source_thread_id>\n'
        '  <input>&gt; **bold** &amp; `a&lt;b&gt;`</input>\n</codex_delegation>')}


def test_delegation_without_text_wraps_the_event_json() -> None:
    output = submitted(delegation_from='agent-1')['toolOutput']['output']
    body = output.split('<input>', 1)[1].rsplit('</input>', 1)[0]
    assert json.loads(unescape(body)) == EVENT
