"""Conservative, bounded checks of compiled constraints (not a safety proof).

Normalize A*B-C modulo the file's modulus before checking wire occurrence:
syntactic occurrence alone is misleading in constraints such as x*0=0.
No input/output uniqueness or application-specific correctness is inferred.
"""

from collections import Counter
from math import gcd
import time


class AnalysisCancelled(Exception):
    pass


# Capability coverage, not nine passing test results. Compiled constraints do not
# retain source assignments, components, declared types or conditional execution.
SOURCE_CHECK_COVERAGE = [
    ('unconstrained_output', 'Unconstrained output / 未受约束的输出', 'limited',
     'Only detects public outputs absent from every effective constraint; does not establish output uniqueness.'),
    ('unconstrained_component_input', 'Unconstrained component input / 组件输入未受约束', 'unavailable',
     'Component boundaries and component input declarations are not present in R1CS.'),
    ('data_flow_constraint_discrepancy', 'Data flow / constraint discrepancy / 计算与约束不一致', 'unavailable',
     'The original witness computation and assignment expressions are not present in R1CS.'),
    ('unused_component_output', 'Unused component output / 未使用的组件输出', 'unavailable',
     'Component boundaries and source-level uses are not present in R1CS.'),
    ('type_mismatch', 'Type mismatch / 类型不匹配', 'unavailable',
     'Source type and template information is not present in R1CS.'),
    ('assignment_misuse', 'Assignment misuse / 赋值误用', 'unavailable',
     'R1CS does not record whether <--, <== or other source assignments were used.'),
    ('unused_signal', 'Unused signal / 未使用的信号', 'limited',
     'Only detects retained wires absent from effective constraints; optimized-away declarations cannot be recovered.'),
    ('divide_by_zero', 'Unsafe division / 除零风险', 'unavailable',
     'Original division expressions and witness computation are not present in R1CS.'),
    ('nondeterministic_data_flow', 'Nondeterministic data flow / 非确定性数据流', 'unavailable',
     'Original conditional computation is not present in R1CS; output uniqueness is not checked.'),
]


def analyze_r1cs(reader, *, cancelled=lambda: False, progress=lambda done, total: None,
                 timeout=20, max_products=1_000_000, max_row_products=4096,
                 max_linear_operations=100_000, max_findings=200):
    started = time.monotonic()
    total = reader.metadata['constraints']
    modulus = reader.prime
    effective = bytearray(reader.wires)
    uncertain = bytearray(reader.wires)
    basis = {}
    findings = []
    counts = Counter()
    incomplete = set()
    stats = {'constraints_scanned': 0, 'constraints_total': total,
             'normalized_constraints': 0, 'skipped_constraints': 0,
             'tautologies': 0, 'linear_constraints_checked': 0}
    products_left = max_products
    linear_left = max_linear_operations

    def checkpoint():
        if cancelled():
            raise AnalysisCancelled()
        return time.monotonic() - started < timeout

    def finding(code, severity, message, *, wires=(), constraints=(), count=None):
        counts[severity] += 1
        if len(findings) < max_findings:
            findings.append({'code': code, 'severity': severity, 'message': message,
                             'wires': list(wires)[:20], 'constraints': list(constraints)[:20],
                             'affected_count': count if count is not None else max(len(wires), len(constraints)),
                             'evidence_truncated': len(wires) > 20 or len(constraints) > 20
                                                   or (count is not None and count > 20)})

    def check_linear(poly, index):
        nonlocal linear_left
        if len(poly) > 128 or len(basis) >= 256:
            incomplete.add('Some linear consistency checks exceeded the size limit.')
            return
        row = {term[0]: coefficient for term, coefficient in poly.items() if term}
        constant = poly.get((), 0)
        evidence = {index}
        while row:
            if not checkpoint():
                incomplete.add('The analysis time limit was reached.')
                return
            pivot = min(row)
            factor = row[pivot]
            if pivot not in basis:
                if gcd(factor, modulus) != 1:
                    incomplete.add('A non-invertible coefficient prevented a linear consistency check.')
                    return
                inverse = pow(factor, -1, modulus)
                basis[pivot] = ({wire: value * inverse % modulus for wire, value in row.items()},
                                constant * inverse % modulus, evidence)
                stats['linear_constraints_checked'] += 1
                return
            previous, value, origins = basis[pivot]
            if len(previous) > linear_left:
                incomplete.add('Some linear consistency checks exceeded the operation limit.')
                return
            linear_left -= len(previous)
            constant = (constant - factor * value) % modulus
            evidence |= origins
            for wire, coefficient in previous.items():
                updated = (row.get(wire, 0) - factor * coefficient) % modulus
                if updated:
                    row[wire] = updated
                else:
                    row.pop(wire, None)
            if len(row) > 128:
                incomplete.add('Some linear consistency checks exceeded the size limit.')
                return
        stats['linear_constraints_checked'] += 1
        if constant:
            finding('contradictory_linear_constraints', 'error',
                    'These linear constraints are inconsistent modulo p; no witness can satisfy them all.',
                    constraints=sorted(evidence))

    def scan():
        nonlocal products_left
        for constraint in reader.iter_constraints():
            if not checkpoint():
                incomplete.add('The analysis time limit was reached.')
                return
            index = constraint['index']
            terms = [{t['wire']: int(t['coefficient']) for t in constraint[name] if int(t['coefficient'])}
                     for name in ('a', 'b', 'c')]
            a, b, c = terms
            product_count = len(a) * len(b)
            if product_count > min(max_row_products, products_left):
                for combination in terms:
                    for wire in combination:
                        uncertain[wire] = 1
                stats['skipped_constraints'] += 1
                incomplete.add('Some dense constraints exceeded the polynomial expansion limit.')
            else:
                products_left -= product_count
                poly = {}
                for left, left_value in a.items():
                    for right, right_value in b.items():
                        monomial = tuple(sorted(w for w in (left, right) if w))
                        poly[monomial] = (poly.get(monomial, 0) + left_value * right_value) % modulus
                for wire, coefficient in c.items():
                    monomial = (wire,) if wire else ()
                    poly[monomial] = (poly.get(monomial, 0) - coefficient) % modulus
                poly = {term: value for term, value in poly.items() if value}
                stats['normalized_constraints'] += 1
                for term in poly:
                    for wire in term:
                        effective[wire] = 1
                if not poly:
                    stats['tautologies'] += 1
                    finding('ineffective_constraint', 'info',
                            'This constraint simplifies to 0 = 0 and imposes no restriction.', constraints=[index])
                elif set(poly) == {()}:
                    finding('impossible_constraint', 'error',
                            f'This constraint simplifies to {poly[()]} = 0 modulo p and cannot be satisfied.',
                            constraints=[index])
                elif all(len(term) <= 1 for term in poly):
                    check_linear(poly, index)
            stats['constraints_scanned'] += 1
            if stats['constraints_scanned'] % 100 == 0:
                progress(stats['constraints_scanned'], total)

    scan()
    if cancelled():
        raise AnalysisCancelled()
    # A partly unread file may constrain any wire later: never report absence from it.
    if stats['constraints_scanned'] == total:
        output_count = reader.metadata['public_outputs']
        absent_outputs, absent_other = [], []
        output_total = other_total = 0
        for wire in range(1, reader.wires):
            if not effective[wire] and not uncertain[wire]:
                if wire <= output_count:
                    output_total += 1
                    if len(absent_outputs) < 20:
                        absent_outputs.append(wire)
                else:
                    other_total += 1
                    if len(absent_other) < 20:
                        absent_other.append(wire)
            if wire % 10000 == 0 and not checkpoint():
                incomplete.add('The wire scan time limit was reached; counts cover only scanned wires.')
                break
        stats['unconstrained_outputs'] = output_total
        stats['unused_other_wires'] = other_total
        if absent_outputs:
            finding('unconstrained_output', 'warning',
                    'These public outputs do not occur in any effective constraint. If the remaining '
                    'constraints are satisfiable, these outputs can take arbitrary field values.',
                    wires=absent_outputs, count=output_total)
        if absent_other:
            finding('unused_wire', 'info',
                    'These non-output wires do not affect any effective constraint. Check whether '
                    'they were intended to participate; unused wires alone do not prove a vulnerability.',
                    wires=absent_other, count=other_total)
    else:
        stats['unconstrained_outputs'] = None
        stats['unused_other_wires'] = None
    # Findings establishing impossible constraints take precedence over conditional warnings.
    has_errors = bool(counts['error'])
    for item in findings:
        if item['code'] == 'unconstrained_output' and has_errors:
            item['message'] += ' This file also contains a proven contradiction, so it has no valid witness.'
    progress(stats['constraints_scanned'], total)
    verdict = ('issues_found' if counts['error'] or counts['warning'] else
               'inconclusive' if incomplete else 'no_findings')
    return {'kind': 'r1cs', 'analysis': 'basic_constraint_checks',
            'status': 'partial' if incomplete else 'completed', 'verdict': verdict,
            'counts': {severity: counts[severity] for severity in ('error', 'warning', 'info')},
            'findings': findings, 'findings_truncated': sum(counts.values()) > len(findings),
            'stats': stats, 'incomplete_reasons': sorted(incomplete),
            'coverage': [{'code': code, 'name': name, 'status': status, 'reason': reason}
                         for code, name, status, reason in SOURCE_CHECK_COVERAGE],
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'limitations': 'Basic structural and linear checks only. No complete underconstraint, '
                           'output-uniqueness, nonlinear satisfiability, or application-logic check '
                           'was performed. No findings does not establish circuit safety.'}
