"""Experimental native R1CS checker; deliberately not wired into the web app.

Exact encoding: every wire is an integer in [0, p), w0 = 1, and each
A*B-C is zero modulo p. Uniqueness uses two copies sharing ALL input wires.
This implements the properties, not Picus's inference/optimization algorithm.
Run in a dedicated environment with z3-solver==4.15.4.0 installed.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui.r1cs_checks import CHECK_NAMES, aggregate, check_result, prepare_checks


def satisfies(rows, values, prime):
    """Independently evaluate the ORIGINAL R1CS, using only Python integers."""
    if values[0] != 1 or any(not 0 <= v < prime for v in values):
        return False
    for row in rows:
        a, b, c = (sum(int(t['coefficient']) * values[t['wire']] for t in row[k])
                   for k in ('a', 'b', 'c'))
        if (a * b - c) % prime:
            return False
    return True


def analyze(path, timeout_ms=5000):
    import z3

    started = time.perf_counter()
    reader = R1CSFile(path)
    p, meta = reader.prime, reader.metadata
    input_start = 1 + meta['public_outputs']
    input_end = input_start + meta['public_inputs'] + meta['private_inputs']
    if input_end > reader.wires:
        raise ValueError('Input counts do not map to live wires; recompile with --O0.')
    inputs = list(range(input_start, input_end))
    outputs = list(range(1, input_start))
    targets = [w for w in range(1, reader.wires) if w not in inputs]
    rows = list(reader.iter_constraints())
    with tempfile.TemporaryDirectory(prefix='native_r1cs_scan_') as directory:
        structural = prepare_checks(reader, Path(directory) / 'unused-query.smt2')
    checks = {}
    for item in structural:
        checks[item['id']] = item

    def make_wires(prefix, shared=None):
        return [z3.IntVal(1)] + [shared[w] if shared is not None and w in inputs
                                else z3.Int(f'{prefix}{w}')
                                for w in range(1, reader.wires)]

    def system(wires):
        terms = [bound for w in wires[1:] for bound in (w >= 0, w < p)]
        for row in rows:
            parts = []
            for key in ('a', 'b', 'c'):
                # Signed representatives are algebraically equivalent modulo p.
                parts.append(z3.Sum([((int(t['coefficient']) + p // 2) % p - p // 2)
                                     * wires[t['wire']] for t in row[key]]))
            a, b, c = parts
            terms.append((a * b - c) % p == 0)
        return terms

    first = make_wires('u')
    solver = z3.Solver()
    solver.set(timeout=timeout_ms, random_seed=0)
    solver.add(*system(first))

    def solve():
        before = time.perf_counter()
        result = solver.check()
        duration = round(time.perf_counter() - before, 4)
        return result, duration, solver.reason_unknown() if result == z3.unknown else 'completed'

    def assignment(wires, model):
        values = [model.eval(w, model_completion=True).as_long() for w in wires]
        if not satisfies(rows, values, p):
            raise AssertionError('Solver assignment failed independent R1CS evaluation.')
        return values

    result, elapsed, reason = solve()
    sat_status = 'pass' if result == z3.sat else 'fail' if result == z3.unsat else 'unknown'
    sat_details = {}
    if result == z3.sat:
        sat_details = {'witness': [str(v) for v in assignment(first, solver.model())],
                       'witness_validated': True}
    checks['satisfiability'] = check_result(
        'satisfiability', sat_status, str(result), reason=reason,
        elapsed_seconds=elapsed, **sat_details)
    if result != z3.unsat:
        second = make_wires('v', shared=first)
        solver.add(*system(second))
    for identifier, selected in (('output_uniqueness', outputs), ('signal_uniqueness', targets)):
        if identifier == 'output_uniqueness' and not outputs:
            checks[identifier] = check_result(identifier, 'skipped', 'No public outputs.', reason='no_outputs')
            continue
        if sat_status == 'fail':
            checks[identifier] = check_result(identifier, 'skipped', 'Constraints have no solution.', reason='unsatisfiable')
            continue
        solver.push()
        solver.add(z3.Or([first[w] != second[w] for w in selected]))
        answer, elapsed, reason = solve()
        status = ('pass' if answer == z3.unsat else 'unknown' if answer == z3.unknown
                  else 'fail' if identifier == 'output_uniqueness' else 'warning')
        details = {}
        if answer == z3.sat:
            model = solver.model()
            u, v = assignment(first, model), assignment(second, model)
            if any(u[w] != v[w] for w in inputs) or not any(u[w] != v[w] for w in selected):
                raise AssertionError('Counterexample failed input equality or target inequality.')
            details = {'counterexample_validated': True,
                       'counterexample': {'first': [str(x) for x in u], 'second': [str(x) for x in v]}}
        checks[identifier] = check_result(identifier, status, str(answer), reason=reason,
                                          elapsed_seconds=elapsed, **details)
        solver.pop()
    ordered = [checks[key] for key in CHECK_NAMES]
    verdict, reason = aggregate(ordered, no_outputs=not outputs)
    return {'engine': 'native-z3-prototype', 'solver_version': z3.get_version_string(),
            'os': os.name, 'python': sys.executable, 'metadata': meta,
            'query_timeout_ms': timeout_ms, 'checks': ordered, 'verdict': verdict,
            'reason': reason, 'elapsed_seconds': round(time.perf_counter() - started, 4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--timeout-ms', type=int, default=5000)
    args = parser.parse_args()
    if args.timeout_ms < 1:
        parser.error('--timeout-ms must be positive')
    print(json.dumps(analyze(args.file, args.timeout_ms), ensure_ascii=True))


if __name__ == '__main__':
    main()
