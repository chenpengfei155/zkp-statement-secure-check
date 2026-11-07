"""Audit every .r1cs file sequentially with the native Windows analysis engine.

  .venv/Scripts/python.exe scripts/audit_r1cs.py

Results/progress default to build/r1cs-audit. Matching reports are reused;
--retry-incomplete reruns unresolved reports. Create stop.request in the output
directory to cancel and stop, then remove it before resuming. Superseded reports
and individual worker attempts are retained. Exit 0 means every file was
attempted, not that every check was conclusive; exit 2 means stopped.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
SCHEMA = 1


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


class Stopped(Exception):
    pass


def file_hash(path, cancelled=lambda: False):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            if cancelled():
                raise Stopped()
            block = stream.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def implementation_hash():
    paths = {ROOT / 'src/cirverify/r1cs.py', ROOT / 'web_ui/windows_process.py',
             Path(__file__).resolve()}
    paths.update((ROOT / 'web_ui').glob('picus*.py'))
    paths.update((ROOT / 'web_ui').glob('r1cs*.py'))
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode('utf-8') + b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_hash(home, cancelled=lambda: False):
    """Hash the manifest and actual declared artifacts, including missing files.

    This fingerprint does not replace the engine's runtime integrity validation.
    """
    from web_ui.picus_runtime import relative_file
    home = Path(home).expanduser().resolve()
    manifest_path = home / 'runtime.json'
    manifest = read_json(manifest_path)
    paths = {manifest_path, home / 'picus.exe', home / 'cvc5.exe'}
    if isinstance(manifest, dict) and isinstance(manifest.get('files'), dict):
        for name in manifest['files']:
            try:
                path = (home / relative_file(name)).resolve()
                if path.is_relative_to(home):
                    paths.add(path)
            except (ValueError, TypeError):
                pass  # The native runtime validator reports malformed manifests.
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path).encode('utf-8') + b'\0')
        try:
            digest.update(file_hash(path, cancelled).encode('ascii'))
        except OSError as error:
            digest.update(('missing:' + type(error).__name__).encode('ascii'))
    return digest.hexdigest()


def report_status(report):
    from web_ui.picus import valid_checks
    if not isinstance(report, dict) or not valid_checks(report):
        return 'incomplete'
    if report.get('verdict') == 'cancelled':
        return 'cancelled'
    for check in report['checks']:
        if (check.get('complete') is False
                or check.get('reason') in ('scan_limit', 'not_started')
                or check['status'] in ('unknown', 'error', 'cancelled')
                or (check['status'] == 'skipped'
                    and check.get('reason') not in ('no_outputs', 'unsatisfiable'))):
            return 'incomplete'
    return 'completed'


def failed_record(reason, diagnostic='', stage='audit_worker', cancelled=False):
    from web_ui.picus import empty_report
    report = empty_report('cancelled' if cancelled else 'unknown', reason)
    if diagnostic:
        report['logs'] = [diagnostic]
    return {'status': report_status(report), 'reason': reason, 'stage': stage, 'report': report}


def worker(request_path):
    from cirverify.r1cs import R1CSFile
    from web_ui.picus import PicusConfig, PicusEngine
    request = read_json(request_path)
    attempt = request_path.parent
    path, stop = Path(request['file']), Path(request['stop'])
    started = time.monotonic()
    record = {'stage': 'validation', 'status': 'incomplete'}

    def checkpoint():
        if stop.exists():
            raise Stopped()
        if time.monotonic() - started > request['worker_timeout_seconds']:
            raise TimeoutError('Full validation and analysis exceeded the worker budget.')

    def progress(event):
        write_json(attempt / 'progress.json', {'updated_at': now(), **event})

    write_json(attempt / 'stage.json', record)
    try:
        reader = R1CSFile(path, checkpoint=checkpoint)
        record.update(metadata=reader.metadata, stage='analysis',
                      validation_seconds=round(time.monotonic() - started, 4))
        write_json(attempt / 'stage.json', record)
        config = PicusConfig(**request['config'])
        report = PicusEngine(config).analyze(reader, cancelled=stop.exists, progress=progress)
        record.update(report=report, status=report_status(report), reason=report['reason'], stage='finished')
    except Stopped:
        record.update(failed_record('cancelled', stage=record['stage'], cancelled=True))
    except TimeoutError as error:
        record.update(failed_record('audit_timeout', str(error), record['stage']))
    except MemoryError:
        record.update(failed_record('memory_limit', 'Worker exhausted its memory limit.', record['stage']))
    except Exception:
        record.update(failed_record('validation_error' if record['stage'] == 'validation' else 'audit_worker_error',
                                    traceback.format_exc(limit=8), record['stage']))
    record['elapsed_seconds'] = round(time.monotonic() - started, 4)
    write_json(attempt / 'result.json', record)


def run_file(path, attempt, stop, config, worker_timeout):
    from web_ui.windows_process import ProcessJob
    request = {'file': str(path), 'stop': str(stop), 'config': config,
               'worker_timeout_seconds': worker_timeout}
    write_json(attempt / 'request.json', request)
    job, process = None, None
    started, stop_at = time.monotonic(), None
    try:
        job = ProcessJob(memory_mib=config['memory_mib'])
        process = job.spawn([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                             '--worker', str(attempt / 'request.json')],
                            cwd=str(ROOT), env=dict(os.environ, PYTHONUTF8='1'))
        while True:
            if stop.exists() and stop_at is None:
                stop_at = time.monotonic()
            if stop_at is not None and time.monotonic() - stop_at > 15:
                return failed_record('cancelled', 'Stopped by stop.request.', cancelled=True)
            if time.monotonic() - started > worker_timeout + 15:
                return failed_record('audit_timeout', 'Outer worker watchdog expired.')
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        (attempt / 'stdout.log').write_bytes(stdout)
        (attempt / 'stderr.log').write_bytes(stderr)
        result = read_json(attempt / 'result.json')
        if process.returncode or not isinstance(result, dict) or not isinstance(result.get('report'), dict):
            result = failed_record('audit_worker_error',
                f'Worker exit {process.returncode}: ' + stderr.decode('utf-8', errors='replace')[-4000:])
        result['status'] = report_status(result.get('report'))
        return result
    except Exception:
        return failed_record('audit_runner_error', traceback.format_exc(limit=8))
    finally:
        if job is not None:
            job.close()
        if process is not None:
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def main(argv=None):
    from web_ui.picus import PicusConfig, valid_checks
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--r1cs', type=Path, default=ROOT / 'benchmarks/r1cs')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/r1cs-audit')
    parser.add_argument('--timeout', type=float, default=PicusConfig().timeout_seconds,
                        help='Analysis budget ceiling (capped at 195s; ordinary tasks randomize within the last 15s).')
    parser.add_argument('--worker-timeout', type=float, default=420, help='Validation plus analysis budget (seconds).')
    parser.add_argument('--query-timeout-ms', type=int, default=5000)
    parser.add_argument('--memory-mib', type=int, default=4096, help='Windows limit per process, not aggregate job memory.')
    parser.add_argument('--home', default=PicusConfig().home)
    parser.add_argument('--retry-incomplete', action='store_true')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        worker(args.worker)
        return 0
    if not math.isfinite(args.worker_timeout) or args.worker_timeout <= 0:
        parser.error('--worker-timeout must be finite and positive')
    config = {'home': str(Path(args.home).expanduser().resolve()), 'timeout_seconds': args.timeout,
              'query_timeout_ms': args.query_timeout_ms, 'memory_mib': args.memory_mib}
    try:
        PicusConfig(**config)
    except ValueError as error:
        parser.error(str(error))
    paths = sorted(args.r1cs.resolve().glob('*.r1cs'), key=lambda path: (path.stat().st_size, path.name))
    if not paths:
        parser.error('The R1CS directory contains no .r1cs files')
    output = args.output.resolve()
    reports = output / 'results'
    reports.mkdir(parents=True, exist_ok=True)
    stop = output / 'stop.request'
    records, reused = [], 0

    def progress(state, current=None):
        summary = {'schema': SCHEMA, 'updated_at': now(), 'state': state, 'total': len(paths),
                   'processed': len(records), 'remaining': len(paths) - len(records), 'reused': reused,
                   'current': current, 'config': config, 'worker_timeout_seconds': args.worker_timeout,
                   'memory_limit_scope': 'per_process',
                   'all_checks_complete': len(records) == len(paths) and all(r['status'] == 'completed' for r in records),
                   'counts_by_status': dict(Counter(r['status'] for r in records)),
                   'counts_by_reason': dict(Counter(r['reason'] for r in records)), 'records': records}
        write_json(output / 'summary.json', summary)
        write_json(output / 'progress.json', {key: value for key, value in summary.items() if key != 'records'})

    def fingerprint(path):
        return {'schema': SCHEMA, 'source_path': str(path), 'input_sha256': file_hash(path, stop.exists),
                'code_sha256': implementation_hash(), 'runtime_sha256': runtime_hash(config['home'], stop.exists),
                'python': sys.executable, 'python_version': platform.python_version(), 'config': config,
                'worker_timeout_seconds': args.worker_timeout}

    for path in paths:
        if stop.exists():
            progress('stopped')
            return 2
        progress('running', {'file': path.name, 'stage': 'fingerprinting'})
        try:
            identity = fingerprint(path)
        except Stopped:
            progress('stopped')
            return 2
        report_path = reports / (path.name + '.json')
        cached = read_json(report_path)
        audit = cached.get('audit', {}) if isinstance(cached, dict) else {}
        if (isinstance(cached, dict) and isinstance(cached.get('report'), dict)
                and valid_checks(cached['report'])
                and audit.get('identity') == identity and not audit.get('changed_during_run')
                and cached.get('status') == report_status(cached.get('report'))
                and cached['status'] != 'cancelled'
                and not (args.retry_incomplete and cached['status'] != 'completed')):
            result = cached
            reused += 1
        else:
            token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid.uuid4().hex[:8]
            attempt = output / 'attempts' / (path.name + '.' + token)
            attempt.mkdir(parents=True)
            if report_path.exists():
                shutil.copy2(report_path, attempt / 'previous-report.json')
            started_at, started = now(), time.monotonic()
            progress('running', {'file': path.name, 'stage': 'analysis', 'started_at': started_at,
                                 'attempt_path': str(attempt)})
            result = run_file(path, attempt, stop, config, args.worker_timeout)
            try:
                changed = fingerprint(path) != identity
            except (Stopped, OSError):
                changed = True
            result.update(file=path.name, size_bytes=path.stat().st_size,
                          elapsed_seconds=round(time.monotonic() - started, 4))
            result['audit'] = {'identity': identity, 'started_at': started_at, 'finished_at': now(),
                               'changed_during_run': changed, 'attempt_path': str(attempt)}
            if changed and result['status'] == 'completed':
                result['status'], result['reason'] = 'incomplete', 'changed_during_run'
            write_json(report_path, result)
        record = {key: result.get(key) for key in ('file', 'status', 'reason', 'elapsed_seconds')}
        record.update(verdict=result['report'].get('verdict'), report_path=str(report_path))
        records.append(record)
        progress('running')
        print(json.dumps(record, ensure_ascii=True), flush=True)
    progress('stopped' if stop.exists() else 'complete')
    return 2 if stop.exists() else 0


if __name__ == '__main__':
    raise SystemExit(main())
