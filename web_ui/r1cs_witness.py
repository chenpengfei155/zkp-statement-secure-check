"""Bounded candidate construction; only full R1CS verification proves existence.

This is an incomplete witness search, not an UNSAT or uniqueness prover.
All arithmetic and the final verification use the original finite-field equations.
"""
import math
import time
from collections import deque


def known_prime(prime):
    """Only bypass solver field validation for primes established exactly here."""
    if prime == 21888242871839275222246405745257275088548364400416034343698204186575808495617:
        return True
    return (2 <= prime <= 65536
            and all(prime % divisor for divisor in range(2, math.isqrt(prime) + 1)))


class _LimitReached(Exception):
    pass


class _Budget:
    def __init__(self, checkpoint, seconds, work):
        self.checkpoint = checkpoint
        self.deadline = time.monotonic() + seconds
        self.remaining = work

    def spend(self, count=1):
        self.checkpoint()
        self.remaining -= count
        if self.remaining < 0 or time.monotonic() >= self.deadline:
            raise _LimitReached


def _boolean_wire(row, prime):
    """Recognize a nonzero multiple of x*x-x, without expanding large rows."""
    variables = {wire for part in row for wire, _ in part if wire}
    if len(variables) != 1:
        return None
    wire, = variables
    a, b, c = map(dict, row)
    quadratic = a.get(wire, 0) * b.get(wire, 0) % prime
    linear = (a.get(0, 0) * b.get(wire, 0)
              + b.get(0, 0) * a.get(wire, 0) - c.get(wire, 0)) % prime
    constant = (a.get(0, 0) * b.get(0, 0) - c.get(0, 0)) % prime
    return wire if quadratic and (linear + quadratic) % prime == 0 and constant == 0 else None


def _reduce(part, values, prime):
    constant = 0
    unknown = {}
    for wire, coefficient in part:
        value = values[wire]
        if value is None:
            unknown[wire] = coefficient
        else:
            constant += coefficient * value
    return constant % prime, unknown


def _binary_values(coefficients, constant, boolean, prime, *, prefer_wrapped=False,
                   branch_state=None):
    """Choose bits whose weighted integer sum has the required field residue.

    When the sum can wrap modulo the prime, this chooses a candidate, not a
    unique solution. Other equations can reject it during full verification.
    """
    if not all(wire in boolean for wire in coefficients):
        return None
    for sign in (1, -1):
        weights = {wire: coefficient * sign % prime for wire, coefficient in coefficients.items()}
        if any(not weight or weight & (weight - 1) for weight in weights.values()):
            # Num2Bits widths just beyond the field bit length store their top
            # powers modulo p. Reconstruct only a bounded, unambiguous sequence
            # of possible integer bit weights. The final R1CS check remains the
            # authority if this heuristic recognizes an unrelated linear row.
            lifted = {}
            for bit in range(min(512, prime.bit_length() + 2)):
                weight = 1 << bit
                residue = weight % prime
                lifted[residue] = None if residue in lifted else weight
            weights = {wire: lifted.get(weight) or 0 for wire, weight in weights.items()}
        mask = 0
        for weight in weights.values():
            if not weight or weight & (weight - 1) or mask & weight:
                break
            mask |= weight
        else:
            target = -constant * sign % prime
            # A sparse bit mask may represent target+p even when target itself
            # uses a missing bit. Keep this search bounded rather than enumerate
            # all representatives or claim a modular decomposition is unique.
            candidates = [candidate for candidate in (target, target + prime)
                          if candidate <= mask and candidate & ~mask == 0]
            if candidates:
                if len(candidates) == 2 and branch_state is not None:
                    branch_state['wrapped_choice'] = True
                candidate = candidates[-1] if prefer_wrapped else candidates[0]
                return {wire: int(bool(candidate & weight)) for wire, weight in weights.items()}
    return None


def _propagate(rows, dependents, boolean, values, prime, budget, passes, *,
               prefer_wrapped=False, branch_state=None):
    # Revisiting only rows touched by a new value handles long, reverse-ordered
    # computation chains without rescanning every unrelated row each round.
    pending = deque(range(len(rows)))
    queued = bytearray([1]) * len(rows)
    visits_left = max(0, passes) * len(rows)
    while pending and visits_left:
        index = pending.popleft()
        queued[index] = 0
        visits_left -= 1
        row = rows[index]
        budget.spend(sum(map(len, row)) + 1)
        (a0, a), (b0, b), (c0, c) = (_reduce(part, values, prime) for part in row)
        if a and b:
            # Leave genuinely nonlinear choices to candidate completion or SMT.
            continue
        factor, unknown = (a0, b) if not a else (b0, a)
        coefficients = {wire: factor * value for wire, value in unknown.items()}
        for wire, value in c.items():
            coefficients[wire] = coefficients.get(wire, 0) - value
        coefficients = {wire: value % prime for wire, value in coefficients.items() if value % prime}
        constant = (a0 * b0 - c0) % prime
        if not coefficients:
            if constant:
                return False  # Only this input candidate is inconsistent.
            continue
        if len(coefficients) == 1:
            wire, coefficient = next(iter(coefficients.items()))
            inferred = {wire: -constant * pow(coefficient, -1, prime) % prime}
        else:
            inferred = _binary_values(coefficients, constant, boolean, prime,
                                      prefer_wrapped=prefer_wrapped, branch_state=branch_state)
        if inferred:
            for wire, value in inferred.items():
                if wire in boolean and value not in (0, 1):
                    return False
                values[wire] = value
                for affected in dependents.get(wire, ()):
                    if not queued[affected]:
                        pending.append(affected)
                        queued[affected] = 1
    return True


def _verify(reader, values, budget):
    """Re-read every original equation; never trust propagation alone."""
    if len(values) != reader.wires or values[0] != 1:
        return False
    count = 0
    for row in reader.iter_constraints():
        budget.spend(sum(len(row[key]) for key in ('a', 'b', 'c')) + 1)
        a, b, c = (sum(int(term['coefficient']) * values[term['wire']] for term in row[key])
                   % reader.prime for key in ('a', 'b', 'c'))
        if (a * b - c) % reader.prime:
            return False
        count += 1
    budget.spend()
    return count == reader.metadata['constraints']


def find_verified_assignment(reader, checkpoint=lambda: None, *, timeout_seconds=1.0,
                             max_terms=250_000, max_work=2_000_000, max_passes=32,
                             collect_alternative=False):
    """Try zero/one input seeds with linear and bit-decomposition propagation.

    A missing result means only that this bounded heuristic did not find a
    witness. The caller must retain the original unrestricted solver query.
    Cancellation propagates to the task supervisor instead of being swallowed.
    Optionally retain a second, different, fully verified assignment with the
    same mapped inputs, including a second modular bit representation. Search
    each input seed independently, preferring a pair that differs in outputs.
    Never combine witnesses from different seeds into a counterexample. If the
    fixed seeds fail, try releasing at most eight affine leaf inputs, both
    individually and in growing groups (for multiple commitment comparisons).
    Failing to find a pair says nothing about uniqueness.
    """
    checkpoint()
    if (not known_prime(reader.prime) or reader.wires > 100_000
            or reader.metadata['constraints'] > 50_000 or reader.size > 16 * 1024 * 1024):
        return None
    input_start = 1 + reader.metadata['public_outputs']
    input_end = input_start + reader.metadata['public_inputs'] + reader.metadata['private_inputs']
    # SAT needs a witness, not a reconstructed input map. If optimization removed
    # input wires, use constants alone rather than guessing or clamping a layout.
    inputs_seeded = input_end <= reader.wires
    budget = _Budget(checkpoint, timeout_seconds, max_work)
    verified = None
    best_verified = None

    def pair_strength(witness):
        if witness is None or witness.get('alternative_values') is None:
            return 0
        return 2 if witness['values'][1:input_start] != witness['alternative_values'][1:input_start] else 1

    def better(first, second):
        if first is None or pair_strength(second) > pair_strength(first):
            return second
        return first
    try:
        rows, boolean, dependents = [], set(), {}
        terms = 0
        for row in reader.iter_constraints():
            budget.spend(sum(len(row[key]) for key in ('a', 'b', 'c')) + 1)
            parts = tuple(tuple((term['wire'], int(term['coefficient'])) for term in row[key]
                                if int(term['coefficient'])) for key in ('a', 'b', 'c'))
            terms += sum(map(len, parts))
            if terms > max_terms:
                return None
            for wire in {wire for part in parts for wire, _ in part if wire}:
                dependents.setdefault(wire, []).append(len(rows))
            rows.append(parts)
            wire = _boolean_wire(parts, reader.prime)
            if wire is not None:
                boolean.add(wire)
        if len(rows) != reader.metadata['constraints']:
            return None
        def attempt(seed, released=(), *, prefer_wrapped=False):
            nonlocal verified
            budget.spend()
            values = [None] * reader.wires
            values[0] = 1
            if inputs_seeded:
                values[input_start:input_end] = [seed] * (input_end - input_start)
            for wire in released:
                values[wire] = None
            branch_state = {}
            if not _propagate(rows, dependents, boolean, values, reader.prime, budget, max_passes,
                              prefer_wrapped=prefer_wrapped, branch_state=branch_state):
                return branch_state
            for fill in (0, 1):
                candidate = [fill if value is None else value for value in values]
                if verified is not None and candidate == verified['values']:
                    continue
                if _verify(reader, candidate, budget):
                    if verified is not None:
                        if candidate[input_start:input_end] == verified['values'][input_start:input_end]:
                            verified['alternative_values'] = candidate
                            return branch_state
                        continue
                    verified = {'values': candidate, 'input_seed': seed, 'unresolved_fill': fill,
                                'inputs_seeded': inputs_seeded,
                                'seeded_input_count': input_end - input_start - len(released) if inputs_seeded else 0,
                                'released_input_wires': list(released),
                                'constraints_verified': reader.metadata['constraints']}
                    if collect_alternative:
                        verified['alternative_values'] = None
                    if not collect_alternative or not inputs_seeded:
                        return branch_state
            return branch_state

        def search(seed, released=()):
            branch_state = attempt(seed, released)
            if (collect_alternative and inputs_seeded and branch_state.get('wrapped_choice')
                    and (verified is None or verified.get('alternative_values') is None)):
                # Recreate the entire assignment. Previously inferred bits must
                # not constrain the alternative modular representative.
                attempt(seed, released, prefer_wrapped=True)

        seeds = (0, 1) if inputs_seeded and input_end > input_start else (0,) if inputs_seeded else (None,)
        for seed in seeds:
            search(seed)
            if verified is not None:
                if (not collect_alternative or pair_strength(verified) == 2
                        or (input_start == 1 and pair_strength(verified))):
                    return verified
                best_verified = better(best_verified, verified)
                # The next seed starts a separate search. In particular, never
                # compare a new seed against the previous seed's assignment.
                verified = None
        if best_verified is not None:
            return best_verified

        if inputs_seeded:
            # A single affine occurrence is a useful search heuristic, not proof
            # that this input is a comparison value. Multiple commitments may
            # need to be derived together. All candidates still require full
            # verification of the original R1CS and exact input equality for pairs.
            releasable = []
            for wire in range(input_start, input_end):
                budget.spend()
                involved = dependents.get(wire, ())
                if len(involved) != 1:
                    continue
                row = rows[involved[0]]
                budget.spend(sum(map(len, row)) + 1)
                a, b, c = map(dict, row)
                if any(a) and any(b):
                    continue
                coefficient = (a.get(0, 0) * b.get(wire, 0)
                               + b.get(0, 0) * a.get(wire, 0) - c.get(wire, 0)) % reader.prime
                if coefficient:
                    releasable.append(wire)
                    if len(releasable) == 8:
                        break
            releases = []
            for index, wire in enumerate(releasable):
                releases.append((wire,))
                if index:
                    releases.append(tuple(releasable[:index + 1]))
            for seed in (0, 1):
                for released in releases:
                    search(seed, released)
                    if verified is not None:
                        return verified
    except _LimitReached:
        pass
    # Exhausting the optional second-candidate search cannot invalidate the
    # first candidate, whose full original-constraint verification finished.
    return better(best_verified, verified)
