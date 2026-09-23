"""Route-one experiment: original Picus + cvc5, running natively on Windows.

This is an isolated validation runner, not the web application's backend.
No WSL command is used. Build the portable runtime separately first.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui.picus_worker import PicusOutput, SatisfiabilityOutput, REVISION
from web_ui.r1cs_checks import CHECK_NAMES, aggregate, check_result, prepare_checks


class ProcessJob:
    """Kill the whole Windows process tree when this job's handle closes.

The child Python shim waits for stdin before launching anything, so assignment
to the job is complete before Racket can create a solver child.
"""
    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.QueryInformationJobObject.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, x64: LimitFlags at byte 16.
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            self.close()
            raise RuntimeError('This probe requires 64-bit Python.')
        information = ctypes.create_string_buffer(144)
        ctypes.c_uint32.from_buffer(information, 16).value = 0x2100  # KILL_ON_JOB_CLOSE | PROCESS_MEMORY
        ctypes.c_uint64.from_buffer(information, 112).value = 4 * 1024 ** 3
        if not self.api.SetInformationJobObject(self.handle, 9, information, len(information)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def attach(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            accounting = ctypes.create_string_buffer(48)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if not self.api.QueryInformationJobObject(self.handle, 1, accounting, 48, None):
                    break
                if ctypes.c_uint32.from_buffer(accounting, 40).value == 0:
                    break
                time.sleep(0.01)
            self.api.CloseHandle(self.handle)
            self.handle = None


def run(command, env, cwd, seconds, parsed=None):
    started = time.monotonic()
    job = ProcessJob()
    process = None
    reason = None
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child', *command],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, cwd=cwd, creationflags=subprocess.CREATE_NO_WINDOW)
        job.attach(process)
        try:
            stdout, stderr = process.communicate(b'go\n', timeout=max(0.001, seconds))
        except subprocess.TimeoutExpired:
            reason = 'timeout'
            job.close()
            stdout, stderr = process.communicate(timeout=5)
        if parsed is not None:
            for line in stdout.decode('utf-8', errors='replace').splitlines():
                parsed.consume(line)
            for line in stderr.decode('utf-8', errors='replace').splitlines():
                parsed.consume(line, stderr=True)
            return parsed.report(process.returncode, time.monotonic() - started, reason)
        return dict(exit_code=process.returncode, reason=reason,
                    stdout=stdout.decode('utf-8', errors='replace'),
                    stderr=stderr.decode('utf-8', errors='replace'),
                    elapsed_seconds=round(time.monotonic() - started, 3))
    finally:
        job.close()
        if process is not None:
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def counterexample_valid(reader, example, strong=False):
    if not example or example.get('truncated'):
        return None
    pairs = [{0: 1}, {0: 1}]
    for item in example['inputs']:
        for values in pairs:
            values[item['wire']] = int(item['value']) % reader.prime
    for category in ('outputs', 'internal'):
        for item in example.get(category, []):
            for values, key in zip(pairs, ('first', 'second')):
                if item[key] is not None:
                    values[item['wire']] = int(item[key]) % reader.prime
    rows = list(reader.iter_constraints())
    needed = {term['wire'] for row in rows for key in ('a', 'b', 'c') for term in row[key]}
    if any(not needed <= values.keys() for values in pairs):
        return None
    for values in pairs:
        for row in rows:
            a, b, c = (sum(int(term['coefficient']) * values[term['wire']] for term in row[key])
                       for key in ('a', 'b', 'c'))
            if (a * b - c) % reader.prime:
                return False
    candidates = pairs[0].keys() & pairs[1].keys()
    if not strong:
        candidates &= set(range(1, 1 + reader.metadata['public_outputs']))
    return any(pairs[0][wire] != pairs[1][wire] for wire in candidates)


def analyze(path, runtime, timeout_ms, case_seconds):
    started = time.monotonic()
    reader = R1CSFile(path)
    env = dict(os.environ, PLTUSERHOME=str(runtime / 'racket-user'), PLTSTDERR='error none@picus',
               SOLVER_PATH=str(runtime / 'cvc5.exe'))
    with tempfile.TemporaryDirectory(prefix='native_picus_电路 测试_', dir=runtime / 'jobs') as directory:
        job = Path(directory)
        # Exercise Unicode and spaces in the actual native Racket/cvc5 argument path.
        circuit = job / '电路 sample.r1cs'
        shutil.copyfile(path, circuit)
        env.update(TEMP=str(job), TMP=str(job))
        query = job / 'constraints.smt2'
        structural = prepare_checks(reader, query)
        sat = run([str(runtime / 'cvc5.exe'), '--lang', 'smt2', f'--tlimit-per={timeout_ms}', str(query)],
                  env, job, min(case_seconds, timeout_ms / 1000 + 2), SatisfiabilityOutput())
        checks = [sat]
        for strong in (False, True):
            key = 'signal_uniqueness' if strong else 'output_uniqueness'
            if not strong and not reader.metadata['public_outputs']:
                checks.append(check_result(key, 'skipped', 'No public outputs.', reason='no_outputs'))
                continue
            if sat['status'] == 'fail':
                checks.append(check_result(key, 'skipped', 'Constraints have no solution.', reason='unsatisfiable'))
                continue
            remaining = case_seconds - (time.monotonic() - started)
            parser = PicusOutput(strong)
            command = [str(runtime / 'racket/racket.exe'), str(runtime / 'Picus/picus.rkt'),
                       '--json', '-', '--truncate', 'off', '--log-level', 'PROGRESS',
                       '--solver', 'cvc5', '--timeout', str(timeout_ms)]
            raw = run(command + (['--strong'] if strong else []) + [str(circuit)], env, job,
                      remaining if strong else remaining / 2, parser)
            status = {'safe': 'pass', 'unsafe': 'warning' if strong else 'fail',
                      'unknown': 'unknown', 'error': 'error', 'cancelled': 'cancelled'}[raw['verdict']]
            check = check_result(key, status, raw['verdict'], **{k: raw[k] for k in
                ('verdict', 'reason', 'elapsed_seconds', 'exit_code', 'counterexample', 'logs')})
            if raw['counterexample']:
                check['counterexample_verified'] = counterexample_valid(reader, raw['counterexample'], strong)
            checks.append(check)
    checks += structural
    verdict, reason = aggregate(checks, not reader.metadata['public_outputs'])
    return dict(verdict=verdict, reason=reason, checks=checks,
                elapsed_seconds=round(time.monotonic() - started, 3), metadata=reader.metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT / 'build/windows-picus-probe')
    parser.add_argument('--reference', type=Path, default=ROOT / 'build/native-r1cs-probe/combined-results.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/windows-picus-probe/results.json')
    parser.add_argument('--timeout-ms', type=int, default=5000)
    parser.add_argument('--case-seconds', type=float, default=40)
    parser.add_argument('--case', action='append')
    parser.add_argument('files', type=Path, nargs='*')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Run with Windows Python, not WSL.')
    if args.timeout_ms <= 0 or args.case_seconds <= 0:
        parser.error('Timeouts must be positive.')
    runtime = args.runtime.resolve()
    for relative in ('racket/racket.exe', 'Picus/picus.rkt', 'cvc5.exe'):
        if not (runtime / relative).is_file():
            parser.error(f'Missing native runtime: {runtime / relative}')
    (runtime / 'jobs').mkdir(exist_ok=True)
    revision = subprocess.check_output(['git', '-C', str(runtime / 'Picus'), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != REVISION:
        parser.error('Unexpected Picus revision.')
    reference = json.loads(args.reference.read_text(encoding='utf-8')) if args.reference.exists() else {}
    entries = reference.get('cases', [])
    if args.files:
        entries = [dict(name=p.stem, file=str(p.resolve())) for p in args.files]
    elif not entries:
        entries = [dict(name=p.stem, file=str(p)) for p in (ROOT / 'demo.r1cs', ROOT / 'demo1.r1cs')]
    if args.case:
        entries = [entry for entry in entries if entry['name'] in args.case]
    if not entries:
        parser.error('No matching cases.')
    report = dict(created_utc=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
                  python=sys.executable, runtime=str(runtime), revision=revision,
                  cvc5_sha256=hashlib.sha256((runtime / 'cvc5.exe').read_bytes()).hexdigest(),
                  query_timeout_ms=args.timeout_ms, case_timeout_seconds=args.case_seconds,
                  windows_process_commit_limit_mib=4096, cases=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        name = entry['name']
        path = Path(entry.get('file', entry.get('path', '')))
        if not path.is_absolute():
            path = ROOT / path
        result = analyze(path, runtime, args.timeout_ms, args.case_seconds)
        statuses = {c['id']: c['status'] for c in result['checks']}
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        row = dict(name=name, file=str(path), sha256=digest, native=result, statuses=statuses)
        if 'sha256' in entry:
            row['reference_input_matches'] = digest == entry['sha256']
        expected = entry.get('expected_properties', entry.get('expected'))
        if expected:
            row['expected'] = expected
            row['expected_match'] = all(statuses[k] == v for k, v in expected.items())
        baseline = entry.get('picus')
        if baseline:
            baseline_status = {c['id']: c['status'] for c in baseline['checks']}
            row['reference_statuses'] = baseline_status
            row['reference_match'] = statuses == baseline_status
        report['cases'].append(row)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(dict(name=name, **statuses, seconds=result['elapsed_seconds']), ensure_ascii=True), flush=True)
    return int(any('error' in row['statuses'].values() or any(c.get('counterexample_verified') is False
        for c in row['native']['checks']) for row in report['cases']))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        if sys.stdin.buffer.readline() != b'go\n':
            sys.exit(1)
        sys.exit(subprocess.run(sys.argv[2:], stdin=subprocess.DEVNULL).returncode)
    sys.exit(main())
