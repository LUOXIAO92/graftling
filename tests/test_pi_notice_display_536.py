"""536: a Pi Main draws an agents notice as who · event over its Markdown body, not a type label.

The model input (the message content) keeps the title with the execution. Drawn with the
installed Pi's own CustomMessageComponent, so the label and Markdown are what a user sees.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

EXTENSION = Path(__file__).resolve().parents[1] / 'src/graftling/pi/extension.mjs'
SCRIPT = '''
import { pathToFileURL } from 'node:url';
const [module, piEntry] = process.argv.slice(1);
const ext = await import(pathToFileURL(module).href);
const sdk = await ext.loadHostSdk(piEntry);
sdk.initTheme();
const renderers = {}, sent = [];
const pi = { registerMessageRenderer: (type, renderer) => { renderers[type] = renderer; },
  sendMessage: message => sent.push(message) };
ext.registerNoticeDisplay(pi, sdk);
ext.deliverNotice(pi, {id: 'exec-1:completed', alias: 'n536', agent: 'agent-1', event: 'completed',
  message: '## Done\\n\\n- **one**', execution: 'exec-1'}, 'pi-main', {getEntries: () => []});
const draw = message => renderers['graftling:agent-communication'](message, {expanded: false, outputPad: 1})
  ?.render(60).join('\\n').replace(/\\x1b\\[[0-9;]*m/g, '') ?? null;
console.log(JSON.stringify({content: sent[0].content, drawn: draw(sent[0]),
  plain: draw({customType: 'graftling:agent-communication', content: 'x', display: true})}));
'''


def test_notice_is_drawn_as_who_and_markdown_with_the_model_input_unchanged() -> None:
    node, pi = shutil.which('node'), shutil.which('pi')
    if node is None or pi is None:
        pytest.skip('Node and an installed Pi are required')
    result = subprocess.run([node, '--input-type=module', '-e', SCRIPT, str(EXTENSION), pi],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown['content'] == '[subagents notice from n536 · completed · execution exec-1]\n## Done\n\n- **one**'
    lines = [line.strip() for line in shown['drawn'].splitlines() if line.strip()]
    assert lines == ['[subagents · n536 · completed]', 'Done', '- one']
    assert not re.search(r'graftling:agent-communication|##|\*\*', shown['drawn'])
    # A message without the notice details keeps Pi's default drawing.
    assert shown['plain'] is None
