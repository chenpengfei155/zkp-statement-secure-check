"""Bounded sufficient proofs of uniqueness over the original R1CS equations.

Known means equal in every two satisfying assignments with identical inputs.
It does not mean a sampled value is known, or that an input is nonzero.
An incomplete proof never establishes ambiguity or satisfiability.
"""
from collections import deque

from web_ui.r1cs_witness import _Budget, _LimitReached, _boolean_wire, known_prime


def _injective_boolean_weights(coefficients, boolean, prime):
    """An unwrapped binary sum identifies every bit, including sparse masks."""
    if not all(wire in boolean for wire in coefficients):
        return False
    for sign in (1, -1):
        mask = 0
        for coefficient in coefficients.values():
            weight = coefficient * sign % prime
            if not weight or weight & (weight - 1) or mask & weight:
                break
            mask |= weight
        else:
            if mask < prime:
                return True
    return False


def prove_uniqueness(reader, checkpoint=lambda: None, *, timeout_seconds=1.0,
                     max_terms=250_000, max_work=2_000_000):
    """Return sufficient output/strong proofs; False always means unproved.

    Only exact field-linear implications and nonwrapping Boolean tuple
    injectivity are used. No witness values enter this proof.
    """
    checkpoint()
    metadata = reader.metadata
    outputs = metadata['public_outputs']
    input_start = 1 + outputs
    input_end = input_start + metadata['public_inputs'] + metadata['private_inputs']
    unknown = {'output_unique': False, 'signal_unique': False, 'known_count': 0}
    if input_end > reader.wires:
        return dict(unknown, reason='input_layout_unavailable')
    if not known_prime(reader.prime):
        return dict(unknown, reason='unverified_field')
    if (reader.wires > 100_000 or metadata['constraints'] > 50_000
            or reader.size > 16 * 1024 * 1024):
        return dict(unknown, reason='size_limit')
    budget = _Budget(checkpoint, timeout_seconds, max_work)
    known = bytearray(reader.wires)
    known[0] = 1
    known[input_start:input_end] = bytes([1]) * (input_end - input_start)
    ready = False

    def result(reason):
        return {'output_unique': all(known[1:input_start]),
                'signal_unique': all(known), 'known_count': sum(known), 'reason': reason}

    try:
        rows, boolean, dependents = [], set(), {}
        terms = 0
        for row in reader.iter_constraints():
            cost = sum(len(row[key]) for key in ('a', 'b', 'c'))
            budget.spend(cost + 1)
            parts = tuple(tuple((term['wire'], int(term['coefficient'])) for term in row[key]
                                if int(term['coefficient'])) for key in ('a', 'b', 'c'))
            terms += sum(map(len, parts))
            if terms > max_terms:
                return dict(unknown, reason='term_limit')
            for wire in {wire for part in parts for wire, _ in part if wire}:
                dependents.setdefault(wire, []).append(len(rows))
            rows.append(parts)
            wire = _boolean_wire(parts, reader.prime)
            if wire is not None:
                boolean.add(wire)
        if len(rows) != metadata['constraints']:
            return dict(unknown, reason='incomplete_constraint_scan')
        ready = True
        pending = deque(range(len(rows)))
        queued = bytearray([1]) * len(rows)
        while pending:
            index = pending.popleft()
            queued[index] = 0
            row = rows[index]
            budget.spend(sum(map(len, row)) + 1)
            a, b, c = map(dict, row)
            if not any(a) or not any(b):
                # Only a literal constant is a valid field multiplier here.
                # A unique input could be zero and cannot justify division.
                factor, part = (a.get(0, 0), b) if not any(a) else (b.get(0, 0), a)
                coefficients = {wire: (factor * part.get(wire, 0) - c.get(wire, 0)) % reader.prime
                                for wire in part.keys() | c.keys() if wire and not known[wire]}
                coefficients = {wire: value for wire, value in coefficients.items() if value}
            elif all(known[wire] for part in (a, b) for wire in part):
                # A and B may be nonlinear in the inputs, but their product is
                # equal across the two assignments; C has constant coefficients.
                coefficients = {wire: value for wire, value in c.items() if wire and not known[wire]}
            else:
                continue
            if len(coefficients) != 1 and not (coefficients and _injective_boolean_weights(
                    coefficients, boolean, reader.prime)):
                continue
            for wire in coefficients:
                known[wire] = 1
                for affected in dependents.get(wire, ()):
                    if not queued[affected]:
                        pending.append(affected)
                        queued[affected] = 1
        proof = result('completed')
        if not proof['output_unique'] or not proof['signal_unique']:
            proof['reason'] = 'incomplete_proof'
        return proof
    except _LimitReached:
        # Already established implications remain valid if propagation stops;
        # incomplete construction, however, must not produce any proof.
        return result('budget_limit') if ready else dict(unknown, reason='budget_limit')
