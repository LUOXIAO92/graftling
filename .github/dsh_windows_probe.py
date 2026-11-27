"""Whether DSH runs on this Windows runner at all, without a credential (649, W11).

Evidence only. Every probe's command, exit code, duration and output are written under --out:
findings.json (one record per probe), findings.txt (the same, readable) and logs/<probe>.txt (the
whole output, with launch tokens removed). The script exits 0 once every probe ran and was recorded,
whatever DSH did; 2 only when recording itself failed. So a probe never fails silently and never
gates. No credential is read or passed: every DSH process gets this environment without
credential-class and provider variables (their names are recorded) and a fresh, empty DSH_HOME.

Probes, in order (each bounded):
  runner       Node, npm and Python versions; npm's global prefix and root
  registry     npm's view of the pinned DSH and its dist-tags (os, cpu, engines, scripts)
  install      npm install -g @deepseek-ai/dsh@<version>, install scripts shown
  package      the installed package's platform facts: package.json os/cpu/engines/bin, the
               platform-specific packages npm installed, optional sets with no win32 build,
               native .node files, install scripts in the tree, where `dsh` resolves
  cli-version  dsh --version            (the npm shim, as shutil.which finds it)
  cli-node     node <bin.js> --version  (no shim, no cmd.exe)
  cli-help     dsh --help;  cli-web-help: dsh web --help
  preflight    graftling's own DSH preflight: the package path it derives from `dsh`,
               dsh_version_refusal there and at the real package, tests/conftest's
               unsupported_dsh, settings.prepare with no credential, the pi-ai provider catalog
  web-raw      dsh web --no-open --port 0 in a kill-on-close job: output until the launch URL,
               the job's processes, then the job handle closed and whether each process ended
  service      graftling's DshService: start (launch URL and cookie), session/list, close, and
               whether each process of its job ended
  owner-death  a separate Python starts DshService and is killed (TerminateProcess, no close):
               whether each process of the DSH job ended with its owner (decision 3)
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

PACKAGE = '@deepseek-ai/dsh'
PROBES = ['runner', 'registry', 'install', 'package', 'cli-version', 'cli-node', 'cli-help', 'cli-web-help',
          'preflight', 'web-raw', 'service', 'owner-death']
# A launch link carries a single-use token; keep its shape, never its value.
TOKEN = re.compile(r'(token=)[^\s&"\']+', re.I)
# A Windows build in an optional set (win32-x64, win-x64, windows-...), not "darwin".
WIN = re.compile(r'(?:^|[-/@])win(?:32|dows)?(?:[-_]|$)')
LAUNCH = re.compile(r'https?://(?:127\.0\.0\.1|localhost):(\d+)/\S*token=')
CREDENTIAL_PART = re.compile(r'(KEY|TOKEN|SECRET|SECRETS|PASSWORD|CREDENTIAL|CREDENTIALS|COOKIE|AUTH)$')
PROVIDER_PREFIXES = ('ANTHROPIC_', 'OPENAI_', 'DEEPSEEK_', 'CODEX_', 'CLAUDE_CODE_USE_', 'ACTIONS_', 'GRAFTLING_')


def redact(text: str) -> str:
    return TOKEN.sub(r'\1<redacted>', text)


def removed_name(name: str) -> bool:
    """A credential-class or provider variable, or DSH_HOME (each probe sets its own)."""
    upper = name.upper()
    return (any(CREDENTIAL_PART.search(part) for part in upper.split('_'))
            or upper.startswith(PROVIDER_PREFIXES) or upper.endswith('_BASE_URL') or upper == 'DSH_HOME')


def code_of(error: BaseException) -> str:
    return f"{type(error).__name__} {getattr(error, 'code', '')}: {redact(str(error))[:2000]}"


class Recorder:
    def __init__(self, out: Path) -> None:
        self.out = out
        (out / 'logs').mkdir(parents=True, exist_ok=True)
        self.records: list[dict] = []

    def add(self, probe: str, title: str, *, exit: object, seconds: float, output: str, facts: dict | None = None,
            argv: list[str] | None = None, timed_out: bool = False) -> dict:
        output = redact(output)
        (self.out / 'logs' / f'{probe}.txt').write_text(output, encoding='utf-8')
        lines = [line for line in output.splitlines() if line.strip()]
        record = {'probe': probe, 'title': title, 'argv': [redact(str(a)) for a in argv or []], 'exit': exit,
                  'timed_out': timed_out, 'seconds': round(seconds, 1), 'log': f'logs/{probe}.txt',
                  'excerpt': [line[:240] for line in lines[-12:]], 'facts': facts or {}}
        self.records.append(record)
        print(f'[{probe}] exit {exit}{" (timed out)" if timed_out else ""} in {record["seconds"]} s: {title}', flush=True)
        for line in record['excerpt'][-6:]:
            print(f'    {line}', flush=True)
        return record

    def run(self, probe: str, title: str, argv: list[str], *, env: dict, cwd: Path, timeout: float) -> dict:
        start = time.monotonic()
        try:
            done = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, encoding='utf-8', errors='replace', timeout=timeout)
            return self.add(probe, title, exit=done.returncode, seconds=time.monotonic() - start, argv=argv,
                            output=done.stdout + ('\n--- stderr\n' + done.stderr if done.stderr else ''))
        except subprocess.TimeoutExpired as error:
            text = error.stdout.decode('utf-8', 'replace') if isinstance(error.stdout, bytes) else (error.stdout or '')
            return self.add(probe, title, exit=None, seconds=time.monotonic() - start, output=text, argv=argv,
                            timed_out=True)
        except OSError as error:
            return self.add(probe, title, exit=code_of(error), seconds=time.monotonic() - start,
                            output=traceback.format_exc(), argv=argv)

    def guarded(self, probe: str, title: str, body) -> dict:
        """Run an in-process probe; its exception is the probe's result, not the script's."""
        start = time.monotonic()
        lines: list[str] = []
        facts: dict = {}
        try:
            exit = body(lines, facts)
        except BaseException as error:  # noqa: BLE001 - every outcome is evidence
            lines.append(traceback.format_exc())
            exit = code_of(error)[:300]
        return self.add(probe, title, exit=exit, seconds=time.monotonic() - start, output='\n'.join(lines),
                        facts=facts)

    def write(self, meta: dict) -> None:
        recorded = [r['probe'] for r in self.records]
        meta = {**meta, 'probes': self.records, 'expected_probes': PROBES, 'complete': recorded == PROBES}
        (self.out / 'findings.json').write_text(json.dumps(meta, indent=2, default=str), encoding='utf-8')
        with open(self.out / 'findings.txt', 'w', encoding='utf-8') as handle:
            for record in self.records:
                handle.write(f"{record['probe']}\texit {record['exit']}\t{record['seconds']} s\t{record['title']}\n")
                for key, value in record['facts'].items():
                    handle.write(f'    {key}: {json.dumps(value, default=str)[:800]}\n')
                for line in record['excerpt']:
                    handle.write(f'    | {line}\n')


def walk_packages(root: Path) -> list[dict]:
    """Every package below a node_modules root: name, version, os, cpu, install scripts, optional set."""
    found = []
    for manifest in root.rglob('package.json'):
        owner = manifest.parent.parent
        if not (owner.name == 'node_modules' or (owner.name.startswith('@') and owner.parent.name == 'node_modules')):
            continue
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or 'name' not in data:
            continue
        scripts = {k: v for k, v in (data.get('scripts') or {}).items() if k in ('preinstall', 'install', 'postinstall')}
        found.append({'name': data['name'], 'version': data.get('version'), 'os': data.get('os'),
                      'cpu': data.get('cpu'), 'install_scripts': scripts,
                      'optional': sorted(data.get('optionalDependencies') or {})})
    return found


def tree_processes(tree) -> list[dict]:
    from graftling.process import process_executable

    rows = []
    for pid in tree.pids():
        try:
            image = process_executable(pid)
        except Exception as error:  # noqa: BLE001
            image = f'unreadable: {error}'
        rows.append({'pid': pid, 'image': str(image)})
    return rows


def alive_after(pids: list[int], seconds: float) -> list[int]:
    """The PIDs still running after up to ``seconds``."""
    from graftling.process import process_alive

    deadline = time.monotonic() + seconds
    left = [pid for pid in pids if process_alive(pid)]
    while left and time.monotonic() < deadline:
        time.sleep(0.5)
        left = [pid for pid in left if process_alive(pid)]
    return left


def kill_left(pids: list[int], lines: list[str]) -> None:
    """Clean-up only, recorded: a probe that found survivors does not leave them for the next one."""
    for pid in pids:
        try:
            done = subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True, text=True)
            lines.append(f'cleanup taskkill /F /T /PID {pid}: exit {done.returncode} '
                         f'{(done.stdout + done.stderr).strip()[:200]}')
        except OSError as error:
            lines.append(f'cleanup of {pid} failed: {code_of(error)}')


def read_lines(stream) -> queue.Queue:
    """A queue of the stream's lines, then None at its end."""
    lines: queue.Queue = queue.Queue()

    def pump() -> None:
        for line in stream:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=pump, daemon=True).start()
    return lines


# The owner-death probe's owner: start a DshService, report its job, then wait to be killed.
OWNER = r'''
import json, os, sys, time
from pathlib import Path
from graftling.dsh.service import DshService
service = DshService(sys.argv[1], Path(sys.argv[2]), json.loads(sys.argv[3]))
service.start()
print(json.dumps({"owner": os.getpid(), "dsh": service.process.pid, "pids": service.tree.pids(),
                  "port": service.origin.rsplit(":", 1)[-1]}), flush=True)
time.sleep(600)
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--dsh-version', required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    rec = Recorder(out)
    repo = Path.cwd()
    work = Path(tempfile.mkdtemp(prefix='dsh-probe-'))
    removed = sorted(name for name in os.environ if removed_name(name))
    base_env = {k: v for k, v in os.environ.items() if k not in removed}

    def dsh_env(tag: str) -> dict:
        home = work / f'home-{tag}'
        home.mkdir(parents=True, exist_ok=True)
        return {**base_env, 'DSH_HOME': str(home)}

    npm = shutil.which('npm') or 'npm'
    node = shutil.which('node') or 'node'
    meta = {'dsh_version': args.dsh_version, 'platform': sys.platform, 'python': sys.version,
            'removed_variable_names': removed, 'work': str(work)}
    print(f'removed from every probe environment (names only): {removed or "none"}', flush=True)

    def runner(lines, facts):
        for key, argv in (('node', [node, '--version']), ('npm', [npm, '--version']),
                          ('npm_prefix', [npm, 'prefix', '-g']), ('npm_root', [npm, 'root', '-g'])):
            done = subprocess.run(argv, capture_output=True, text=True, env=base_env, timeout=60)
            facts[key] = done.stdout.strip()
            lines.append(f'{key}: exit {done.returncode} {done.stdout.strip()} {done.stderr.strip()[:200]}')
        facts['which_node'], facts['which_npm'] = node, npm
        facts['arch'] = os.environ.get('PROCESSOR_ARCHITECTURE')
        return 0
    npm_root = Path(rec.guarded('runner', 'Node, npm, prefix and root', runner)['facts'].get('npm_root') or '.')

    rec.run('registry', f'npm view {PACKAGE}@{args.dsh_version} and dist-tags',
            [npm, 'view', f'{PACKAGE}@{args.dsh_version}', 'version', 'dist-tags', 'engines', 'os', 'cpu',
             'scripts', 'bin', 'optionalDependencies', '--json'], env=base_env, cwd=work, timeout=120)
    rec.run('install', f'npm install -g {PACKAGE}@{args.dsh_version}',
            [npm, 'install', '-g', '--foreground-scripts', '--no-audit', '--no-fund', f'{PACKAGE}@{args.dsh_version}'],
            env=base_env, cwd=work, timeout=900)

    package_dir = npm_root / '@deepseek-ai' / 'dsh'

    def package(lines, facts):
        facts['package_dir'] = str(package_dir)
        manifest = json.loads((package_dir / 'package.json').read_text(encoding='utf-8'))
        facts['package_json'] = {k: manifest.get(k) for k in ('name', 'version', 'os', 'cpu', 'engines', 'bin',
                                                              'scripts', 'optionalDependencies', 'type')}
        packages = walk_packages(npm_root)
        facts['packages_total'] = len(packages)
        facts['platform_packages_installed'] = sorted(f"{p['name']}@{p['version']} os={p['os']} cpu={p['cpu']}"
                                                      for p in packages if p['os'] or p['cpu'])
        facts['optional_sets_without_win32'] = sorted(f"{p['name']}: {p['optional']}" for p in packages
                                                      if p['optional'] and not any(WIN.search(o) for o in p['optional']))
        facts['install_scripts'] = sorted(f"{p['name']}@{p['version']}: {p['install_scripts']}"
                                          for p in packages if p['install_scripts'])
        facts['native_node_files'] = sorted(path.relative_to(npm_root).as_posix() for path in npm_root.rglob('*.node'))
        facts['which_dsh'] = shutil.which('dsh')
        try:
            where = subprocess.run(['where.exe', 'dsh'], capture_output=True, text=True)
            facts['where_dsh'] = where.stdout.split() or [f'exit {where.returncode} {where.stderr.strip()}']
        except OSError as error:
            facts['where_dsh'] = code_of(error)
        lines.append(json.dumps(facts, indent=1))
        return 0
    rec.guarded('package', 'installed package platform facts', package)

    dsh = shutil.which('dsh') or 'dsh'
    bin_js = package_dir / 'lib' / 'bin.js'
    rec.run('cli-version', 'dsh --version (npm shim)', [dsh, '--version'], env=dsh_env('cli'), cwd=work, timeout=120)
    rec.run('cli-node', 'node <bin.js> --version (no shim)', [node, str(bin_js), '--version'], env=dsh_env('cli'),
            cwd=work, timeout=120)
    rec.run('cli-help', 'dsh --help', [dsh, '--help'], env=dsh_env('cli'), cwd=work, timeout=120)
    rec.run('cli-web-help', 'dsh web --help', [dsh, 'web', '--help'], env=dsh_env('cli'), cwd=work, timeout=120)

    def preflight(lines, facts):
        from graftling.dsh import settings

        derived = Path(dsh).resolve().parent.parent / 'package.json'
        facts['derived_package_json'] = str(derived)
        facts['derived_exists'] = derived.exists()
        facts['refusal_at_derived'] = settings.dsh_version_refusal(derived)
        facts['refusal_at_package'] = settings.dsh_version_refusal(package_dir / 'package.json')
        sys.path.insert(0, str(repo / 'tests'))
        try:
            import conftest  # tests/conftest.py, as the suite reads it
            facts['conftest_unsupported_dsh'] = conftest.unsupported_dsh()
        except Exception as error:  # noqa: BLE001
            facts['conftest_unsupported_dsh'] = f'unreadable: {code_of(error)}'
        for label, config in (('prepare_which', {}), ('prepare_executable', {'executable': dsh})):
            try:
                prepared = settings.prepare({**config, 'model': 'probe-model', 'sandbox': 'read-only'}, work)
                facts[label] = {k: prepared[k] for k in ('executable', 'package', 'provider')}
            except Exception as error:  # noqa: BLE001
                facts[label] = code_of(error)
        served = settings.catalog_providers(str(package_dir / 'package.json'))
        facts['catalog_providers'] = None if served is None else len(served)
        lines.append(json.dumps(facts, indent=1, default=str))
        return 0
    rec.guarded('preflight', "graftling's DSH preflight on the installed DSH", preflight)

    def web_raw(lines, facts):
        from graftling.process_tree import OwnedTree

        tree = OwnedTree(kill_on_close=True)
        argv = [dsh, 'web', '--no-open', '--port', '0']
        start = time.monotonic()
        process = tree.start(argv, cwd=work, env=dsh_env('web-raw'), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                             errors='replace', bufsize=1)
        output, port = read_lines(process.stdout), None
        while time.monotonic() - start < 90:
            try:
                line = output.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                break
            lines.append(redact(line.rstrip()))
            match = LAUNCH.search(re.sub(r'\x1b\[[0-9;]*m', '', line))
            if match:
                port = match.group(1)
                break
        facts['launch_url_seen'] = port is not None
        facts['port'] = port
        facts['seconds_to_url'] = round(time.monotonic() - start, 1)
        facts['exit_before_url'] = process.poll()
        procs = tree_processes(tree)
        facts['job_processes'] = procs
        tree.close()  # the only handle to the job: kill-on-close ends it
        left = alive_after([row['pid'] for row in procs], 15)
        facts['alive_15s_after_job_handle_closed'] = left
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        facts['dsh_exit_code'] = process.poll()
        kill_left(left, lines)
        return 0 if port else 1
    rec.guarded('web-raw', 'dsh web in a kill-on-close job, then the job handle closed', web_raw)

    def service(lines, facts):
        from graftling.dsh.service import DshService

        svc = DshService(dsh, work, dsh_env('service'))
        start = time.monotonic()
        try:
            svc.start()
        except Exception as error:  # noqa: BLE001
            facts['start'] = code_of(error)
            lines.append(facts['start'])
            if svc.tree is not None:
                pids = svc.tree.pids()
                svc.tree.close()
                kill_left(alive_after(pids, 10), lines)
            return 1
        facts['start'] = f'ok in {round(time.monotonic() - start, 1)} s'
        facts['port'] = svc.origin.rsplit(':', 1)[-1]
        facts['cookie_received'] = bool(svc.cookie)
        procs = tree_processes(svc.tree)
        facts['job_processes'] = procs
        try:
            listed = svc.rpc('session/list', {})
            facts['session_list'] = f"ok: {len((listed or {}).get('items', []))} sessions"
        except Exception as error:  # noqa: BLE001
            facts['session_list'] = code_of(error)
        start = time.monotonic()
        try:
            svc.close()
            facts['close'] = f'ok in {round(time.monotonic() - start, 1)} s'
        except Exception as error:  # noqa: BLE001
            facts['close'] = code_of(error)
        facts['dsh_exit_code'] = svc.process.poll()
        left = alive_after([row['pid'] for row in procs], 15)
        facts['alive_15s_after_close'] = left
        kill_left(left, lines)
        lines.append(json.dumps(facts, indent=1, default=str))
        return 0
    rec.guarded('service', "graftling's DshService: start, session/list, close", service)

    def owner_death(lines, facts):
        from graftling.process import process_alive

        owner = subprocess.Popen([sys.executable, '-c', OWNER, dsh, str(work), json.dumps(dsh_env('owner'))],
                                 cwd=repo, env=base_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        output, report, start = read_lines(owner.stdout), None, time.monotonic()
        while report is None and time.monotonic() - start < 120:
            try:
                line = output.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                break
            lines.append(redact(line.rstrip()))
            if line.startswith('{'):
                report = json.loads(line)
        if report is None:
            owner.kill()
            facts['owner'] = f'no report within 120 s; owner exit {owner.poll()}'
            return 1
        facts['owner_report'] = report
        facts['alive_before_kill'] = [pid for pid in report['pids'] if process_alive(pid)]
        owner.kill()  # TerminateProcess: the owner dies without close()
        owner.wait(timeout=30)
        facts['owner_exit_code'] = owner.returncode
        left = alive_after(report['pids'], 15)
        facts['alive_15s_after_owner_killed'] = left
        kill_left(left, lines)
        return 0
    rec.guarded('owner-death', 'DshService owner killed without close: does the DSH job end with it?', owner_death)

    rec.write(meta)
    print(f"recorded {len(rec.records)} of {len(PROBES)} probes; findings in {out / 'findings.json'}", flush=True)
    return 0 if [r['probe'] for r in rec.records] == PROBES else 2


if __name__ == '__main__':
    try:
        status = main()
    except Exception:  # noqa: BLE001 - the recording itself failed
        traceback.print_exc()
        status = 2
    sys.exit(status)
