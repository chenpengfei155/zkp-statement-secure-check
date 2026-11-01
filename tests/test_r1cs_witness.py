"""Bounded candidate construction must finish with an exact R1CS verification."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui.r1cs_witness import find_verified_assignment
from picus_fixtures import r1cs


def boolean(wire):
    return ({0: -1, wire: 1}, {wire: 1}, {})


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def reader(self, rows, **options):
        path = Path(self.directory.name) / 'candidate.r1cs'
        path.write_bytes(r1cs(rows, prime=options.pop('prime', 17), **options))
        return R1CSFile(path)

    def assert_verified(self, reader, witness):
        self.assertIsNotNone(witness)
        values = witness['values']
        self.assertIsInstance(values, list)
        self.assertEqual(len(values), reader.wires)
        self.assertTrue(all(type(value) is int and 0 <= value < reader.prime for value in values))
        self.assertEqual(values[0], 1)
        if witness['inputs_seeded']:
            self.assertIn(witness['input_seed'], (0, 1))
        else:
            self.assertIsNone(witness['input_seed'])
            self.assertEqual(witness['seeded_input_count'], 0)
        self.assertIn(witness['unresolved_fill'], (0, 1))
        count = 0
        for row in reader.iter_constraints():
            a, b, c = (sum(int(term['coefficient']) * values[term['wire']]
                           for term in row[key]) % reader.prime for key in ('a', 'b', 'c'))
            self.assertEqual((a * b - c) % reader.prime, 0, f"constraint {row['index']}")
            count += 1
        self.assertEqual(count, reader.metadata['constraints'])
        self.assertEqual(witness['constraints_verified'], count)
        return values

    def test_reverse_order_linear_chain_requires_another_pass(self):
        reader = self.reader([
            ({1: 1}, {0: 1}, {3: 1, 0: 1}),  # out = temporary + 1
            ({3: 1}, {0: 1}, {2: 1, 0: 2}),  # temporary = input + 2
        ], wires=4, private=1)
        values = self.assert_verified(reader, find_verified_assignment(reader))
        self.assertEqual(values, [1, 3, 0, 2])

    def test_long_reverse_chain_uses_dependencies_instead_of_full_rescans(self):
        rows = [({wire: 1}, {0: 1}, {wire + 1: 1, 0: 1})
                for wire in range(1, 129)]
        rows.append(({129: 1}, {0: 1}, {0: 2}))
        reader = self.reader(rows, wires=130, prime=65521)
        witness = find_verified_assignment(reader, max_passes=4, max_work=5000)
        values = self.assert_verified(reader, witness)
        self.assertEqual(values[1], 130)
        self.assertEqual(values[129], 2)

    def test_linear_division_uses_the_field_modulus(self):
        reader = self.reader([({1: 3}, {0: 1}, {0: 2})])
        values = self.assert_verified(reader, find_verified_assignment(reader))
        self.assertEqual(values[1], 12)  # 3 * 12 = 2 (mod 17).

    def test_zero_coefficient_is_not_divided_by(self):
        reader = self.reader([({1: 1}, {}, {})])
        self.assert_verified(reader, find_verified_assignment(reader))
        reader = self.reader([({1: 1}, {}, {0: 1})])
        self.assertIsNone(find_verified_assignment(reader))

    def test_constant_wire_cannot_be_zeroed_to_satisfy_a_conflict(self):
        reader = self.reader([({0: 1}, {0: 1}, {})])
        self.assertIsNone(find_verified_assignment(reader))

    def test_failed_zero_input_candidate_retries_one_without_claiming_unsat(self):
        reader = self.reader([
            ({2: 1}, {0: 1}, {0: 1}),
            ({1: 1}, {0: 1}, {2: 1}),
        ], wires=3, private=1)
        witness = find_verified_assignment(reader)
        values = self.assert_verified(reader, witness)
        self.assertEqual(witness['input_seed'], 1)
        self.assertEqual(values, [1, 1, 1])

    def test_unknown_wires_may_be_filled_only_if_full_verification_passes(self):
        reader = self.reader([({1: 1}, {1: 1}, {0: 1})])
        witness = find_verified_assignment(reader)
        values = self.assert_verified(reader, witness)
        self.assertEqual(values[1], 1)
        self.assertEqual(witness['unresolved_fill'], 1)
        reader = self.reader([({1: 1}, {1: 1}, {0: 4})])
        self.assertIsNone(find_verified_assignment(reader))  # +/-2 require the solver.

    def test_late_nonlinear_conflict_prevents_partial_success(self):
        rows = [({1: 1}, {0: 1}, {0: 2})] * 128
        rows.append(({1: 1}, {1: 1}, {0: 3}))  # 2 squared is not 3.
        self.assertIsNone(find_verified_assignment(self.reader(rows)))

    def test_candidate_is_reverified_against_a_fresh_constraint_scan(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 3})])
        real_rows = list(reader.iter_constraints())
        initial_rows = [dict(real_rows[0], c=[{'wire': 0, 'coefficient': '2'}])]
        scans = 0

        def scan():
            nonlocal scans
            scans += 1
            return iter(initial_rows if scans == 1 else real_rows)

        with patch.object(reader, 'iter_constraints', side_effect=scan):
            witness = find_verified_assignment(reader)
        self.assertGreaterEqual(scans, 2)
        if witness is not None:
            # A constructor may rebuild after detecting changed constraints.
            self.assertEqual(self.assert_verified(reader, witness)[1], 3)

    def test_incomplete_constraint_scan_never_returns_a_witness(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 1})])
        for declared in (0, 2):
            with self.subTest(declared=declared):
                reader.metadata['constraints'] = declared
                self.assertIsNone(find_verified_assignment(reader))

    def test_boolean_decomposition_handles_both_equation_signs(self):
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                rows = [boolean(wire) for wire in (1, 2, 3)]
                rows.append(({}, {}, {0: -5 * sign, 1: sign, 2: 2 * sign, 3: 4 * sign}))
                reader = self.reader(rows, wires=4)
                values = self.assert_verified(reader, find_verified_assignment(reader))
                self.assertEqual(values, [1, 1, 0, 1])

    def test_binary_weight_heuristic_requires_boolean_constraints(self):
        reader = self.reader([({}, {}, {0: 5, 1: -1, 2: -2, 3: -4})], wires=4)
        self.assertIsNone(find_verified_assignment(reader))

    def test_duplicate_binary_weights_are_not_a_bit_decomposition(self):
        rows = [boolean(wire) for wire in (1, 2, 3)]
        rows.append(({}, {}, {0: 1, 1: -1, 2: -1, 3: -2}))
        self.assertIsNone(find_verified_assignment(self.reader(rows, wires=4)))

    def test_modular_bit_decomposition_returns_only_a_verified_candidate(self):
        rows = [boolean(wire) for wire in (1, 2, 3)]
        rows.append(({}, {}, {0: 3, 1: -1, 2: -2, 3: -4}))
        reader = self.reader(rows, wires=4, prime=7)
        values = self.assert_verified(reader, find_verified_assignment(reader))
        self.assertEqual(values, [1, 1, 1, 0])

    def test_sparse_modular_bits_can_use_a_lifted_representative(self):
        rows = [boolean(wire) for wire in range(1, 5)]
        rows.append(({}, {}, {0: 3, 1: -2, 2: -4, 3: -8, 4: -16}))
        reader = self.reader(rows, wires=5)
        values = self.assert_verified(reader, find_verified_assignment(reader))
        # 3 has an unavailable low bit, but 3 + 17 = 4 + 16 is representable.
        self.assertEqual(values, [1, 0, 1, 0, 1])

    def test_modular_bit_candidate_cannot_override_another_constraint(self):
        rows = [boolean(wire) for wire in range(1, 5)]
        rows.append(({}, {}, {0: 3, 1: -2, 2: -4, 3: -8, 4: -16}))
        rows.append(({2: 1}, {2: 1}, {0: 2}))  # Contradicts boolean wire 2.
        self.assertIsNone(find_verified_assignment(self.reader(rows, wires=5)))

    def test_unproven_field_moduli_do_not_bypass_solver_validation(self):
        for prime in (15, 2**61 - 1):
            with self.subTest(prime=prime):
                reader = self.reader([({1: 1}, {0: 1}, {0: 1})], prime=prime)
                self.assertIsNone(find_verified_assignment(reader))

    def test_budgets_return_no_conclusion(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 2})])
        for options in ({'timeout_seconds': 0}, {'max_terms': 0},
                        {'max_work': 0}, {'max_passes': 0}):
            with self.subTest(options=options):
                self.assertIsNone(find_verified_assignment(reader, **options))

    def test_cancellation_is_propagated_instead_of_becoming_a_candidate_failure(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 2})] * 128)
        calls = 0

        def checkpoint():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise InterruptedError('cancelled')

        with self.assertRaisesRegex(InterruptedError, 'cancelled'):
            find_verified_assignment(reader, checkpoint)

    def test_cancellation_during_dependency_propagation_is_not_swallowed(self):
        rows = [({1: 1}, {0: 1}, {0: 2})] * 128
        reader = self.reader(rows)
        calls = 0

        def checkpoint():
            nonlocal calls
            calls += 1
            if calls > len(rows) + 2:
                raise InterruptedError('cancelled')

        with self.assertRaisesRegex(InterruptedError, 'cancelled'):
            find_verified_assignment(reader, checkpoint)

    def test_unavailable_input_layout_uses_constants_without_guessing_inputs(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 3})], private=10)
        witness = find_verified_assignment(reader)
        values = self.assert_verified(reader, witness)
        self.assertEqual(values, [1, 3])
        self.assertFalse(witness['inputs_seeded'])
        self.assertIsNone(witness['input_seed'])
        self.assertEqual(witness['seeded_input_count'], 0)

    def test_unavailable_input_layout_does_not_bypass_full_verification(self):
        reader = self.reader([({0: 1}, {0: 1}, {})], private=10)
        self.assertIsNone(find_verified_assignment(reader))

    def test_empty_constraints_still_return_a_complete_assignment(self):
        reader = self.reader([], wires=3)
        self.assert_verified(reader, find_verified_assignment(reader))

    def test_alternative_assignments_share_all_public_and_private_inputs(self):
        reader = self.reader([boolean(1)], wires=5, public=1, private=1)
        witness = find_verified_assignment(reader, collect_alternative=True)
        first = self.assert_verified(reader, witness)
        second = self.assert_verified(reader, dict(witness, values=witness['alternative_values']))
        self.assertNotEqual(first, second)
        self.assertEqual(first[2:4], second[2:4])
        self.assertNotEqual(first[1], second[1])
        self.assertEqual(first[0], second[0])

    def test_alternative_must_pass_the_entire_constraint_scan(self):
        reader = self.reader([boolean(1), ({1: 1}, {1: 1}, {})])
        witness = find_verified_assignment(reader, collect_alternative=True)
        self.assertEqual(self.assert_verified(reader, witness)[1], 0)
        self.assertIsNone(witness['alternative_values'])

    def test_different_input_seeds_do_not_form_an_alternative_pair(self):
        reader = self.reader([({1: 1}, {0: 1}, {2: 1})], wires=3, private=1)
        witness = find_verified_assignment(reader, collect_alternative=True)
        self.assertEqual(self.assert_verified(reader, witness), [1, 0, 0])
        self.assertIsNone(witness['alternative_values'])

    def test_alternative_budget_exhaustion_retains_only_the_verified_first(self):
        reader = self.reader([boolean(1)])
        self.assertIsNone(find_verified_assignment(reader, max_work=13, collect_alternative=True))
        witness = find_verified_assignment(reader, max_work=14, collect_alternative=True)
        self.assertEqual(self.assert_verified(reader, witness)[1], 0)
        self.assertIsNone(witness['alternative_values'])

    def test_alternative_search_cancellation_still_propagates(self):
        reader = self.reader([boolean(1)])
        from web_ui.r1cs_witness import _verify
        verified_first = False

        def verify(*args):
            nonlocal verified_first
            result = _verify(*args)
            verified_first = result
            return result

        def checkpoint():
            if verified_first:
                raise InterruptedError('cancelled after first witness')

        with patch('web_ui.r1cs_witness._verify', side_effect=verify):
            with self.assertRaisesRegex(InterruptedError, 'cancelled after first witness'):
                find_verified_assignment(reader, checkpoint, collect_alternative=True)

    def test_unknown_input_layout_never_supplies_an_alternative_pair(self):
        reader = self.reader([boolean(1)], private=10)
        witness = find_verified_assignment(reader, collect_alternative=True)
        self.assert_verified(reader, witness)
        self.assertIsNone(witness['alternative_values'])

    def test_wrapped_bit_branch_produces_a_fully_verified_pair(self):
        rows = [boolean(wire) for wire in (1, 2, 3)]
        rows.append(({}, {}, {1: 1, 2: 2, 3: 4}))
        reader = self.reader(rows, wires=4, prime=7)
        witness = find_verified_assignment(reader, collect_alternative=True)
        first = self.assert_verified(reader, witness)
        second = self.assert_verified(reader, dict(witness, values=witness['alternative_values']))
        self.assertEqual(first, [1, 0, 0, 0])
        self.assertEqual(second, [1, 1, 1, 1])  # 1 + 2 + 4 = 0 mod 7.

    def test_wrapped_branch_recognizes_top_bit_weights_reduced_modulo_the_prime(self):
        rows = [boolean(wire) for wire in range(1, 8)]
        rows.append(({}, {}, {wire: 1 << (wire - 1) for wire in range(1, 8)}))
        reader = self.reader(rows, wires=8)
        witness = find_verified_assignment(reader, collect_alternative=True)
        first = self.assert_verified(reader, witness)
        second = self.assert_verified(reader, dict(witness, values=witness['alternative_values']))
        self.assertEqual(first, [1] + [0] * 7)
        self.assertEqual(sum(value << bit for bit, value in enumerate(second[1:])), 17)

    def test_non_bit_weights_do_not_invent_a_wrapped_assignment(self):
        rows = [boolean(wire) for wire in (1, 2, 3)]
        rows.append(({}, {}, {1: 1, 2: 2, 3: 3}))
        reader = self.reader(rows, wires=4, prime=7)
        witness = find_verified_assignment(reader, collect_alternative=True)
        self.assertEqual(self.assert_verified(reader, witness), [1, 0, 0, 0])
        self.assertIsNone(witness['alternative_values'])

    def test_no_wrapped_choice_does_not_add_another_constraint_scan(self):
        rows = [boolean(wire) for wire in (1, 2, 3)]
        rows.append(({}, {}, {0: -3, 1: 1, 2: 2, 3: 4}))
        reader = self.reader(rows, wires=4)
        with patch.object(reader, 'iter_constraints', wraps=reader.iter_constraints) as scans:
            witness = find_verified_assignment(reader, collect_alternative=True)
            self.assertEqual(scans.call_count, 2)  # Construction and full verification.
        self.assert_verified(reader, witness)
        self.assertIsNone(witness['alternative_values'])

    def test_wrapped_branch_cannot_override_another_equation(self):
        rows = [boolean(wire) for wire in (1, 2, 3)]
        rows.extend([({}, {}, {1: 1, 2: 2, 3: 4}), ({1: 1}, {1: 1}, {})])
        reader = self.reader(rows, wires=4, prime=7)
        witness = find_verified_assignment(reader, collect_alternative=True)
        self.assertEqual(self.assert_verified(reader, witness), [1, 0, 0, 0])
        self.assertIsNone(witness['alternative_values'])

    def test_wrapped_branch_budget_exhaustion_keeps_the_first_witness(self):
        rows = [boolean(wire) for wire in (1, 2, 3)]
        rows.append(({}, {}, {1: 1, 2: 2, 3: 4}))
        reader = self.reader(rows, wires=4, prime=7)
        witness = find_verified_assignment(reader, max_work=66, collect_alternative=True)
        self.assertEqual(self.assert_verified(reader, witness), [1, 0, 0, 0])
        self.assertIsNone(witness['alternative_values'])

    def test_affine_input_can_be_derived_only_after_fixed_seeds_fail(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 2}),
                              ({2: 1}, {0: 1}, {1: 1, 0: 2})], wires=3, private=1)
        witness = find_verified_assignment(reader, collect_alternative=True)
        self.assertEqual(self.assert_verified(reader, witness), [1, 2, 4])
        self.assertEqual(witness['released_input_wires'], [2])
        self.assertEqual(witness['seeded_input_count'], 0)
        self.assertIsNone(witness['alternative_values'])

    def test_released_inputs_must_also_match_before_returning_a_pair(self):
        rows = [boolean(wire) for wire in range(3, 8)]
        rows.extend([({}, {}, {3: 1, 4: 2, 5: 4, 6: 8, 7: 16}),
                     ({1: 1}, {0: 1}, {3: 1}),
                     ({2: 1}, {0: 1}, {0: 2, 3: 1})])
        reader = self.reader(rows, wires=8, private=1)
        witness = find_verified_assignment(reader, collect_alternative=True)
        first = self.assert_verified(reader, witness)
        self.assertEqual(first[2], 2)
        self.assertEqual(witness['released_input_wires'], [2])
        # A lifted 17-bit representation also satisfies the equations, but its
        # input is 3 instead of 2. It cannot be reported as non-uniqueness.
        self.assertIsNone(witness['alternative_values'])

    def test_multiple_commitment_inputs_can_be_derived_together(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 2}),
                              ({2: 1}, {0: 1}, {1: 1, 0: 2}),
                              ({3: 1}, {0: 1}, {1: 1, 0: 3})], wires=4, private=2)
        witness = find_verified_assignment(reader, collect_alternative=True)
        self.assertEqual(self.assert_verified(reader, witness), [1, 2, 4, 5])
        self.assertEqual(witness['released_input_wires'], [2, 3])
        self.assertEqual(witness['seeded_input_count'], 0)
        self.assertIsNone(witness['alternative_values'])

    def test_second_seed_can_find_output_ambiguity_without_mixing_inputs(self):
        # For x=0 both z values have y=0; x=1 reveals different outputs y=z.
        reader = self.reader([({2: 1}, {3: 1}, {1: 1}), boolean(3)], wires=4, private=1)
        witness = find_verified_assignment(reader, collect_alternative=True)
        first = self.assert_verified(reader, witness)
        second = self.assert_verified(reader, dict(witness, values=witness['alternative_values']))
        self.assertEqual(first[2], second[2])
        self.assertEqual(first[2], 1)
        self.assertNotEqual(first[1], second[1])

    def test_second_seed_budget_exhaustion_keeps_the_first_verified_pair(self):
        from web_ui import r1cs_witness as implementation
        reader = self.reader([({2: 1}, {3: 1}, {1: 1}), boolean(3)], wires=4, private=1)
        propagate = implementation._propagate
        def limited(rows, dependents, boolean_wires, values, *args, **kwargs):
            if values[2] == 1:
                raise implementation._LimitReached()
            return propagate(rows, dependents, boolean_wires, values, *args, **kwargs)
        with patch.object(implementation, '_propagate', side_effect=limited):
            witness = find_verified_assignment(reader, collect_alternative=True)
        first = self.assert_verified(reader, witness)
        second = self.assert_verified(reader, dict(witness, values=witness['alternative_values']))
        self.assertEqual(first[2], 0)
        self.assertEqual(first[2], second[2])
        self.assertNotEqual(first[3], second[3])

    @unittest.skipUnless((ROOT / 'benchmarks/r1cs/circom-ml__model1_test.r1cs').is_file(),
                         'requires the ML model benchmark fixture')
    def test_model_output_counterexample_uses_one_shared_input_assignment(self):
        reader = R1CSFile(ROOT / 'benchmarks/r1cs/circom-ml__model1_test.r1cs')
        witness = find_verified_assignment(reader, collect_alternative=True)
        first = self.assert_verified(reader, witness)
        second = self.assert_verified(reader, dict(witness, values=witness['alternative_values']))
        start = 1 + reader.metadata['public_outputs']
        end = start + reader.metadata['public_inputs'] + reader.metadata['private_inputs']
        self.assertEqual(first[start:end], second[start:end])
        self.assertNotEqual(first[1:start], second[1:start])

    def test_joint_release_cannot_hide_an_inconsistent_original_equation(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 2}),
                              ({2: 1}, {0: 1}, {1: 1, 0: 2}),
                              ({3: 1}, {0: 1}, {1: 1, 0: 3}),
                              ({1: 1}, {1: 1}, {0: 5})], wires=4, private=2)
        self.assertIsNone(find_verified_assignment(reader, collect_alternative=True))

    @unittest.skipUnless((ROOT / 'benchmarks/r1cs/zk-SQL-4c3626d__insert.r1cs').is_file(),
                         'requires the SQL insert benchmark fixture')
    def test_sql_insert_derives_both_commitment_inputs(self):
        reader = R1CSFile(ROOT / 'benchmarks/r1cs/zk-SQL-4c3626d__insert.r1cs')
        witness = find_verified_assignment(reader, collect_alternative=True)
        values = self.assert_verified(reader, witness)
        self.assertEqual(witness['released_input_wires'], [2, 3])
        self.assertTrue(all(value not in (0, 1) for value in values[2:4]))
        self.assertEqual(witness['constraints_verified'], reader.metadata['constraints'])

    def test_affine_release_does_not_relax_multiple_uses_or_nonlinear_inputs(self):
        for rows in (
            [({2: 1}, {0: 1}, {0: 2}), ({2: 1}, {0: 1}, {1: 1})],
            [({2: 1}, {2: 1}, {0: 4})],
        ):
            with self.subTest(rows=rows):
                reader = self.reader(rows, wires=3, private=1)
                self.assertIsNone(find_verified_assignment(reader, collect_alternative=True))

    def test_affine_release_search_considers_at_most_eight_inputs(self):
        rows = [({1: 1}, {0: 1}, {})]
        rows.extend(({wire: 1}, {0: 1}, {}) for wire in range(2, 10))
        rows.append(({10: 1}, {0: 1}, {0: 2}))
        reader = self.reader(rows, wires=11, private=9)
        self.assertIsNone(find_verified_assignment(reader, collect_alternative=True))

    @unittest.skipUnless((ROOT / 'benchmarks/r1cs/iden3-core-56a08f9__utils_checkIdenStateMatchesRoots.r1cs').is_file(),
                         'requires the identity-state benchmark fixture')
    def test_expected_state_can_be_derived_without_changing_the_other_inputs(self):
        reader = R1CSFile(ROOT / 'benchmarks/r1cs/iden3-core-56a08f9__utils_checkIdenStateMatchesRoots.r1cs')
        witness = find_verified_assignment(reader, collect_alternative=True)
        values = self.assert_verified(reader, witness)
        self.assertEqual(values[1:4], [0, 0, 0])
        self.assertNotIn(values[4], (0, 1))
        self.assertEqual(witness['released_input_wires'], [4])
        self.assertEqual(witness['seeded_input_count'], 3)
        self.assertIsNone(witness['alternative_values'])

    def test_real_full_width_bit_circuits_have_verified_same_input_pairs(self):
        names = ('iden3-core-56a08f9__inTest.r1cs',
                 'iden3-core-56a08f9__utils_getClaimExpiration.r1cs')
        if not all((ROOT / 'benchmarks/r1cs' / name).is_file() for name in names):
            self.skipTest('requires full-width bit benchmark fixtures')
        for name in names:
            with self.subTest(name=name):
                reader = R1CSFile(ROOT / 'benchmarks/r1cs' / name)
                witness = find_verified_assignment(reader, collect_alternative=True)
                first = self.assert_verified(reader, witness)
                second = self.assert_verified(reader, dict(witness, values=witness['alternative_values']))
                start = 1 + reader.metadata['public_outputs']
                end = start + reader.metadata['public_inputs'] + reader.metadata['private_inputs']
                self.assertEqual(first[start:end], second[start:end])
                self.assertNotEqual(first, second)
                if 'getClaimExpiration' in name:
                    self.assertNotEqual(first[1:start], second[1:start])

    @unittest.skipUnless((ROOT / 'benchmarks/r1cs/ed25519-099d19c__binadd2.r1cs').is_file(),
                         'requires the binadd2 benchmark fixture')
    def test_binadd2_has_fully_verified_distinct_outputs_for_the_same_inputs(self):
        reader = R1CSFile(ROOT / 'benchmarks/r1cs/ed25519-099d19c__binadd2.r1cs')
        witness = find_verified_assignment(reader, collect_alternative=True)
        first = self.assert_verified(reader, witness)
        second = self.assert_verified(reader, dict(witness, values=witness['alternative_values']))
        input_start = 1 + reader.metadata['public_outputs']
        input_end = input_start + reader.metadata['public_inputs'] + reader.metadata['private_inputs']
        self.assertTrue(witness['inputs_seeded'])
        self.assertEqual(first[input_start:input_end], second[input_start:input_end])
        self.assertNotEqual(first[1:input_start], second[1:input_start])

    @unittest.skipUnless((ROOT / 'benchmarks/r1cs/circomlib-cff5ab6__Sign@sign.r1cs').is_file(),
                         'requires the Sign benchmark fixture')
    def test_sign_zero_inputs_have_nonzero_internal_values(self):
        reader = R1CSFile(ROOT / 'benchmarks/r1cs/circomlib-cff5ab6__Sign@sign.r1cs')
        witness = find_verified_assignment(reader)
        values = self.assert_verified(reader, witness)
        self.assertEqual(witness['input_seed'], 0)
        self.assertEqual(witness['constraints_verified'], 264)
        self.assertEqual(values[1], 0)
        self.assertTrue(all(value == 0 for value in values[2:256]))
        self.assertEqual(values[269], 8192)
        self.assertEqual(values[383], 134874104835293724451048291106324996096)

    def test_long_hash_chains_and_full_width_bits_have_verified_witnesses(self):
        names = (
            'circomlib-cff5ab6__Poseidon@poseidon.r1cs',
            'iden3-core-56a08f9__poseidon.r1cs',
            'circomlib-cff5ab6__Num2Bits_strict@bitify.r1cs',
            'circom-ml__IsPositive_test.r1cs',
            'circom-ml__IsNegative_test.r1cs',
        )
        if not all((ROOT / 'benchmarks/r1cs' / name).is_file() for name in names):
            self.skipTest('requires small benchmark fixtures')
        for name in names:
            with self.subTest(name=name):
                reader = R1CSFile(ROOT / 'benchmarks/r1cs' / name)
                self.assert_verified(reader, find_verified_assignment(reader, timeout_seconds=1.0))


if __name__ == '__main__':
    unittest.main()
