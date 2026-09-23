"""Windows/WSL bridge. No client-supplied command or filesystem path is accepted."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from .picus_worker import REVISION, SCOPE
from .r1cs_checks import CHECK_NAMES, aggregate, check_result


@dataclass(frozen=True)
class PicusConfig:
    distro: str = 'Ubuntu-22.04'
    home: str = '~/.local/share/cirverify-picus'
    timeout_seconds: float = 120
    query_timeout_ms: int = 5000
    memory_mib: int = 4096

    @classmethod
    def from_env(cls):
        config = cls(os.getenv('CIRVERIFY_PICUS_DISTRO', cls.distro),
                     os.getenv('CIRVERIFY_PICUS_HOME', cls.home),
                     float(os.getenv('CIRVERIFY_PICUS_TIMEOUT', cls.timeout_seconds)),
                     int(os.getenv('CIRVERIFY_PICUS_QUERY_TIMEOUT_MS', cls.query_timeout_ms)),
                     int(os.getenv('CIRVERIFY_PICUS_MEMORY_MIB', cls.memory_mib)))
        if not math.isfinite(config.timeout_seconds) or config.timeout_seconds <= 0 or config.query_timeout_ms <= 0 or config.memory_mib < 256:
            raise ValueError('Invalid Picus time or memory limits.')
        return config


def empty_report(verdict, reason):
    return {'kind': 'r1cs', 'engine': 'picus', 'revision': REVISION, 'solver': 'cvc5',
            'scope': SCOPE, 'verdict': verdict, 'reason': reason,
            'elapsed_seconds': 0, 'exit_code': None, 'logs': [],
            'logs_truncated': False, 'counterexample': None,
            'checks': [check_result(key, 'cancelled' if verdict == 'cancelled' else 'error' if verdict == 'error'
                                    else 'unknown', 'Check did not complete.', reason=reason)
                       for key in CHECK_NAMES]}


def valid_checks(report):
    """Do not accept a passing headline with missing or inconclusive check results."""
    checks = report.get('checks')
    if not isinstance(checks, list) or len(checks) != len(CHECK_NAMES):
        return False
    statuses = {'pass', 'fail', 'warning', 'unknown', 'error', 'cancelled', 'skipped'}
    if any(not isinstance(item, dict) or not isinstance(item.get('id'), str)
           or not isinstance(item.get('status'), str) or item['status'] not in statuses for item in checks):
        return False
    if {item['id'] for item in checks} != set(CHECK_NAMES):
        return False
    no_outputs = any(c['id'] == 'output_uniqueness' and c.get('reason') == 'no_outputs' for c in checks)
    expected, _ = aggregate(checks, no_outputs)
    return report.get('verdict') == expected or report.get('verdict') == 'cancelled'


class PicusEngine:
    def __init__(self, config=None):
        self.configuration_error = None
        try:
            self.config = config or PicusConfig.from_env()
        except ValueError as error:
            self.config = PicusConfig()
            self.configuration_error = 'Invalid server-side Picus configuration: ' + str(error)
        self._status = None
        self._checked = 0
        self._lock = threading.Lock()

    def linux_path(self, path):
        if os.name != 'nt':
            return str(Path(path).resolve())
        result = subprocess.run(['wsl.exe', '-d', self.config.distro, '--exec', 'wslpath', '-u',
                                 str(Path(path).resolve())], capture_output=True, timeout=10,
                                encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Cannot access WSL/Ubuntu. Check CIRVERIFY_PICUS_DISTRO.')
        return result.stdout.strip()

    def command(self, mode):
        script = self.linux_path(Path(__file__).with_name('picus_worker.py'))
        prefix = ['wsl.exe', '-d', self.config.distro, '--exec', 'python3'] if os.name == 'nt' else [sys.executable]
        return prefix + [script, mode, '--home', self.config.home]

    def status(self, refresh=False):
        if self.configuration_error:
            return {'engine': 'picus', 'revision': REVISION, 'ready': False, 'reason': self.configuration_error}
        with self._lock:
            ttl = 60 if self._status and self._status['ready'] else 10
            if not refresh and self._status and time.monotonic() - self._checked < ttl:
                return dict(self._status)
            try:
                result = subprocess.run(self.command('check'), capture_output=True, timeout=40,
                                        encoding='utf-8', errors='replace', **self.process_options())
                data = json.loads(result.stdout)
                if (result.returncode or not isinstance(data, dict) or data.get('type') != 'environment'
                        or data.get('revision') != REVISION or not isinstance(data.get('ready'), bool)):
                    raise ValueError('Invalid Picus environment check response.')
                data.pop('type', None)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                data = {'engine': 'picus', 'revision': REVISION, 'ready': False,
                        'reason': f'Picus is not ready: {error} Run scripts/setup_picus.ps1.'}
            self._status, self._checked = data, time.monotonic()
            return dict(data)

    @staticmethod
    def process_options():
        return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

    def analyze(self, reader, *, cancelled, progress):
        if self.configuration_error:
            raise RuntimeError(self.configuration_error)
        if cancelled():
            return empty_report('cancelled', 'cancelled')
        config = self.config
        command = self.command('run') + ['--file', self.linux_path(reader.path),
                   '--timeout-seconds', str(config.timeout_seconds),
                   '--query-timeout-ms', str(config.query_timeout_ms),
                   '--memory-mib', str(config.memory_mib)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, **self.process_options())
        messages = queue.Queue(maxsize=128)
        diagnostic = []
        invalid = False
        def read(stream, stderr):
            while True:
                line = stream.readline(8 * 1024 * 1024 + 1)
                if not line:
                    break
                messages.put((line.decode('utf-8', errors='replace'), stderr))
            messages.put((None, stderr))
        threads = [threading.Thread(target=read, args=(process.stdout, False), daemon=True),
                   threading.Thread(target=read, args=(process.stderr, True), daemon=True)]
        for thread in threads:
            thread.start()
        started = time.monotonic()
        sent_cancel = False
        timed_out = False
        ended = 0
        report = None
        try:
            while ended < 2 or process.poll() is None:
                elapsed = time.monotonic() - started
                if (cancelled() or elapsed > config.timeout_seconds + 10) and not sent_cancel:
                    timed_out = not cancelled()
                    sent_cancel = True
                    try:
                        process.stdin.write(b'cancel\n')
                        process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        pass
                if elapsed > config.timeout_seconds + 25:
                    raise TimeoutError('The Picus supervisor did not finish cleanup in time.')
                try:
                    line, stderr = messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    ended += 1
                elif stderr:
                    if len(diagnostic) < 50:
                        diagnostic.append(line[:1000])
                else:
                    try:
                        event = json.loads(line)
                        if event['type'] == 'progress':
                            progress({k: event[k] for k in ('message', 'elapsed_seconds')})
                        elif event['type'] == 'result' and report is None:
                            candidate = event['report']
                            if not isinstance(candidate, dict) or candidate.get('engine') != 'picus' or candidate.get('revision') != REVISION or candidate.get('verdict') not in ('safe', 'unsafe', 'unknown', 'error', 'cancelled', 'warning', 'unsatisfiable', 'not_applicable'):
                                raise ValueError('Unexpected Picus report')
                            if not valid_checks(candidate):
                                raise ValueError('Missing or inconsistent R1CS check results')
                            report = candidate
                        else:
                            raise ValueError('Unexpected supervisor event')
                    except (ValueError, KeyError, TypeError):
                        invalid = True
            process.wait(timeout=5)
            if process.returncode or report is None or invalid:
                report = empty_report('error', 'supervisor_error')
                report['logs'] = diagnostic or ['Picus returned an incomplete or invalid response.']
            if cancelled():
                report['verdict'], report['reason'] = 'cancelled', 'cancelled'
            elif timed_out:
                report['verdict'], report['reason'] = 'unknown', 'timeout'
            return report
        finally:
            # EOF tells the Linux supervisor to kill/reap its own process group.
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
