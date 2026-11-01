"""Bound each source analysis so malformed or expensive files cannot stall Flask."""

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from cirverify.source_report import SOURCE_CHECKS, incomplete_source_report

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name('source_worker.py')


def _valid_report(report):
    if not isinstance(report, dict) or report.get('kind') != 'circom':
        return False
    if report.get('status') not in ('completed', 'incomplete', 'cancelled', 'not_applicable'):
        return False
    elapsed = report.get('elapsed_seconds')
    if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
            or not isinstance(report.get('filename'), (str, type(None)))):
        return False
    if not isinstance(report.get('diagnostics', []), list) or not all(
            isinstance(line, str) for line in report.get('diagnostics', [])):
        return False
    checks = report.get('checks')
    if not isinstance(checks, list) or len(checks) != len(SOURCE_CHECKS):
        return False
    if [check.get('id') for check in checks if isinstance(check, dict)] != [key for key, _ in SOURCE_CHECKS]:
        return False
    for check in checks:
        if not isinstance(check.get('name'), str) or not isinstance(check.get('message'), str):
            return False
        findings = check.get('findings')
        if not isinstance(findings, list) or not all(
                isinstance(item, dict) and isinstance(item.get('location'), str)
                and isinstance(item.get('message'), str) for item in findings):
            return False
        status, count = check.get('status'), check.get('count')
        if status in ('pass', 'warning'):
            if type(count) is not int or count < 0 or count != len(findings):
                return False
            if (status == 'pass') != (count == 0):
                return False
        elif status in ('unknown', 'skipped'):
            if count is not None:
                return False
        else:
            return False
    if report['status'] == 'completed':
        if not all(check['status'] in ('pass', 'warning') for check in checks):
            return False
        total = sum(check['count'] for check in checks)
        return (type(report.get('warning_count')) is int and report['warning_count'] == total
                and report.get('verdict') == ('warning' if total else 'safe'))
    expected_verdict = {'incomplete': 'unknown', 'cancelled': 'cancelled',
                        'not_applicable': 'not_applicable'}[report['status']]
    return (report.get('verdict') == expected_verdict and report.get('warning_count') is None
            and isinstance(report.get('reason'), str) and bool(report['reason'])
            and isinstance(report.get('message'), str) and bool(report['message'])
            and any(check['status'] in ('unknown', 'skipped') for check in checks))


def analyze_source(file_path, *, filename=None, timeout_seconds=150, cancelled=None):
    """Return a report even when a child times out, crashes, or cannot be started."""
    started = time.monotonic()
    job = process = None
    result = None
    directory = None
    def incomplete(reason, diagnostics=None):
        return incomplete_source_report(reason, filename=filename,
                                        elapsed_seconds=time.monotonic() - started,
                                        diagnostics=diagnostics, stage='execution')
    try:
        timeout_seconds = float(timeout_seconds)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            return incomplete('configuration_error', ['Source timeout must be a finite positive number of seconds.'])
        if cancelled and cancelled():
            return incomplete('cancelled')
        source = Path(file_path).resolve(strict=True)
        directory = tempfile.TemporaryDirectory(prefix='cirverify_source_')
        output = Path(directory.name) / 'result.json'
        command = [sys.executable, '-X', 'utf8', str(WORKER), str(source), str(output)]
        if filename is not None:
            command.extend(['--filename', str(filename)])
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        if os.name == 'nt':
            from web_ui.windows_process import ProcessJob
            job = ProcessJob(memory_mib=4096)
            process = job.spawn(command, cwd=str(ROOT), env=env)
        else:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
        while True:
            if cancelled and cancelled():
                result = incomplete('cancelled')
                break
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                result = incomplete('timeout', [f'Time limit: {timeout_seconds:g} seconds.'])
                break
            try:
                stdout, stderr = process.communicate(timeout=min(remaining, 0.1))
                break
            except subprocess.TimeoutExpired:
                continue
        if result is None:
            if process.returncode != 0 or not output.is_file():
                detail = stderr.decode('utf-8', errors='replace')[-8000:]
                result = incomplete('worker_error', [f'Worker exit code: {process.returncode}', detail])
            elif output.stat().st_size > 64 * 1024 * 1024:
                result = incomplete('worker_error', ['The worker result exceeded the 64 MiB report limit.'])
            else:
                candidate = json.loads(output.read_text(encoding='utf-8'))
                result = candidate if _valid_report(candidate) else incomplete('worker_error', ['Invalid worker result.'])
                result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    except Exception as error:
        result = incomplete('worker_error', [f'{type(error).__name__}: {error}'])
    finally:
        try:
            if job is not None:
                job.close()
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process is not None:
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
        except Exception as error:
            result = incomplete('worker_error', [f'Could not clean up analysis process: {error}'])
        finally:
            if directory is not None:
                try:
                    directory.cleanup()
                except OSError as error:
                    result = incomplete('worker_error', [f'Could not clean up analysis temporary files: {error}'])
    return result
