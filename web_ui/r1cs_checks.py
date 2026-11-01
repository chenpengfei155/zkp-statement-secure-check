"""Bounded structural checks and an exact finite-field satisfiability query.

These checks describe the compiled equations, not the original source program.
All indices in reports are zero-based, as in the constraint viewer API.
"""
import time
from contextlib import nullcontext
from web_ui.r1cs_witness import known_prime as _known_prime


CHECK_NAMES = {
    'satisfiability': 'Constraint Satisfiability',
    'output_uniqueness': 'Output Uniqueness',
    'signal_uniqueness': 'All-Signal Uniqueness (including internal signals)',
    'unused_wires': 'Unused Wires',
    'trivial_constraints': 'Trivial Constraints',
}


def check_result(identifier, status, message, **details):
    return dict(id=identifier, name=CHECK_NAMES[identifier], status=status,
                message=message, **details)


def verified_pair_uniqueness(metadata, witness, elapsed_seconds=0):
    """Deduce non-uniqueness from two fully verified assignments, never a pass.

    Only the internal witness constructor supplies this object. Both complete
    assignments must have passed its fresh scan of the original constraints.
    """
    first, second = witness.get('values'), witness.get('alternative_values')
    outputs, wires = metadata['public_outputs'], metadata['wires']
    input_end = 1 + outputs + metadata['public_inputs'] + metadata['private_inputs']
    prime = int(metadata['prime'])
    if (input_end > wires or witness.get('constraints_verified') != metadata['constraints']
            or not isinstance(first, list) or not isinstance(second, list)
            or len(first) != wires or len(second) != wires
            or first[0] != 1 or second[0] != 1
            or any(type(value) is not int or not 0 <= value < prime for values in (first, second) for value in values)
            or first[1 + outputs:input_end] != second[1 + outputs:input_end]):
        return []
    different_outputs = [wire for wire in range(1, 1 + outputs) if first[wire] != second[wire]]
    different_internal = [wire for wire in range(input_end, wires) if first[wire] != second[wire]]
    if not different_outputs and not different_internal:
        return []
    def differences(indices):
        return [{'wire': wire, 'first': str(first[wire]), 'second': str(second[wire])} for wire in indices[:500]]
    counterexample = {
        'inputs': [{'wire': wire, 'value': str(first[wire])} for wire in range(1 + outputs, min(input_end, 501 + outputs))],
        'outputs': differences(different_outputs), 'internal': differences(different_internal),
        'truncated': max(len(different_outputs), len(different_internal), input_end - 1 - outputs) > 500,
    }
    details = dict(method='verified_witness_pair', reason='verified_counterexample', verdict='unsafe',
                   counterexample=counterexample, elapsed_seconds=round(elapsed_seconds, 2),
                   constraints_verified=metadata['constraints'])
    checks = []
    if different_outputs:
        checks.append(check_result('output_uniqueness', 'fail',
            'Two fully verified assignments have identical inputs and different outputs.', **details))
    checks.append(check_result('signal_uniqueness', 'warning',
        'Two fully verified assignments have identical inputs and different output or internal values.', **details))
    return checks


def polynomial(a, b, c, prime):
    """Expand A*B-C; wire zero is the constant 1, not a free variable."""
    result = {}
    for x, left in a.items():
        for y, right in b.items():
            key = tuple(sorted(w for w in (x, y) if w))
            result[key] = (result.get(key, 0) + left * right) % prime
    for wire, value in c.items():
        key = (wire,) if wire else ()
        result[key] = (result.get(key, 0) - value) % prime
    return {key: value for key, value in result.items() if value}


def _prime_field_structure(a, b, c, prime):
    """Exact effective variables and identity test in linear time over a field.

    If both factors have variable terms, every factor variable appears in a
    nonzero square or cross term; subtracting linear C cannot remove it.
    Otherwise the entire constraint is linear and can be normalized directly.
    """
    if any(a) and any(b):
        return (a.keys() | b.keys() | c.keys()) - {0}, False
    factor, part = (a.get(0, 0), b) if not any(a) else (b.get(0, 0), a)
    remaining = {wire for wire in part.keys() | c.keys()
                 if (factor * part.get(wire, 0) - c.get(wire, 0)) % prime}
    return remaining - {0}, not remaining


def _combination(terms, prime, checkpoint):
    parts = []
    for i, (wire, coefficient) in enumerate(terms.items()):
        if i % 256 == 0:
            checkpoint()
        value = f'#f{coefficient}m{prime}'
        parts.append(value if wire == 0 else f'(ff.mul {value} w{wire})')
    return (f'(ff.add {" ".join(parts)})' if len(parts) > 1
            else parts[0] if parts else f'#f0m{prime}')


def _declarations(query, reader, checkpoint):
    query.write(f'(set-logic QF_FF)\n(define-sort F () (_ FiniteField {reader.prime}))\n')
    for wire in range(1, reader.wires):
        if wire % 256 == 0:
            checkpoint()
        query.write(f'(declare-fun w{wire} () F)\n')


def _terms(row):
    return tuple({term['wire']: int(term['coefficient']) for term in row[key]
                  if int(term['coefficient'])} for key in ('a', 'b', 'c'))


def _assertion(query, parts, prime, checkpoint):
    a, b, c = (_combination(part, prime, checkpoint) for part in parts)
    query.write(f'(assert (= (ff.mul {a} {b}) {c}))\n')


def write_satisfiability_query(reader, query_path, checkpoint=lambda: None):
    """Generate the unrestricted solver query only if witness checks need it."""
    with open(query_path, 'w', encoding='ascii') as query:
        _declarations(query, reader, checkpoint)
        for row in reader.iter_constraints():
            checkpoint()
            _assertion(query, _terms(row), reader.prime, checkpoint)
        query.write('(check-sat)\n')


def prepare_checks(reader, query_path=None, checkpoint=lambda: None, *,
                   expansion_limit=1_000_000, row_limit=20_000):
    """Scan constraints, verify a candidate assignment, and prepare the SMT fallback.

    A failed candidate says nothing about satisfiability. Structural expansion
    limits never skip the exact assignment check on an individual constraint.
    """
    started = time.monotonic()
    p = reader.prime
    used = bytearray(reader.wires)
    findings = {'trivial_constraints': []}
    counts = {key: 0 for key in findings}
    normalized = 0
    spent = 0
    zero_assignment_valid = True
    constraints_checked = 0
    prime_field = _known_prime(p)

    def record(identifier, item):
        counts[identifier] += 1
        if len(findings[identifier]) < 100:
            findings[identifier].append(item)

    with (open(query_path, 'w', encoding='ascii') if query_path is not None else nullcontext(None)) as query:
        if query is not None:
            _declarations(query, reader, checkpoint)
        for row in reader.iter_constraints():
            checkpoint()
            a, b, c = _terms(row)
            # w0 is always 1. With all other wires 0, each combination equals
            # its constant coefficient. A complete successful verification is
            # a concrete witness of existence, independent of uniqueness.
            if (a.get(0, 0) * b.get(0, 0) - c.get(0, 0)) % p:
                zero_assignment_valid = False
            constraints_checked += 1
            if query is not None:
                _assertion(query, (a, b, c), p, checkpoint)
            if prime_field:
                effective, trivial = _prime_field_structure(a, b, c, p)
                normalized += 1
                for wire in effective:
                    used[wire] = 1
                if trivial:
                    record('trivial_constraints', {'constraint': row['index']})
                continue
            cost = len(a) * len(b) + len(c)
            if cost > row_limit or spent + cost > expansion_limit:
                # Conservatively mark all syntactically present variables. This can
                # miss unused wires, but cannot invent an unused-wire finding.
                for part in (a, b, c):
                    for wire in part:
                        used[wire] = 1
                continue
            spent += cost
            normalized += 1
            poly = polynomial(a, b, c, p)
            for monomial in poly:
                for wire in monomial:
                    used[wire] = 1
            if not poly:
                record('trivial_constraints', {'constraint': row['index']})
        if query is not None:
            query.write('(check-sat)\n')
    unused = []
    unused_count = 0
    for wire in range(1, reader.wires):
        if wire % 256 == 0:
            checkpoint()
        if not used[wire]:
            unused_count += 1
            if len(unused) < 100:
                unused.append({'wire': wire})
    total = reader.metadata['constraints']
    checks = []
    for identifier, count, items, complete, description in (
        ('unused_wires', unused_count, unused, normalized == total,
         'wires absent from the effective constraints (excluding w0)'),
        ('trivial_constraints', counts['trivial_constraints'], findings['trivial_constraints'],
         normalized == total, 'constraints that simplify to 0 = 0'),
    ):
        status = 'warning' if count else 'pass' if complete else 'unknown'
        if count == 1:
            description = description.replace('wires absent', 'wire absent').replace(
                'constraints that simplify', 'constraint that simplifies')
        message = f'Found {count} {description}.'
        if not complete:
            message += ' Partial scan: the structural-check work limit was reached.'
        checks.append(check_result(identifier, status, message, count=count, findings=items,
                                   truncated=count > len(items), complete=complete,
                                   reason='completed' if complete else 'scan_limit'))
    if zero_assignment_valid and constraints_checked == total and prime_field:
        checks.append(check_result(
            'satisfiability', 'pass',
            'At least one assignment satisfies all constraints: verified w0 = 1 and all other wires = 0.',
            method='verified_zero_assignment', reason='completed',
            witness={'w0': '1', 'other_wires': '0', 'constraints_verified': total},
            elapsed_seconds=round(time.monotonic() - started, 2)))
    return checks


def aggregate(checks, no_outputs=False):
    """Structural/strong findings are advisory; output ambiguity is unsafe."""
    by_id = {item['id']: item for item in checks}
    states = {item['status'] for item in checks}
    if 'cancelled' in states:
        return 'cancelled', 'cancelled'
    if by_id['satisfiability']['status'] == 'fail':
        return 'unsatisfiable', 'inconsistent_constraints'
    if by_id['output_uniqueness']['status'] == 'fail':
        return 'unsafe', 'output_not_unique'
    if 'error' in states:
        return 'error', 'check_error'
    if 'unknown' in states:
        reason = 'timeout' if any(c.get('reason') == 'timeout' for c in checks) else 'inconclusive_checks'
        return 'unknown', reason
    if 'warning' in states:
        return 'warning', 'review_findings'
    if any(c['status'] == 'skipped' and not (c['id'] == 'output_uniqueness' and no_outputs) for c in checks):
        return 'unknown', 'incomplete_checks'
    if no_outputs:
        return 'not_applicable', 'no_outputs'
    return 'safe', 'completed'
