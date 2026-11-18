"""Looking up an archive by native Session reads only archive headers (518).

An archive's first line holds its header and every resolved observation, often
half the file. ``SessionArchive.native_header`` (and ``find_session``, which uses
it) needs only the headers of all archives under the root, so a lookup never
decodes the rest of those lines; opening an archive still reads all of it.
"""
import json
from pathlib import Path

import pytest

from graftling.sessions.archive import JsonlSessionStore, SessionArchive, archive_path
from graftling.sessions.reading import find_session


def header(native: str, archive_id: str | None = None, **extra: object) -> dict:
    """Return a valid v5 Claude header for native Session ``native``."""
    return {'version': 5, 'id': archive_id or 'claude:' + native, 'createdAt': 100, 'isSeeded': False,
            'native': {'runtime': 'claude', 'sessionId': native, 'locator': f'/native/{native}.jsonl'}, **extra}


def archive(root: Path, value: dict) -> Path:
    """Create one archive as graftling writes it and return its file."""
    path = archive_path(root, value['id'])
    JsonlSessionStore.create(path, value)
    return path


def first_line(path: Path, replace: tuple[str, str]) -> None:
    """Rewrite the archive's first line text in place (simulating a damaged or foreign file)."""
    line, rest = path.read_text(encoding='utf-8').split('\n', 1)
    assert replace[0] in line
    path.write_text(line.replace(*replace) + '\n' + rest, encoding='utf-8')


def test_lookup_reads_headers_only_and_a_damaged_archive_still_refuses_when_opened(tmp_path: Path) -> None:
    """A lookup takes every header without decoding observations; the damaged archive itself still raises."""
    wanted = archive(tmp_path, header('n-a'))
    damaged = archive(tmp_path, header('n-b'))
    # Valid header, then a first-line tail that is not JSON: only the archive's own reads may see it.
    first_line(damaged, ('"observations":[]', '"observations":[{"unterminated'))

    found = SessionArchive(tmp_path).native_header('claude', 'n-a')
    assert found == header('n-a')
    assert SessionArchive(tmp_path).native_header('claude', 'n-b') == header('n-b')
    assert find_session(tmp_path, 'n-a').path == wanted
    with pytest.raises(ValueError):
        JsonlSessionStore(damaged)
    with pytest.raises(ValueError):
        find_session(tmp_path, 'n-b')


def test_lookup_keeps_conflicts_versions_large_headers_and_other_line_shapes(tmp_path: Path) -> None:
    """The same headers give the same answers and errors as reading whole lines."""
    # A header longer than one read, and a line in another key order, are still found.
    archive(tmp_path, header('n-big', note='x' * 200_000))
    reordered = archive(tmp_path, header('n-order'))
    line, rest = reordered.read_text(encoding='utf-8').split('\n', 1)
    metadata = json.loads(line)
    reordered.write_text(json.dumps({'observations': metadata.pop('observations'), **metadata},
                                    separators=(',', ':')) + '\n' + rest, encoding='utf-8')
    lookup = SessionArchive(tmp_path)
    assert lookup.native_header('claude', 'n-big')['note'] == 'x' * 200_000
    assert lookup.native_header('claude', 'n-order') == header('n-order')
    assert lookup.native_header('claude', 'n-none') is None

    # Two unrelated archives claiming one native Session remain a conflict.
    archive(tmp_path, header('n-twice', 'claude:first'))
    archive(tmp_path, header('n-twice', 'claude:second'))
    with pytest.raises(ValueError, match='multiple canonical archive IDs'):
        SessionArchive(tmp_path).native_header('claude', 'n-twice')

    # An unsupported archive version still refuses every lookup.
    other = archive(tmp_path, header('n-version'))
    first_line(other, ('{"archiveVersion":1,', '{"archiveVersion":2,'))
    with pytest.raises(ValueError, match='Unsupported Session archive version'):
        SessionArchive(tmp_path).native_header('claude', 'n-big')
