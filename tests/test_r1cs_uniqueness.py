"""Every positive bounded proof must survive independent finite-field enumeration."""
from itertools import product
from pathlib import Path
import random
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui.r1cs_uniqueness import prove_uniqueness
from picus_fixtures import r1cs


def boolean(wire):
    return ({0: -1, wire: 1}, {wire: 1}, {})


class UniquenessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def reader(self, rows, **options):
        path = Path(self.directory.name) / 'proof.r1cs'
        path.write_bytes(r1cs(rows, prime=options.pop('prime', 7), **options))
        return R1CSFile(path)

    def assert_sound_by_enumeration(self, reader, proof=None):
        """Group all satisfying assignments by their actual mapped input tuple."""
        proof = prove_uniqueness(reader) if proof is None else proof
        rows = [tuple({term['wire']: int(term['coefficient']) for term in row[key]}
                      for key in ('a', 'b', 'c')) for row in reader.iter_constraints()]
        start = 1 + reader.metadata['public_outputs']
        end = start + reader.metadata['public_inputs'] + reader.metadata['private_inputs']
        groups = {}
        for suffix in product(range(reader.prime), repeat=reader.wires - 1):
            values = (1,) + suffix
            for row in rows:
                a, b, c = (sum(coefficient * values[wire] for wire, coefficient in part.items())
                           % reader.prime for part in row)
                if (a * b - c) % reader.prime:
                    break
            else:
                inputs = values[start:end]
                outputs = values[1:start]
                non_inputs = outputs + values[end:]
                if inputs in groups:
                    previous_outputs, previous_non_inputs = groups[inputs]
                    if proof['output_unique']:
                        self.assertEqual(previous_outputs, outputs, (proof, inputs, rows))
                    if proof['signal_unique']:
                        self.assertEqual(previous_non_inputs, non_inputs, (proof, inputs, rows))
                else:
                    groups[inputs] = outputs, non_inputs
        return proof

    def test_known_factors_can_determine_a_linear_output(self):
        reader = self.reader([({2: 1}, {3: 1}, {1: 3})], wires=4, private=2)
        proof = self.assert_sound_by_enumeration(reader)
        self.assertTrue(proof['output_unique'])
        self.assertTrue(proof['signal_unique'])

    def test_unique_input_is_not_a_nonzero_divisor(self):
        for c in ({}, {2: 1}):
            reader = self.reader([({2: 1}, {1: 1}, c)], wires=3, private=1)
            proof = self.assert_sound_by_enumeration(reader)
            self.assertFalse(proof['output_unique'])
            self.assertFalse(proof['signal_unique'])

    def test_square_roots_are_not_implicitly_unique(self):
        reader = self.reader([({1: 1}, {1: 1}, {0: 1})])
        proof = self.assert_sound_by_enumeration(reader)
        self.assertFalse(proof['output_unique'])

    def test_literal_zero_can_determine_c_without_determining_a(self):
        reader = self.reader([({2: 1}, {}, {1: 1})], wires=3)
        proof = self.assert_sound_by_enumeration(reader)
        self.assertTrue(proof['output_unique'])
        self.assertFalse(proof['signal_unique'])

    def test_linearized_unknown_coefficients_must_be_combined_and_cancelled(self):
        reader = self.reader([({1: 2, 3: 1}, {0: 2}, {1: 4, 2: 1})], wires=4, private=1)
        proof = self.assert_sound_by_enumeration(reader)
        self.assertFalse(proof['output_unique'])
        self.assertFalse(proof['signal_unique'])
        self.assertEqual(proof['known_count'], 3)  # Constant, input and internal wire 3.

    def test_sparse_nonwrapping_bits_are_injective_with_either_sign(self):
        for sign in (1, -1):
            rows = [boolean(1), boolean(2), ({}, {}, {1: sign, 2: 4 * sign, 3: -sign})]
            reader = self.reader(rows, wires=4, outputs=2, private=1)
            proof = self.assert_sound_by_enumeration(reader)
            self.assertTrue(proof['output_unique'])
            self.assertTrue(proof['signal_unique'])

    def test_known_product_can_determine_an_injective_boolean_tuple(self):
        reader = self.reader([boolean(1), boolean(2), ({3: 1}, {4: 1}, {1: 1, 2: 4})],
                             wires=5, outputs=2, private=2)
        self.assertTrue(self.assert_sound_by_enumeration(reader)['signal_unique'])

    def test_wrapping_bit_sum_does_not_prove_uniqueness(self):
        rows = [boolean(wire) for wire in (1, 2, 3)]
        rows.append(({}, {}, {1: 1, 2: 2, 3: 4}))
        reader = self.reader(rows, wires=4, outputs=3)
        proof = self.assert_sound_by_enumeration(reader)
        self.assertFalse(proof['output_unique'])
        self.assertFalse(proof['signal_unique'])

    def test_duplicate_weights_do_not_identify_each_bit(self):
        reader = self.reader([boolean(1), boolean(2), ({}, {}, {0: -1, 1: 1, 2: 1})],
                             wires=3, outputs=2)
        self.assertFalse(self.assert_sound_by_enumeration(reader)['output_unique'])

    def test_binary_coefficients_alone_do_not_prove_boolean_values(self):
        reader = self.reader([({}, {}, {1: 1, 2: 2, 3: -1})], wires=4, outputs=2, private=1)
        self.assertFalse(self.assert_sound_by_enumeration(reader)['output_unique'])

    def test_boolean_proof_requires_exact_roots_zero_and_one(self):
        reader = self.reader([({1: 1}, {1: 1}, {1: 2}),
                              ({2: 1}, {2: 1}, {2: 2}),
                              ({}, {}, {1: 1, 2: 2, 3: -1})],
                             wires=4, outputs=2, private=1)
        self.assertFalse(self.assert_sound_by_enumeration(reader)['output_unique'])

    def test_empty_constraints_distinguish_free_outputs_from_inputs(self):
        self.assertFalse(self.assert_sound_by_enumeration(self.reader([]))['output_unique'])
        inputs_only = self.reader([], wires=3, outputs=0, public=1, private=1)
        proof = self.assert_sound_by_enumeration(inputs_only)
        self.assertTrue(proof['output_unique'])
        self.assertTrue(proof['signal_unique'])

    def test_reverse_dependency_chain_reaches_all_signals(self):
        rows = [({wire: 1}, {0: 1}, {wire + 1: 1, 0: 1}) for wire in range(1, 90)]
        rows.append(({90: 1}, {0: 1}, {0: 2}))
        proof = prove_uniqueness(self.reader(rows, wires=91), max_work=5000)
        self.assertTrue(proof['signal_unique'])
        self.assertEqual(proof['known_count'], 91)

    def test_invalid_layout_unknown_field_and_size_limits_produce_no_proof(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 2})], private=3)
        self.assertFalse(prove_uniqueness(reader)['output_unique'])
        for prime in (15, 2**61 - 1):
            reader = self.reader([({1: 1}, {0: 1}, {0: 2})], prime=prime)
            self.assertEqual(prove_uniqueness(reader)['reason'], 'unverified_field')
        reader = self.reader([({1: 1}, {0: 1}, {0: 2})])
        reader.size = 17 * 1024 * 1024
        self.assertEqual(prove_uniqueness(reader)['reason'], 'size_limit')

    def test_limits_during_construction_do_not_return_a_partial_proof(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 2})])
        for options in ({'timeout_seconds': 0}, {'max_terms': 0}, {'max_work': 0}):
            proof = prove_uniqueness(reader, **options)
            self.assertFalse(proof['output_unique'])
            self.assertFalse(proof['signal_unique'])
        reader.metadata['constraints'] += 1
        self.assertEqual(prove_uniqueness(reader)['reason'], 'incomplete_constraint_scan')

    def test_cancellation_propagates(self):
        reader = self.reader([({1: 1}, {0: 1}, {0: 2})])
        calls = 0

        def checkpoint():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise InterruptedError('cancelled')

        with self.assertRaisesRegex(InterruptedError, 'cancelled'):
            prove_uniqueness(reader, checkpoint)

    def test_generated_small_prime_systems_have_no_counterexamples_to_positive_proofs(self):
        rng = random.Random(349821)
        for prime in (3, 5, 7):
            for case in range(40):
                rows = [tuple({wire: rng.randrange(prime) for wire in range(4) if rng.random() < 0.45}
                              for _ in range(3)) for _ in range(rng.randrange(4))]
                with self.subTest(prime=prime, case=case):
                    self.assert_sound_by_enumeration(self.reader(rows, prime=prime, wires=4, private=1))

    def test_real_alias_and_strict_bit_number_circuits_are_fully_proved(self):
        names = ('circomlib-cff5ab6__AliasCheck@aliascheck.r1cs',
                 'circomlib-cff5ab6__Bits2Num_strict@bitify.r1cs')
        if not all((ROOT / 'benchmarks/r1cs' / name).is_file() for name in names):
            self.skipTest('requires strict bit benchmark fixtures')
        for name in names:
            with self.subTest(name=name):
                reader = R1CSFile(ROOT / 'benchmarks/r1cs' / name)
                proof = prove_uniqueness(reader)
                self.assertTrue(proof['output_unique'])
                self.assertTrue(proof['signal_unique'])
                self.assertEqual(proof['known_count'], reader.wires)


if __name__ == '__main__':
    unittest.main()
