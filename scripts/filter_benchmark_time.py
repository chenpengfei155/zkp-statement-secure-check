"""Measure source detection sequentially and prune only measured >150s entries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ROOT / 'benchmarks' / 'entries'
RESULTS = ROOT / 'build' / 'benchmark-time-150'
LIMIT = 150


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def stop_worker(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        process.kill()
    process.wait()


def publish(records, total, complete=False):
    removed = sum(r['removed'] for r in records)
    save(ENTRIES / 'timing_results.json', {
        'threshold_seconds': LIMIT,
        'method': 'Sequential local CirVerify detect + print_reports, monotonic wall time; startup excluded',
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'complete': complete, 'total': total, 'processed': len(records),
        'removed': removed, 'remaining_entries': total - removed,
        'failed': sum(r['status'] == 'failed' for r in records), 'records': records,
    })
    if complete:
        readme = ROOT / 'benchmarks' / 'README.md'
        text = readme.read_text(encoding='utf-8')
        text = text.replace(
            '[`entries/`](entries/) 直接包含全部 **258 个待检测的 `.circom` 入口**，批量检测时使用此目录。',
            f'[`entries/`](entries/) 原有 258 个入口，按本机检测耗时筛选后保留 **{total - removed} 个 `.circom` 入口**，'
            f'已移除 {removed} 个耗时超过 150 秒的入口。批量检测时使用此目录。')
        text = text.replace(
            '`entries/` 是同一批样本的集中副本，不应与下表原始目录重复计数。',
            '`entries/` 是原始样本经耗时筛选后的集中副本，不应与下表原始目录重复计数。\n\n'
            '逐项耗时、超时删除记录和检测失败信息见 [`entries/timing_results.json`](entries/timing_results.json)。\n'
            '检测失败的入口保留；超时仅表示本机运行超过 150 秒，不代表电路有漏洞。')
        readme.write_text(text, encoding='utf-8')


def worker(entry, output):
    base = ROOT / 'src' / 'cirverify'
    sys.path.insert(0, str(ROOT / 'src'))
    for sub in ['parser', 'astgen', 'typecheck', 'utils', 'cdggen', 'detect', 'target']:
        sys.path.insert(0, str(base / sub))
    from cirverify.core import detect, print_reports
    started = time.monotonic()
    save(output.with_suffix('.started.json'), {'started': started})
    try:
        graphs, reports = detect(str(entry))
        if graphs and reports:
            print_reports(graphs, reports)
            status = 'completed'
        else:
            status = 'failed'
        result = {'status': status, 'elapsed_seconds': time.monotonic() - started}
    except Exception:
        result = {'status': 'failed', 'elapsed_seconds': time.monotonic() - started,
                  'error': traceback.format_exc()}
    save(output, result)


def main():
    RESULTS.mkdir(parents=True, exist_ok=True)
    manifest_path = ENTRIES / 'manifest.json'
    original_path = RESULTS / 'original_manifest.json'
    if not original_path.exists():
        save(original_path, json.loads(manifest_path.read_text(encoding='utf-8')))
    original = json.loads(original_path.read_text(encoding='utf-8'))
    records = []
    for index, item in enumerate(original, 1):
        entry = (ENTRIES / item['entry']).resolve()
        assert entry.parent == ENTRIES.resolve() and entry.suffix == '.circom'
        output = RESULTS / f'{index:03d}.json'
        started_path = output.with_suffix('.started.json')
        if not output.exists():
            assert entry.is_file(), entry
            if started_path.exists():
                started_path.unlink()
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                        '--worker', str(entry), str(output)], cwd=ROOT,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            launched = time.monotonic()
            while process.poll() is None:
                if started_path.exists():
                    started = json.loads(started_path.read_text(encoding='utf-8'))['started']
                    if time.monotonic() - started > LIMIT and not output.exists():
                        stop_worker(process)
                        if not output.exists():
                            save(output, {'status': 'timeout', 'elapsed_seconds': time.monotonic() - started})
                        break
                elif time.monotonic() - launched > 30:
                    stop_worker(process)
                    save(output, {'status': 'failed', 'error': 'Worker initialization exceeded 30 seconds'})
                    break
                time.sleep(0.1)
            if not output.exists():
                save(output, {'status': 'failed', 'error': f'Worker exited with code {process.returncode}'})
        result = json.loads(output.read_text(encoding='utf-8'))
        remove = result['status'] in ('timeout', 'completed') and result.get('elapsed_seconds', 0) > LIMIT
        record = {**item, **result, 'removed': remove}
        records.append(record)
        save(RESULTS / 'results.json', records)
        if remove and entry.exists():
            entry.unlink()
        removed = {r['entry'] for r in records if r['removed']}
        save(manifest_path, [i for i in original if i['entry'] not in removed])
        publish(records, len(original))
        print(f"[{index}/{len(original)}] {result['status']} {result.get('elapsed_seconds', 0):.2f}s {item['entry']} removed={remove}", flush=True)
    publish(records, len(original), complete=True)
    print(f"DONE: removed {sum(r['removed'] for r in records)}, kept {sum(not r['removed'] for r in records)}", flush=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        main()
