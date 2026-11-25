"""Run each tests/test_*.py in its own pytest process with a wall-clock limit (632).

Used by .github/workflows/windows.yml so that one hung test file cannot take the
whole run with it: a file still running after --file-limit seconds has its whole
process tree stopped (taskkill /T /F on Windows, its process group on POSIX), is
recorded as hung with the last test that started, and the next file runs. Each
ended file writes its own JUnit file (xunit1). Standard library only.

  python .github/run_test_files.py --out DIR [--file-limit S] [--total-limit S] [FILE ...]

Writes, under DIR: junit/<file stem>.xml, logs/<file stem>.txt (the file's whole
pytest output, faulthandler dumps included) and files.json, rewritten after each
file: one record per test file with its exit code, outcome (ended, hung, or
not-run once --total-limit is spent), elapsed seconds and, for a hung file, the
last test that started. Exit status: 0 when every file ended with pytest exit 0,
else 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

WINDOWS = sys.platform == 'win32'
# pytest -v prints a test's node id when it starts and its outcome when it ends.
OUTCOME = re.compile(r' (PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b')
STARTED = re.compile(r'^(tests/\S+?\.py::.+?)(?: (?:PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b.*| Timeout \(.*)?\s*$')


def pytest_command(test_file: str, junit: Path) -> list[str]:
    """One file's pytest run: unbuffered, so a killed run's log still names the test it was in."""
    return [sys.executable, '-u', '-m', 'pytest', '-p', 'no:cacheprovider', '-v', '-rfEsxX', '--tb=short',
            '--durations=10', '-o', 'faulthandler_timeout=120', '-o', 'junit_family=xunit1',
            f'--junitxml={junit}', test_file]


def stop_tree(process: subprocess.Popen) -> str:
    """Stop the process and everything it started; say how."""
    if WINDOWS:
        done = subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], capture_output=True, text=True)
        return f'taskkill /T /F exit {done.returncode}: {(done.stdout + done.stderr).strip()[-300:]}'
    try:
        os.killpg(process.pid, signal.SIGKILL)
        return 'SIGKILL to the process group'
    except ProcessLookupError:
        return 'process group already gone'


def last_started(log: str) -> tuple[str | None, bool]:
    """The last test whose start -v printed, and whether its outcome followed."""
    for line in reversed(log.splitlines()):
        match = STARTED.match(line)
        if match:
            return match.group(1), OUTCOME.search(line) is not None
    return None, False


def run_file(test_file: str, out: Path, limit: float) -> dict:
    stem = Path(test_file).stem
    junit, log_path = out / 'junit' / f'{stem}.xml', out / 'logs' / f'{stem}.txt'
    junit.unlink(missing_ok=True)
    started = time.monotonic()
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {'start_new_session': True}
    with open(log_path, 'wb') as log:
        process = subprocess.Popen(pytest_command(test_file, junit), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)

        def pump() -> None:
            # Copy output as it comes, partial lines included, to the job log and the file's log.
            while chunk := process.stdout.read1(65536):
                sys.stdout.buffer.write(chunk)
                sys.stdout.flush()
                log.write(chunk)
                log.flush()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        record = {'file': test_file, 'log': str(log_path.relative_to(out))}
        try:
            code = process.wait(timeout=limit)
            record.update(outcome='ended', exit_code=code)
        except subprocess.TimeoutExpired:
            record.update(outcome='hung', exit_code=None, stopped=stop_tree(process))
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                record['stopped'] += '; the process did not exit within 30 s'
        # A leftover grandchild may hold the pipe open; never wait on it for long.
        reader.join(timeout=10)
        record['pipe_closed'] = not reader.is_alive()
    record['elapsed_s'] = round(time.monotonic() - started, 1)
    record['junit'] = str(junit.relative_to(out)) if junit.exists() else None
    if record['outcome'] == 'hung':
        record['last_started'], record['last_finished'] = last_started(log_path.read_text(encoding='utf-8', errors='replace'))
        print(f'\n### HUNG after {limit:.0f} s: {test_file}; last test started: {record["last_started"]} '
              f'(its outcome {"was" if record["last_finished"] else "was not"} printed); {record["stopped"]}', flush=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--file-limit', type=float, default=360.0)
    parser.add_argument('--total-limit', type=float, default=4200.0)
    parser.add_argument('files', nargs='*')
    args = parser.parse_args()
    files = args.files or sorted(path.as_posix() for path in Path('tests').glob('test_*.py'))
    for part in ('junit', 'logs'):
        (args.out / part).mkdir(parents=True, exist_ok=True)
    began, records = time.monotonic(), []
    for test_file in files:
        left = args.total_limit - (time.monotonic() - began)
        if left <= 0:
            records.append({'file': test_file, 'outcome': 'not-run', 'exit_code': None, 'elapsed_s': 0,
                            'junit': None, 'reason': f'--total-limit {args.total_limit:.0f} s spent'})
        else:
            print(f'\n### {test_file} (limit {min(args.file_limit, left):.0f} s)', flush=True)
            records.append(run_file(test_file, args.out, min(args.file_limit, left)))
        (args.out / 'files.json').write_text(json.dumps({'file_limit_s': args.file_limit, 'total_limit_s': args.total_limit,
                                                         'files': records}, indent=1), encoding='utf-8')
    print('\n### per-file results', flush=True)
    for record in records:
        print(f"{record['outcome']:8} exit {str(record['exit_code']):4} {record['elapsed_s']:7.1f} s  {record['file']}"
              + (f"  last started: {record.get('last_started')}" if record['outcome'] == 'hung' else ''), flush=True)
    return 0 if all(record['outcome'] == 'ended' and record['exit_code'] == 0 for record in records) else 1


if __name__ == '__main__':
    sys.exit(main())
