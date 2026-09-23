"""Bounded structural checks and an exact finite-field satisfiability query.

These checks describe the compiled equations, not the original source program.
All indices in reports are zero-based, as in the constraint viewer API.
"""
import hashlib
import json


CHECK_NAMES = {
    'satisfiability': 'Constraint Satisfiability',
    'output_uniqueness': 'Output Uniqueness',
    'signal_uniqueness': 'All-Signal Uniqueness (including internal signals)',
    'unused_wires': 'Unused Wires',
    'trivial_constraints': 'Trivial Constraints',
    'duplicate_constraints': 'Duplicate Constraints',
}


def check_result(identifier, status, message, **details):
    return dict(id=identifier, name=CHECK_NAMES[identifier], status=status,
                message=message, **details)


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


def prepare_checks(reader, query_path, checkpoint=lambda: None, *,
                   expansion_limit=1_000_000, row_limit=20_000, index_limit=100_000):
    """Stream SMT and scan constraints. Capped work is reported as partial, never pass."""
    p = reader.prime
    used = bytearray(reader.wires)
    seen = {}
    findings = {'trivial_constraints': [], 'duplicate_constraints': []}
    counts = {key: 0 for key in findings}
    normalized = 0
    indexed = 0
    spent = 0

    def record(identifier, item):
        counts[identifier] += 1
        if len(findings[identifier]) < 100:
            findings[identifier].append(item)

    def combination(terms):
        parts = []
        for i, (wire, coefficient) in enumerate(terms.items()):
            if i % 256 == 0:
                checkpoint()
            value = f'#f{coefficient}m{p}'
            parts.append(value if wire == 0 else f'(ff.mul {value} w{wire})')
        return (f'(ff.add {" ".join(parts)})' if len(parts) > 1
                else parts[0] if parts else f'#f0m{p}')

    with open(query_path, 'w', encoding='ascii') as query:
        query.write(f'(set-logic QF_FF)\n(define-sort F () (_ FiniteField {p}))\n')
        for wire in range(1, reader.wires):
            if wire % 256 == 0:
                checkpoint()
            query.write(f'(declare-fun w{wire} () F)\n')
        for row in reader.iter_constraints():
            checkpoint()
            a, b, c = ({t['wire']: int(t['coefficient']) for t in row[key]
                        if int(t['coefficient'])} for key in ('a', 'b', 'c'))
            query.write(f'(assert (= (ff.mul {combination(a)} {combination(b)}) {combination(c)}))\n')
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
            digest = hashlib.sha256(json.dumps(sorted(poly.items()), separators=(',', ':')).encode()).digest()
            if digest in seen:
                indexed += 1
                record('duplicate_constraints', {'constraint': row['index'], 'duplicate_of': seen[digest]})
            elif len(seen) < index_limit:
                seen[digest] = row['index']
                indexed += 1
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
        ('duplicate_constraints', counts['duplicate_constraints'], findings['duplicate_constraints'],
         indexed == total, 'repeated equations after polynomial normalization'),
    ):
        status = 'warning' if count else 'pass' if complete else 'unknown'
        if count == 1:
            description = description.replace('wires absent', 'wire absent').replace(
                'constraints that simplify', 'constraint that simplifies').replace('repeated equations', 'repeated equation')
        message = f'Found {count} {description}.'
        if not complete:
            message += ' Partial scan: the structural-check work limit was reached.'
        checks.append(check_result(identifier, status, message, count=count, findings=items,
                                   truncated=count > len(items), complete=complete,
                                   reason='completed' if complete else 'scan_limit'))
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
