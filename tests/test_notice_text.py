"""The Runtime-independent text a Main reads for one child notice (533)."""
import json

from graftling import notice_text

REPLY = '## Done\n\n- item `x`\n\n> quoted "text"'


def ended(**terminal) -> str:
    """GraphTraj's end notice body: a label line, then one JSON object."""
    return 'Session execution ended:\n' + json.dumps({
        'session': 'sess-1', 'execution_id': 'exec-1', 'terminal_confirmed': True, 'session_id': 'sess-1',
        'permissions': {'requested': 'auto', 'source': 'role', 'matches_request': True}, **terminal})


def test_completed_shows_the_reply_as_markdown_and_only_the_facts_the_model_needs() -> None:
    text = notice_text({'alias': 'leader@l1', 'event': 'completed', 'execution': 'exec-1',
                        'message': ended(outcome='completed', last_agent_message=REPLY)})
    title, body = text.split('\n', 1)
    assert title == '[Graftling notice from leader@l1 · completed · execution exec-1]'
    assert REPLY in body  # verbatim: no escaped \n or \"
    assert '\\n' not in text and '\\"' not in text
    for dropped in ('permissions', 'terminal_confirmed', 'session_id', 'matches_request', 'sess-1', '{'):
        assert dropped not in text
    assert text.splitlines()[-1] == 'Facts: execution_id `exec-1` · outcome `completed`'


def test_failed_and_interrupted_keep_the_error_and_the_last_reply() -> None:
    text = notice_text({'alias': 'a', 'event': 'failed', 'execution': 'e',
                        'message': ended(outcome='failed', last_agent_message='partial',
                                         error={'code': 'RUNTIME_EXIT', 'message': 'exit 3'})})
    assert 'partial' in text and '**Error:** exit 3' in text
    assert text.splitlines()[-1] == 'Facts: execution_id `exec-1` · outcome `failed` · error code `RUNTIME_EXIT`'
    interrupted = notice_text({'alias': 'a', 'event': 'interrupted',
                               'message': ended(outcome='interrupted', last_agent_message=None)})
    assert interrupted.split('\n', 1)[0] == '[Graftling notice from a · interrupted]'
    assert 'Session execution ended' in interrupted  # no reply: the label line is the content


def test_permission_request_keeps_request_id_token_and_the_tool_input() -> None:
    details = {'session': 's', 'execution_id': 'exec-2', 'request_id': 'req-9', 'request_token': 'tok-7',
               'method': 'claude/can_use_tool',
               'params': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'ls -la'}}}
    text = notice_text({'alias': 'w@e', 'event': 'claude/can_use_tool', 'execution': 'exec-2',
                        'message': 'Claude requires a native permission reply:\n' + json.dumps(details)})
    assert text.startswith('[Graftling notice from w@e · claude/can_use_tool · execution exec-2]\n')
    assert 'Claude requires a native permission reply' in text
    assert '"command": "ls -la"' in text and '```json' in text
    assert text.splitlines()[-1] == ('Facts: execution_id `exec-2` · request_id `req-9` · '
                                     'request_token `tok-7` · method `claude/can_use_tool`')


def test_plain_messages_pass_through_verbatim() -> None:
    report = ('Submitted report engineer.md for its direct parent to handle; read it through '
              'session_reports: /h/.state/engineer.md')
    assert notice_text({'alias': 'e@x', 'event': 'report-submitted', 'message': report}) == (
        '[Graftling notice from e@x · report-submitted]\n\n' + report)
    budget = 'System notice: the execution budget of Task 7 was exceeded.\n\n**Stop** now.'
    assert notice_text({'alias': 'l', 'event': 'execution-budget-exceeded', 'message': budget,
                        'request': 'r1'}).endswith(budget + '\n\nFacts: request `r1`')
    # A message that only looks structured stays as it is.
    odd = 'Note:\n{not json'
    assert notice_text({'alias': 'l', 'event': 'x', 'message': odd}).endswith('\n\n' + odd)


def test_missing_fields_still_give_one_readable_notice() -> None:
    assert notice_text({}) == '[Graftling notice from graftling · event]'
    assert notice_text({'alias': 'a', 'event': 'completed', 'message': ''}) == '[Graftling notice from a · completed]'
