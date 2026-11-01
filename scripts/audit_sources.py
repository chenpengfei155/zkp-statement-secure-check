"""Audit source entries sequentially, without modifying entries or their includes.

Run with the project Python, for example:
  .venv/Scripts/python.exe scripts/audit_sources.py
Results and progress are written to build/source-audit by default. Re-running
resumes matching reports. Create stop.request in that output directory to cancel
the current child and stop; remove it before resuming.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
INCLUDE = re.compile(rb'\binclude\s+"([^"\r\n]+)"\s*;')


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def implementation_hash():
    paths = [path for path in sorted((ROOT / 'src/cirverify').rglob('*.py'))
             if path.name != 'r1cs.py']
    paths += sorted((ROOT / 'web_ui').glob('source_*.py'))
    paths.append(ROOT / 'web_ui/windows_process.py')
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode('utf-8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def dependency_hash(entry):
    """Conservatively fingerprint literal include paths, including missing ones.

    Commented-out includes can invalidate a cache unnecessarily, but cannot
    cause an outdated result to be reused when a real dependency changes.
    """
    pending, seen, contents = [entry], set(), []
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            data = path.read_bytes()
        except OSError:
            data = None
        contents.append((str(path), data))
        if data is not None:
            pending.extend(path.parent / name.decode('utf-8', errors='replace')
                           for name in INCLUDE.findall(data))
    digest = hashlib.sha256()
    for path, data in sorted(contents):
        digest.update(path.encode('utf-8'))
        digest.update(b'\0missing\0' if data is None else b'\0file\0' + data)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--entries', type=Path, default=ROOT / 'benchmarks/entries')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/source-audit')
    parser.add_argument('--timeout', type=float, default=150)
    parser.add_argument('--retry-incomplete', action='store_true')
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    for dependency in ('antlr4', 'colorama', 'tqdm'):
        try:
            importlib.import_module(dependency)
        except ImportError as error:
            parser.error(f'{error}. Run this command with the installed project Python (.venv).')
    from cirverify.source_report import incomplete_source_report
    from web_ui.source_analysis import analyze_source, _valid_report

    entries = sorted(args.entries.resolve().glob('*.circom'))
    if not entries:
        parser.error('The entries directory contains no .circom files')
    output = args.output.resolve()
    reports_dir = output / 'results'
    reports_dir.mkdir(parents=True, exist_ok=True)
    stop = output / 'stop.request'
    records = {}
    reused = 0

    def progress(state, current=None):
        summary = {'updated_at': now(), 'state': state, 'total': len(entries),
                   'processed': len(records), 'remaining': len(entries) - len(records),
                   'reused': reused, 'current': current, 'timeout_seconds': args.timeout,
                   'counts_by_status': dict(Counter(item['status'] for item in records.values())),
                   'counts_by_reason': dict(Counter(item['reason'] for item in records.values())),
                   'records': list(records.values())}
        write_json(output / 'summary.json', summary)
        write_json(output / 'progress.json', {key: value for key, value in summary.items() if key != 'records'})

    def save_record(entry, report):
        records[entry.name] = {'entry': entry.name, 'status': report['status'],
                               'verdict': report['verdict'], 'reason': report.get('reason', 'completed'),
                               'elapsed_seconds': report.get('elapsed_seconds')}

    for entry in entries:
        if stop.exists():
            progress('stopped')
            return 2
        source_hash = dependency_hash(entry)
        code_hash = implementation_hash()
        path = reports_dir / (entry.name + '.json')
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            cached = None
        audit = cached.get('audit', {}) if isinstance(cached, dict) else {}
        if (_valid_report(cached) and audit.get('dependency_sha256') == source_hash
                and audit.get('code_sha256') == code_hash and audit.get('python') == sys.executable
                and audit.get('timeout_seconds') == args.timeout and not audit.get('changed_during_run')
                and cached['status'] != 'cancelled'
                and not (args.retry_incomplete and cached['status'] != 'completed')):
            reused += 1
            save_record(entry, cached)
            progress('running')
            continue
        started_at, started = now(), time.monotonic()
        progress('running', {'entry': entry.name, 'started_at': started_at})
        try:
            report = analyze_source(entry, filename=entry.name,
                                    timeout_seconds=args.timeout, cancelled=stop.exists)
        except Exception:
            report = incomplete_source_report('audit_runner_error', filename=entry.name,
                       elapsed_seconds=time.monotonic() - started,
                       diagnostics=[traceback.format_exc(limit=8)], stage='audit_runner')
        report['audit'] = {'source_path': str(entry), 'dependency_sha256': source_hash,
                           'code_sha256': code_hash, 'python': sys.executable,
                           'timeout_seconds': args.timeout, 'started_at': started_at, 'finished_at': now(),
                           'changed_during_run': (implementation_hash() != code_hash
                                                  or dependency_hash(entry) != source_hash)}
        write_json(path, report)
        save_record(entry, report)
        progress('running')
        print(json.dumps(records[entry.name], ensure_ascii=True), flush=True)
    progress('complete')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
