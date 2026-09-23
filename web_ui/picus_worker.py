#!/usr/bin/env python3
"""Native supervisor for the pinned Picus artifact. Only stdlib is required.

stdin is a lifetime/control pipe: 'cancel' OR EOF cancels the process group.
stdout is our JSON-lines protocol, never unstructured child output.
"""
import argparse
import ctypes
from collections import deque
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui.r1cs_checks import CHECK_NAMES, aggregate, check_result, prepare_checks
from web_ui.picus_runtime import REVISION, CVC5_REVISION, default_home, picus_command, solver_path, validate_runtime

if os.name == 'nt':
    from web_ui.windows_process import ProcessJob, checked_run
else:
    import resource

SCOPE = 'Output/all-signal uniqueness, constraint satisfiability, and structural checks.'
MAX_LINE = 65536


def emit(kind, **data):
    print(json.dumps({'type': kind, **data}, ensure_ascii=True), flush=True)


def environment(prefix):
    env = dict(os.environ, PLTSTDERR='error none@picus')
    if os.name == 'nt':
        for name in ('PLTCOLLECTS', 'PLTCONFIGDIR', 'PLTADDONDIR'):
            env.pop(name, None)
        env['PLTUSERHOME'] = str(prefix / 'state')
    else:
        env['PATH'] = os.pathsep.join((str(prefix / 'racket-8.16/bin'), str(prefix / 'bin'), env.get('PATH', '')))
    env['SOLVER_PATH'] = str(solver_path(prefix))
    return env


def check_environment(prefix):
    result = {'engine': 'picus', 'revision': REVISION, 'solver': 'cvc5', 'ready': False,
              'platform': 'windows-native' if os.name == 'nt' else 'linux-native'}
    try:
        if os.name == 'nt':
            validate_runtime(prefix)
            env = environment(prefix)
            help_result = checked_run(picus_command(prefix) + ['--help'], env=env)
            if help_result.returncode or 'run-picus' not in help_result.stdout:
                raise ValueError('The bundled Picus executable could not start: ' + help_result.stderr[:500])
            # A linear query can succeed even when cvc5 was built without CoCoA.
            with tempfile.TemporaryDirectory(prefix='cirverify_picus_check_') as directory:
                query = Path(directory) / 'nonlinear.smt2'
                query.write_text('(set-logic QF_FF)\n(declare-fun x () (_ FiniteField 17))\n'
                                 '(assert (= (ff.mul x x) #f3m17))\n(check-sat)\n', encoding='ascii')
                probe = checked_run([str(solver_path(prefix)), '--lang=smt2', '--tlimit-per=5000', str(query)],
                                    env=env, timeout=8)
            if probe.returncode or probe.stdout.strip() != 'unsat':
                raise ValueError('cvc5 nonlinear finite-field self-check failed: ' + probe.stderr[:500])
            result.update(ready=True, reason='Native Windows Picus and finite-field cvc5 are ready.')
            return result
        repo = prefix / 'Picus'
        revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                                           stderr=subprocess.PIPE, timeout=5, text=True).strip()
        if revision != REVISION:
            raise ValueError('Picus revision differs from the supported pinned version. Run setup_picus.ps1.')
        if (prefix / 'cvc5-revision').read_text().strip() != CVC5_REVISION:
            raise ValueError('The pinned cvc5 installation is missing. Run setup_picus.ps1.')
        racket = shutil.which('racket', path=environment(prefix)['PATH'])
        if not racket:
            raise ValueError('Racket is missing. Run scripts/setup_picus.ps1.')
        subprocess.run([racket, '-e', '(require rosette csv-reading graph)'],
                       env=environment(prefix), check=True, capture_output=True, timeout=20)
        smt = '(set-logic QF_FF)\n(declare-fun x () (_ FiniteField 17))\n(assert (= x #f1m17))\n(check-sat)\n'
        probe = subprocess.run([str(prefix / 'bin/cvc5'), '--lang', 'smt2'], input=smt,
                               text=True, capture_output=True, timeout=5, check=True)
        if probe.stdout.strip() != 'sat':
            raise ValueError('cvc5 finite-field self-check failed.')
        result.update(ready=True, reason='Picus and finite-field solver are ready.')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result['reason'] = 'Picus is not ready: ' + str(error) + ' Run scripts/setup_picus.ps1.'
    return result


class PicusOutput:
    """Read the artifact's log events, not a fabricated single JSON result."""
    def __init__(self, strong=False):
        self.strong = strong
        self.logs = deque(maxlen=1000)
        self.log_count = 0
        self.invalid = False
        self.failed = False
        self.final = None
        self.section = None
        self.values = {name: {} for name in ('inputs', 'first possible outputs', 'second possible outputs',
                                           'first internal variables', 'second internal variables')}
        self.cex_truncated = False
        self.cex_invalid = False
        self.stage = 'Starting Picus'

    def log(self, message):
        self.log_count += 1
        self.logs.append(message[:1000])

    def consume(self, line, stderr=False):
        if stderr:
            self.log('stderr: ' + line.rstrip())
            return
        try:
            event = json.loads(line)
            message = event['msg']
            if (not isinstance(message, str) or event.get('logger_name') != 'picus'
                    or event.get('level') not in ('DEBUG', 'INFO', 'PROGRESS', 'ACCOUNTING', 'WARNING', 'ERROR', 'CRITICAL')):
                raise ValueError('Unexpected log protocol')
        except (ValueError, KeyError, TypeError):
            self.invalid = True
            self.log('Invalid Picus log: ' + line.rstrip())
            return
        self.log(line.rstrip())
        if str(event.get('level', '')).upper() in ('ERROR', 'CRITICAL'):
            self.failed = True
        text = message.strip()
        terminals = {'The circuit is properly constrained': 'safe',
                     'The circuit is underconstrained': 'unsafe',
                     'Cannot determine whether the circuit is properly constrained': 'unknown'}
        if text in terminals:
            if self.final is not None and self.final != terminals[text]:
                self.invalid = True
            self.final = terminals[text]
        if str(event.get('level', '')).upper() == 'PROGRESS':
            self.stage = text[:200]
        if text.endswith(':') and text[:-1] in (*self.values, 'first internal variables', 'second internal variables'):
            self.section = text[:-1]
        elif self.section in self.values:
            match = re.fullmatch(r'(\d+):\s*(-?\d+)', text)
            if match:
                wire = int(match[1])
                values = self.values[self.section]
                if len(values) < 500:
                    values[wire] = match[2]
                else:
                    self.cex_truncated = True
            elif text.startswith('no ') or text.startswith('Exiting Picus'):
                self.section = None
            elif text:
                self.cex_invalid = True

    def report(self, code, elapsed, reason=None):
        verdict = {8: 'safe', 9: 'unsafe', 0: 'unknown'}.get(code, 'error')
        if reason == 'timeout':
            verdict = 'unknown'
        elif reason == 'cancelled':
            verdict = 'cancelled'
        elif verdict == 'error' or self.invalid or self.failed or self.final != verdict:
            bad_protocol = self.invalid or (code in (0, 8, 9) and self.final != verdict)
            verdict = 'error'
            reason = reason or ('invalid_output' if bad_protocol else 'process_error')
        report = {'kind': 'r1cs', 'engine': 'picus', 'revision': REVISION, 'solver': 'cvc5',
                  'scope': SCOPE, 'verdict': verdict, 'exit_code': code,
                  'reason': reason or ('solver_inconclusive' if verdict == 'unknown' else 'completed'),
                  'elapsed_seconds': round(elapsed, 2), 'logs': list(self.logs),
                  'logs_truncated': self.log_count > len(self.logs), 'counterexample': None}
        if verdict == 'unsafe':
            first, second = (self.values[name] for name in ('first possible outputs', 'second possible outputs'))
            internal1, internal2 = (self.values[name] for name in ('first internal variables', 'second internal variables'))
            if not self.cex_invalid and (first or second or internal1 or internal2):
                report['counterexample'] = {
                    'inputs': [{'wire': w, 'value': v} for w, v in sorted(self.values['inputs'].items()) if w != 0],
                    'outputs': [{'wire': w, 'first': first.get(w), 'second': second.get(w)}
                                for w in sorted(first.keys() | second.keys())],
                    'internal': [{'wire': w, 'first': internal1.get(w), 'second': internal2.get(w)}
                                 for w in sorted(internal1.keys() | internal2.keys())],
                    'truncated': self.cex_truncated}
        return report


class SatisfiabilityOutput:
    """Only a successful, single cvc5 sat/unsat/unknown response is a conclusion."""
    def __init__(self):
        self.stage = 'Checking constraint satisfiability'
        self.lines = []
        self.logs = deque(maxlen=1000)
        self.invalid = False

    def consume(self, line, stderr=False):
        self.logs.append(('stderr: ' if stderr else '') + line.rstrip()[:1000])
        if not stderr and line.strip():
            if line.strip() not in ('sat', 'unsat', 'unknown') or self.lines:
                self.invalid = True
            self.lines.append(line.strip()[:100])

    def report(self, code, elapsed, reason=None):
        value = self.lines[0] if len(self.lines) == 1 else None
        status, message = {
            'sat': ('pass', 'At least one assignment satisfies all constraints.'),
            'unsat': ('fail', 'No assignment satisfies all constraints.'),
            'unknown': ('unknown', 'The solver could not determine whether the constraints have a solution.'),
        }.get(value, ('error', 'Invalid or incomplete cvc5 response.'))
        if reason in ('timeout', 'cancelled'):
            status = 'unknown' if reason == 'timeout' else 'cancelled'
            message = 'Satisfiability check timed out.' if reason == 'timeout' else 'Check cancelled.'
        elif code != 0 or self.invalid:
            status, message = 'error', 'The cvc5 satisfiability check failed. See the run logs.'
        return check_result('satisfiability', status, message, solver_result=value,
                            reason=reason or ('solver_inconclusive' if status == 'unknown' else
                                              'process_error' if status == 'error' else 'completed'),
                            elapsed_seconds=round(elapsed, 2), exit_code=code, logs=list(self.logs))


def stop_group(process):
    # Always reap descendants as well, even if the Racket group leader exited first.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def run_child(command, parsed, job, env, cancelled, deadline, task_started, label, memory_mib=4096):
    """Each stage owns its descendants, on Windows as well as Linux."""
    started = time.monotonic()
    process = None
    process_job = None
    reason = None
    stop_reader = threading.Event()
    threads = []
    def terminate():
        if process_job is not None:
            process_job.close()
        elif process is not None:
            stop_group(process)
    try:
        if cancelled.is_set() or started >= deadline:
            return parsed.report(None, 0, 'cancelled' if cancelled.is_set() else 'timeout')
        if os.name == 'nt':
            process_job = ProcessJob(memory_mib)
            process = process_job.spawn(command, cwd=job, env=env)
        else:
            process = subprocess.Popen(command, cwd=job, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        lines = queue.Queue(maxsize=256)
        def enqueue(item):
            while not stop_reader.is_set():
                try:
                    lines.put(item, timeout=0.1)
                    return
                except queue.Full:
                    pass
        def read(stream, stderr):
            while not stop_reader.is_set():
                raw = stream.readline(MAX_LINE + 1)
                if not raw:
                    break
                enqueue((raw.decode('utf-8', errors='replace'), stderr))
            enqueue((None, stderr))
        for stream, stderr in ((process.stdout, False), (process.stderr, True)):
            thread = threading.Thread(target=read, args=(stream, stderr), daemon=True)
            thread.start()
            threads.append(thread)
        ended = 0
        next_tick = 0
        while ended < 2 or process.poll() is None:
            now = time.monotonic()
            if cancelled.is_set() or now >= deadline:
                reason = 'cancelled' if cancelled.is_set() else 'timeout'
                terminate()
                break
            try:
                line, stderr = lines.get(timeout=0.1)
                if line is None:
                    ended += 1
                else:
                    parsed.consume(line, stderr)
            except queue.Empty:
                pass
            if now >= next_tick:
                emit('progress', message=f'{label}: {parsed.stage}',
                     elapsed_seconds=round(now - task_started, 1))
                next_tick = now + 0.5
        process.wait(timeout=5)
        return parsed.report(process.returncode, time.monotonic() - started, reason)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parsed.consume(str(error), stderr=True)
        return parsed.report(None, time.monotonic() - started, 'process_error')
    finally:
        terminate()
        stop_reader.set()
        if process is not None:
            process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=2)
            for stream in (process.stdout, process.stderr):
                stream.close()
            if process.stdin is not None:
                process.stdin.close()
            if os.name != 'nt':
                while True:
                    try:
                        os.waitpid(-1, 0)
                    except ChildProcessError:
                        break


def run(args):
    started = time.monotonic()
    if os.name != 'nt':
        # Adopt solver grandchildren so even orphaned children are reaped here.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), 'Cannot supervise Picus child processes')
    cancelled = threading.Event()
    def control():
        for line in sys.stdin:
            if line.strip() == 'cancel':
                break
        cancelled.set()  # EOF means the Windows owner disappeared.
    threading.Thread(target=control, daemon=True).start()
    prefix = Path(args.home).expanduser().resolve()
    deadline = started + args.timeout_seconds
    checks = {key: check_result(key, 'skipped', 'Check did not run.', reason='not_started')
              for key in CHECK_NAMES}
    no_outputs = False
    active = None
    last_tick = 0
    preparing = True
    def checkpoint():
        nonlocal last_tick
        now = time.monotonic()
        if cancelled.is_set() or now >= deadline:
            raise InterruptedError('cancelled' if cancelled.is_set() else 'timeout')
        if preparing and now >= last_tick:
            emit('progress', message='Scanning constraints and preparing satisfiability query',
                 elapsed_seconds=round(now - started, 1))
            last_tick = now + 0.5

    # When launched by the web service, the parent also owns this directory and
    # can remove it after a hard supervisor failure. Direct runs own their temp.
    context = nullcontext(args.job_dir) if args.job_dir else tempfile.TemporaryDirectory(prefix='cirverify_picus_')
    with context as job:
        try:
            checkpoint()
            path = Path(job) / 'circuit.r1cs'
            shutil.copyfile(args.file, path)
            env = environment(prefix)
            env['TMPDIR'] = job
            env['TMP'] = env['TEMP'] = job
            if os.name != 'nt':
                # Disposable Linux worker; Windows stages use Job Object limits.
                memory = args.memory_mib * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            reader = R1CSFile(path)
            no_outputs = not reader.metadata['public_outputs']
            if no_outputs:
                checks['output_uniqueness'] = check_result('output_uniqueness', 'skipped',
                    'No public outputs to check.', reason='no_outputs')
            query_path = Path(job) / 'constraints.smt2'
            for check in prepare_checks(reader, query_path, checkpoint):
                checks[check['id']] = check
            preparing = False
            active = 'satisfiability'
            checks[active] = run_child(
                [str(solver_path(prefix)), '--lang', 'smt2', f'--tlimit-per={args.query_timeout_ms}', str(query_path)],
                SatisfiabilityOutput(), job, env, cancelled,
                min(deadline, time.monotonic() + args.query_timeout_ms / 1000 + 2), started, CHECK_NAMES[active], args.memory_mib)
            if checks[active]['status'] == 'fail':
                for key in ('output_uniqueness', 'signal_uniqueness'):
                    if checks[key].get('reason') != 'no_outputs':
                        checks[key] = check_result(key, 'skipped',
                            'Skipped because the constraint system has no solution.', reason='unsatisfiable')
            else:
                for strong in (False, True):
                    active = 'signal_uniqueness' if strong else 'output_uniqueness'
                    if not strong and no_outputs:
                        continue
                    checkpoint()
                    # Reserve half the remaining task budget for strong mode.
                    stage_deadline = deadline if strong else time.monotonic() + max(0, deadline - time.monotonic()) / 2
                    command = picus_command(prefix) + ['--json', '-', '--truncate', 'off',
                               '--log-level', 'PROGRESS', '--solver', 'cvc5', '--timeout', str(args.query_timeout_ms)]
                    raw = run_child(command + (['--strong'] if strong else []) + [str(path)],
                                    PicusOutput(strong), job, env, cancelled, stage_deadline, started, CHECK_NAMES[active], args.memory_mib)
                    status = {'safe': 'pass', 'unsafe': 'warning' if strong else 'fail',
                              'unknown': 'unknown', 'error': 'error', 'cancelled': 'cancelled'}[raw['verdict']]
                    messages = {
                        'pass': 'All signals are uniquely determined by the inputs.' if strong else 'Output uniqueness verified.',
                        'warning': 'Some output or internal wires are not uniquely determined. Review the counterexample.',
                        'fail': 'Picus found different valid outputs for identical inputs.',
                        'unknown': 'Uniqueness could not be determined within the time limit.',
                        'error': 'Picus could not complete this check. See the run logs.',
                        'cancelled': 'Check cancelled.',
                    }
                    checks[active] = check_result(active, status, messages[status],
                        **{k: raw[k] for k in ('verdict', 'reason', 'elapsed_seconds', 'exit_code', 'counterexample', 'logs', 'logs_truncated')})
            active = None
            checkpoint()
        except (OSError, ValueError, MemoryError, subprocess.SubprocessError) as error:
            reason = str(error) if isinstance(error, InterruptedError) else 'runtime_error'
            status = {'timeout': 'unknown', 'cancelled': 'cancelled'}.get(reason, 'error')
            for key, check in checks.items():
                if check.get('reason') == 'not_started' or key == active:
                    checks[key] = check_result(key, status,
                        'Task time limit reached.' if reason == 'timeout' else 'Check cancelled.' if reason == 'cancelled'
                        else f'Analysis could not complete: {error}', reason=reason)
    # Emit the terminal result only after processes and temporary files are gone.
    results = list(checks.values())
    verdict, reason = aggregate(results, no_outputs)
    if cancelled.is_set():
        verdict, reason = 'cancelled', 'cancelled'
    logs = [f'[{item["name"]}] {line}' for item in results for line in item.get('logs', [])]
    report = {'kind': 'r1cs', 'engine': 'picus', 'revision': REVISION, 'solver': 'cvc5',
              'platform': 'windows-native' if os.name == 'nt' else 'linux-native',
              'scope': SCOPE, 'verdict': verdict, 'reason': reason, 'checks': results,
              'elapsed_seconds': round(time.monotonic() - started, 2),
              'exit_code': checks['output_uniqueness'].get('exit_code'),
              'counterexample': checks['output_uniqueness'].get('counterexample'),
              'logs': logs[-1000:], 'logs_truncated': len(logs) > 1000 or any(c.get('logs_truncated') for c in results)}
    emit('result', report=report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'run'])
    parser.add_argument('--home', default=default_home())
    parser.add_argument('--file')
    parser.add_argument('--job-dir')
    parser.add_argument('--timeout-seconds', type=float, default=120)
    parser.add_argument('--query-timeout-ms', type=int, default=5000)
    parser.add_argument('--memory-mib', type=int, default=4096)
    args = parser.parse_args()
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0 or args.query_timeout_ms <= 0 or args.memory_mib < 256:
        parser.error('Invalid time or memory limits.')
    if args.command == 'check':
        emit('environment', **check_environment(Path(args.home).expanduser().resolve()))
    else:
        run(args)


if __name__ == '__main__':
    main()
