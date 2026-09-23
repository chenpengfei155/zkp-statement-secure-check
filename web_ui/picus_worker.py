#!/usr/bin/env python3
"""Linux supervisor for the pinned Picus artifact. Only stdlib is required.

stdin is a lifetime/control pipe: 'cancel' OR EOF cancels the process group.
stdout is our JSON-lines protocol, never unstructured child output.
"""
import argparse
import ctypes
from collections import deque
import json
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

if os.name != 'nt':
    import resource

REVISION = '138b151d3a388e5b6c040c163e0a1db04f2ceda6'
CVC5_REVISION = 'de62429fa7c03a46d5d75f9d78fc8888792a0798'
SCOPE = 'Same public and private inputs imply unique public outputs (Picus weak safety).'
MAX_LINE = 65536


def emit(kind, **data):
    print(json.dumps({'type': kind, **data}, ensure_ascii=True), flush=True)


def environment(prefix):
    env = dict(os.environ, PLTSTDERR='error none@picus')
    env['PATH'] = os.pathsep.join((str(prefix / 'racket-8.16/bin'), str(prefix / 'bin'), env.get('PATH', '')))
    env['SOLVER_PATH'] = str(prefix / 'bin/cvc5')
    return env


def check_environment(prefix):
    result = {'engine': 'picus', 'revision': REVISION, 'solver': 'cvc5', 'ready': False}
    try:
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
    def __init__(self):
        self.logs = deque(maxlen=1000)
        self.log_count = 0
        self.invalid = False
        self.failed = False
        self.final = None
        self.section = None
        self.values = {'inputs': {}, 'first possible outputs': {}, 'second possible outputs': {}}
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
            if not self.cex_invalid and (first or second):
                report['counterexample'] = {
                    'inputs': [{'wire': w, 'value': v} for w, v in sorted(self.values['inputs'].items()) if w != 0],
                    'outputs': [{'wire': w, 'first': first.get(w), 'second': second.get(w)}
                                for w in sorted(first.keys() | second.keys())],
                    'truncated': self.cex_truncated}
        return report


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


def run(args):
    started = time.monotonic()
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
    parsed = PicusOutput()
    process = None
    with tempfile.TemporaryDirectory(prefix='cirverify_picus_') as job:
        try:
            path = Path(job) / 'circuit.r1cs'
            shutil.copyfile(args.file, path)
            env = environment(prefix)
            env['TMPDIR'] = job
            # This worker is a disposable process. Limits are inherited by every child.
            memory = args.memory_mib * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            process = subprocess.Popen(
                ['racket', str(prefix / 'Picus/picus.rkt'), '--json', '-', '--truncate', 'off',
                 '--log-level', 'PROGRESS', '--solver', 'cvc5', '--timeout', str(args.query_timeout_ms), str(path)],
                cwd=job, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True)
            lines = queue.Queue(maxsize=256)
            def read(stream, stderr):
                while True:
                    raw = stream.readline(MAX_LINE + 1)
                    if not raw:
                        break
                    lines.put((raw.decode('utf-8', errors='replace'), stderr))
                lines.put((None, stderr))
            readers = [threading.Thread(target=read, args=(process.stdout, False), daemon=True),
                       threading.Thread(target=read, args=(process.stderr, True), daemon=True)]
            for reader in readers:
                reader.start()
            ended = 0
            reason = None
            next_tick = 0
            while ended < 2 or process.poll() is None:
                elapsed = time.monotonic() - started
                if cancelled.is_set() or elapsed >= args.timeout_seconds:
                    reason = 'cancelled' if cancelled.is_set() else 'timeout'
                    stop_group(process)
                    break
                try:
                    line, stderr = lines.get(timeout=0.1)
                    if line is None:
                        ended += 1
                    else:
                        parsed.consume(line, stderr)
                except queue.Empty:
                    pass
                if elapsed >= next_tick:
                    emit('progress', message=parsed.stage, elapsed_seconds=round(elapsed, 1))
                    next_tick = elapsed + 0.5
            process.wait(timeout=5)
            report = parsed.report(process.returncode, time.monotonic() - started, reason)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            parsed.log(str(error))
            report = parsed.report(None, time.monotonic() - started, 'process_error')
        finally:
            if process is not None:
                stop_group(process)
                for stream in (process.stdout, process.stderr):
                    stream.close()
                # All adopted children received SIGKILL in stop_group.
                while True:
                    try:
                        os.waitpid(-1, 0)
                    except ChildProcessError:
                        break
    # Emit the terminal result only after processes and temporary files are gone.
    emit('result', report=report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'run'])
    parser.add_argument('--home', default='~/.local/share/cirverify-picus')
    parser.add_argument('--file')
    parser.add_argument('--timeout-seconds', type=float, default=120)
    parser.add_argument('--query-timeout-ms', type=int, default=5000)
    parser.add_argument('--memory-mib', type=int, default=4096)
    args = parser.parse_args()
    if args.command == 'check':
        emit('environment', **check_environment(Path(args.home).expanduser().resolve()))
    else:
        run(args)


if __name__ == '__main__':
    main()
