"""Compile the retained benchmark entries, preserving a per-file audit trail."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks' / 'entries'
OUTPUT = ROOT / 'benchmarks' / 'r1cs'
LOGS = ROOT / 'build' / 'benchmark-r1cs'
COMPILER = ROOT / '.tools' / 'circom.exe'


def main():
    OUTPUT.mkdir(exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    entries = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))
    assert len(entries) == len(list(SOURCE.glob('*.circom')))
    version = subprocess.check_output([str(COMPILER), '--version'], text=True).strip()
    results = []
    for index, item in enumerate(entries, 1):
        source = SOURCE / item['entry']
        target = OUTPUT / (source.stem + '.r1cs')
        log = LOGS / (source.stem + '.log')
        command = [str(COMPILER), str(source), '--r1cs', '--O1', '-o', str(OUTPUT)]
        started = time.monotonic()
        with log.open('w', encoding='utf-8') as stream:
            completed = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        valid = False
        if completed.returncode == 0 and target.is_file():
            with target.open('rb') as stream:
                valid = stream.read(4) == b'r1cs'
        record = {**item, 'r1cs': target.name if valid else None,
                  'status': 'compiled' if valid else 'failed',
                  'exit_code': completed.returncode,
                  'elapsed_seconds': round(time.monotonic() - started, 3),
                  'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                  'log': log.relative_to(ROOT).as_posix()}
        results.append(record)
        report = {'compiler': version, 'flags': ['--r1cs', '--O1'],
                  'total': len(entries), 'processed': len(results),
                  'complete': index == len(entries),
                  'compiled': sum(r['status'] == 'compiled' for r in results),
                  'failed': sum(r['status'] == 'failed' for r in results), 'records': results}
        temporary = OUTPUT / 'manifest.tmp'
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        temporary.replace(OUTPUT / 'manifest.json')
        print(f"[{index}/{len(entries)}] {record['status']} {record['elapsed_seconds']}s {source.name}", flush=True)
    print(f"DONE: {report['compiled']} compiled, {report['failed']} failed", flush=True)


if __name__ == '__main__':
    main()
