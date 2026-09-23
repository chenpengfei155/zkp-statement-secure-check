"""Reproducible route-two experiment; native checks never invoke WSL.

Only --compare-picus invokes the existing WSL engine, as a reference.
Install z3-solver==4.15.4.0 and cvc5==1.3.3 in .venv/native-probe first.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import struct

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'scripts')]
from cirverify.r1cs import R1CSFile
from native_r1cs_z3 import satisfies
from picus_fixtures import BN254, CONSTANT, SQUARE, r1cs

PROPERTIES = ('satisfiability', 'output_uniqueness', 'signal_uniqueness')


def sorted_term_copy(source):
    """Experiment-only copy for our reader's stricter sorted-term requirement.

    Circom's output can contain unsorted sparse combinations. Sorting summands
    changes no coefficient, wire number, equation, section or header field.
    Keep the original compiler artifact beside the copy for inspection.
    """
    data = bytearray(source.read_bytes())
    sections = {}
    cursor = 12
    for _ in range(struct.unpack_from('<I', data, 8)[0]):
        kind, size = struct.unpack_from('<IQ', data, cursor)
        sections[kind] = (cursor + 12, size)
        cursor += 12 + size
    width = struct.unpack_from('<I', data, sections[1][0])[0]
    cursor, size = sections[2]
    end = cursor + size
    changed = 0
    while cursor < end:
        count = struct.unpack_from('<I', data, cursor)[0]
        cursor += 4
        terms = [bytes(data[cursor + i * (width + 4):cursor + (i + 1) * (width + 4)])
                 for i in range(count)]
        ordered = sorted(terms, key=lambda term: int.from_bytes(term[:4], 'little'))
        if terms != ordered:
            changed += 1
        data[cursor:cursor + count * (width + 4)] = b''.join(ordered)
        cursor += count * (width + 4)
    if cursor != end:
        raise ValueError('Invalid generated constraint section.')
    target = source.with_name(source.stem + '.sorted.r1cs')
    target.write_bytes(data)
    target.with_suffix('.normalization.json').write_text(json.dumps({
        'original': str(source.relative_to(ROOT)),
        'original_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'sorted_combinations': changed,
        'note': 'Only summand order changed to satisfy the existing reader; originals preserved.'
    }, indent=2), encoding='utf-8')
    return target


def exhaustive(reader):
    """Small-field oracle: enumerate all assignments, without an SMT solver."""
    rows, p, meta = list(reader.iter_constraints()), reader.prime, reader.metadata
    start = 1 + meta['public_outputs']
    end = start + meta['public_inputs'] + meta['private_inputs']
    groups = {}
    for rest in itertools.product(range(p), repeat=reader.wires - 1):
        values = (1, *rest)
        if satisfies(rows, values, p):
            groups.setdefault(values[start:end], []).append(values)
    if not groups:
        return dict(zip(PROPERTIES, ('fail', 'skipped', 'skipped')))
    output_ambiguous = any(len({v[1:start] for v in group}) > 1 for group in groups.values())
    signal_ambiguous = any(len(group) > 1 for group in groups.values())
    return dict(zip(PROPERTIES, ('pass', 'fail' if output_ambiguous else 'pass',
                                'warning' if signal_ambiguous else 'pass')))


def cases(directory):
    result = [('demo', ROOT / 'demo.r1cs', dict(zip(PROPERTIES, ('pass', 'pass', 'pass')))),
              ('demo1', ROOT / 'demo1.r1cs', dict(zip(PROPERTIES, ('pass', 'pass', 'pass'))))]

    def add(name, rows, expected, **options):
        path = directory / (name + '.r1cs')
        path.write_bytes(r1cs(rows, **options))
        result.append((name, path, dict(zip(PROPERTIES, expected))))

    for prime, suffix in ((17, 'p17'), (BN254, 'bn254')):
        add('ambiguous_output_' + suffix, SQUARE, ('pass', 'fail', 'warning'), prime=prime)
        add('inconsistent_' + suffix, [({1: 1}, {0: 1}, {}), ({1: 1}, {0: 1}, {0: 1})],
            ('fail', 'skipped', 'skipped'), prime=prime)
    add('ambiguous_internal', [({1: 1}, {0: 1}, {2: 1}), ({3: 1}, {3: 1}, {0: 1})],
        ('pass', 'pass', 'warning'), prime=17, wires=4, private=1)
    add('no_constraints', [], ('pass', 'fail', 'warning'), prime=17)
    add('no_outputs', [], ('pass', 'skipped', 'pass'), prime=17, wires=1, outputs=0)
    add('structural_findings', CONSTANT * 2 + [({}, {}, {})], ('pass', 'pass', 'warning'),
        prime=17, wires=3)
    add('public_and_private_inputs', [({2: 1, 3: 1}, {0: 1}, {1: 1})],
        ('pass', 'pass', 'pass'), prime=17, wires=4, public=1, private=1)
    add('modular_wraparound', [({1: 1, 0: 1}, {0: 1}, {})],
        ('pass', 'pass', 'pass'), prime=17, order=(3, 2, 1))
    add('nonlinear_unsatisfiable', [({1: 1}, {1: 1}, {0: 3})],
        ('fail', 'skipped', 'skipped'), prime=17)
    rng = random.Random(20260923)
    for i in range(12):
        rows = [[{w: rng.randrange(5) for w in range(4)} for _ in range(3)]
                for _ in range(i % 3 + 1)]
        path = directory / f'exhaustive_{i:02d}.r1cs'
        path.write_bytes(r1cs(rows, prime=5, wires=4, private=1))
        result.append((path.stem, path, exhaustive(R1CSFile(path))))
    for stem in ('Poseidon@poseidon', 'Num2Bits_strict@bitify'):
        source = ROOT / 'benchmarks/circomlib-cff5ab6' / (stem + '.circom')
        target = directory / (stem + '.r1cs')
        compiler = ROOT / '.tools/circom.exe'
        if compiler.exists() and not target.exists():
            subprocess.run([str(compiler), str(source), '--r1cs', '--O0', '-o', str(directory)],
                           check=True, capture_output=True, timeout=60)
        if target.exists():
            result.append((stem, sorted_term_copy(target), None))
    return result


def environment_probe(python):
    code = '''
import cvc5, json, os, platform, sys, z3
from cvc5 import Kind
result = dict(os=os.name, platform=platform.platform(), python=sys.executable,
              z3=z3.get_version_string(), cvc5=cvc5.__version__)
try:
    tm = cvc5.TermManager()
    s = cvc5.Solver(tm)
    s.setLogic('QF_FF')
    s.setOption('tlimit-per', '5000')
    f = tm.mkFiniteFieldSort('17')
    x = tm.mkConst(f, 'x')
    s.assertFormula(tm.mkTerm(Kind.EQUAL, tm.mkTerm(Kind.FINITE_FIELD_MULT, x, x), tm.mkFiniteFieldElem('3', f)))
    result['cvc5_nonlinear_finite_field'] = str(s.checkSat())
except Exception as error:
    result['cvc5_nonlinear_finite_field'] = str(error)
print(json.dumps(result))
'''
    process = subprocess.run([str(python), '-c', code], capture_output=True, text=True, timeout=15)
    if process.returncode:
        raise RuntimeError(process.stderr)
    return json.loads(process.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-python', type=Path,
                        default=ROOT / '.venv/native-probe/Scripts/python.exe')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/native-r1cs-probe/results.json')
    parser.add_argument('--compare-picus', action='store_true')
    parser.add_argument('--timeout-ms', type=int, default=5000)
    parser.add_argument('--case-seconds', type=float, default=30)
    parser.add_argument('--case', action='append', help='Run only these case names (repeatable).')
    args = parser.parse_args()
    if args.timeout_ms < 1 or args.case_seconds <= 0:
        parser.error('Timeouts must be positive.')
    args.output = args.output.resolve()
    args.native_python = args.native_python.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    report = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'environment': environment_probe(args.native_python),
              'query_timeout_ms': args.timeout_ms, 'case_timeout_seconds': args.case_seconds,
              'timing_note': 'wall_seconds includes process/WSL startup; sequential single runs, not a performance guarantee',
              'cases': []}
    reference = None
    if args.compare_picus:
        from web_ui.picus import PicusConfig, PicusEngine
        reference = PicusEngine(PicusConfig(timeout_seconds=args.case_seconds,
                                            query_timeout_ms=args.timeout_ms))
        report['reference_environment'] = reference.status(refresh=True)
        if not report['reference_environment']['ready']:
            raise RuntimeError(report['reference_environment']['reason'])
    print(json.dumps(report['environment']), flush=True)
    failures = []
    for name, path, expected in cases(args.output.parent):
        if args.case and name not in args.case:
            continue
        entry = {'name': name, 'path': str(path.relative_to(ROOT)),
                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                 'metadata': R1CSFile(path).metadata, 'expected_properties': expected}
        normalization = path.with_suffix('.normalization.json')
        if normalization.exists():
            entry['normalization'] = json.loads(normalization.read_text(encoding='utf-8'))
        started = time.perf_counter()
        try:
            process = subprocess.run([str(args.native_python), str(ROOT / 'scripts/native_r1cs_z3.py'),
                                      str(path), '--timeout-ms', str(args.timeout_ms)],
                                     capture_output=True, text=True, timeout=args.case_seconds, **options)
            native = (json.loads(process.stdout) if process.returncode == 0
                      else {'verdict': 'error', 'error': process.stderr})
        except subprocess.TimeoutExpired:
            native = {'verdict': 'unknown', 'reason': 'hard_timeout', 'checks': []}
        native['wall_seconds'] = round(time.perf_counter() - started, 4)
        entry['native'] = native
        actual = {c['id']: c['status'] for c in native.get('checks', [])}
        entry['expected_comparison'] = ('not_specified' if expected is None else
            'inconclusive' if any(actual.get(k) in (None, 'unknown') for k in expected) else
            'match' if all(actual.get(k) == v for k, v in expected.items()) else 'mismatch')
        if native['verdict'] == 'error' or entry['expected_comparison'] == 'mismatch':
            failures.append(name)
        # Exhaustive cases use an independent mathematical oracle, not Picus.
        if reference and not name.startswith('exhaustive_'):
            before = time.perf_counter()
            entry['picus'] = reference.analyze(R1CSFile(path), cancelled=lambda: False, progress=lambda _: None)
            entry['picus']['wall_seconds'] = round(time.perf_counter() - before, 4)
            ref_checks = {c['id']: c['status'] for c in entry['picus']['checks']}
            entry['same_six_statuses'] = actual == ref_checks
        report['cases'].append(entry)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f"{name}: native={native['verdict']} ({native['wall_seconds']:.2f}s), "
              f"Picus={entry.get('picus', {}).get('verdict', '-')}, "
              f"oracle={entry['expected_comparison']}", flush=True)
    report['incorrect_or_error_cases'] = failures
    report['summary'] = {
        'total_cases': len(report['cases']),
        'expected_matches': sum(c['expected_comparison'] == 'match' for c in report['cases']),
        'native_unknown_cases': [c['name'] for c in report['cases'] if c['native']['verdict'] == 'unknown'],
        'picus_compared_cases': sum('picus' in c for c in report['cases']),
        'six_status_matches': sum(c.get('same_six_statuses', False) for c in report['cases']),
    }
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Report: {args.output}\nIncorrect/error cases: {failures}', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
