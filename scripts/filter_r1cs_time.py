"""Filter compiled benchmarks by sequential native Picus wall time."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui.picus import PicusConfig, PicusEngine

DIRECTORY = ROOT / 'benchmarks' / 'r1cs'
WORK = ROOT / 'build' / 'r1cs-time-150'


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    original = WORK / 'original_manifest.json'
    if not original.exists():
        save(original, json.loads((DIRECTORY / 'manifest.json').read_text(encoding='utf-8')))
    manifest = json.loads(original.read_text(encoding='utf-8'))
    entries = [r for r in manifest['records'] if r['status'] == 'compiled']
    config = PicusConfig.from_env()
    engine = PicusEngine(PicusConfig(home=config.home, timeout_seconds=3600,
                         query_timeout_ms=config.query_timeout_ms, memory_mib=config.memory_mib))
    status = engine.status()
    if not status['ready']:
        raise RuntimeError(status['reason'])
    results = []
    for index, item in enumerate(entries, 1):
        path = (DIRECTORY / item['r1cs']).resolve()
        assert path.parent == DIRECTORY.resolve() and path.suffix == '.r1cs'
        result_path = WORK / f'{index:03d}.json'
        if result_path.exists():
            record = json.loads(result_path.read_text(encoding='utf-8'))
        else:
            record = {'file': path.name, 'removed': False}
            try:
                reader = R1CSFile(path)
                meta = reader.metadata
                if 1 + meta['public_outputs'] + meta['public_inputs'] + meta['private_inputs'] > meta['wires']:
                    raise ValueError('Input wire mapping is incompatible; recompile with --O0.')
                started = time.monotonic()
                crossed = [False]
                def cancelled():
                    if time.monotonic() - started > 150:
                        crossed[0] = True
                    return crossed[0]
                report = engine.analyze(reader, cancelled=cancelled, progress=lambda data: None)
                elapsed = time.monotonic() - started
                # Runtime/format errors are retained, even if cleanup took time.
                remove = crossed[0] or (elapsed > 150 and report['verdict'] != 'error')
                record.update(elapsed_seconds=elapsed, removed=remove,
                              status='over_limit' if remove else report['verdict'], report=report)
            except Exception as error:
                record.update(status='error', error=str(error))
            save(result_path, record)
        if record['removed'] and path.exists():
            path.unlink()
        results.append(record)
        removed = {r['file'] for r in results if r['removed']}
        current = json.loads(json.dumps(manifest))
        for row in current['records']:
            if row.get('r1cs') in removed:
                row['status'] = 'removed_over_150s'
        current['retained_r1cs'] = len(entries) - len(removed)
        current['removed_over_150s'] = len(removed)
        save(DIRECTORY / 'manifest.json', current)
        save(DIRECTORY / 'timing_results.json', {
            'engine': status, 'threshold_seconds': 150, 'total': len(entries),
            'processed': index, 'complete': index == len(entries),
            'removed': len(removed), 'retained': len(entries) - len(removed),
            'method': 'Sequential Picus five checks; wall time excludes file parsing and environment check; errors retained',
            'records': results})
        print(f"[{index}/{len(entries)}] {record['status']} {path.name} removed={record['removed']}", flush=True)
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
