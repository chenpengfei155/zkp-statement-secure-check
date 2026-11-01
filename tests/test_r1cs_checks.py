from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui.r1cs_checks import prepare_checks, aggregate, CHECK_NAMES, check_result
from web_ui.picus_worker import SatisfiabilityOutput
from picus_fixtures import BN254, r1cs


class StructuralTests(unittest.TestCase):
    def scan(self, rows, *, prime=17, **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'circuit.r1cs'
            path.write_bytes(r1cs(rows, wires=4, prime=prime))
            query = Path(directory) / 'query.smt2'
            checks = prepare_checks(R1CSFile(path), query, **options)
            return {item['id']: item for item in checks}, query.read_text()

    def test_effective_variables_ignore_zero_factors_and_cancellation(self):
        checks, query = self.scan([
            ({1: 1, 2: 1}, {0: 1}, {2: 1}),  # w2 cancels: w1 = 0
            ({3: 1}, {}, {}),  # w3 * 0 = 0
        ])
        self.assertEqual(checks['unused_wires']['findings'], [{'wire': 2}, {'wire': 3}])
        self.assertEqual(checks['trivial_constraints']['findings'], [{'constraint': 1}])
        self.assertNotIn('(declare-fun w0', query)
        self.assertIn('(_ FiniteField 17)', query)
        self.assertIn('(check-sat)', query)

    def test_modular_cancellation(self):
        checks, _ = self.scan([
            ({1: 16}, {0: 16}, {1: 1}),  # -w1 * -1 - w1 = 0 (mod 17)
            ({1: 1, 2: 1}, {0: 1}, {2: 1}),
            ({1: 1}, {0: 1}, {0: 1}),  # w1 - 1 differs
        ])
        self.assertEqual(checks['trivial_constraints']['findings'], [{'constraint': 0}])

    def test_budget_never_claims_complete_or_invents_unused_wires(self):
        checks, _ = self.scan([({1: 1, 2: 1}, {2: 1, 3: 1}, {})], row_limit=0)
        for key in ('unused_wires', 'trivial_constraints'):
            self.assertEqual(checks[key]['status'], 'unknown')
            self.assertFalse(checks[key]['complete'])
        self.assertEqual(checks['unused_wires']['count'], 0)
        self.assertEqual(checks['satisfiability']['status'], 'pass')

    def test_zero_assignment_is_a_complete_concrete_witness(self):
        rows = [({1: 1}, {0: 1}, {}), ({2: 1}, {3: 1}, {})]
        checks, _ = self.scan(rows)
        sat = checks['satisfiability']
        self.assertEqual(sat['status'], 'pass')
        self.assertEqual(sat['method'], 'verified_zero_assignment')
        self.assertEqual(sat['witness'],
                         {'w0': '1', 'other_wires': '0', 'constraints_verified': len(rows)})

    def test_zero_assignment_keeps_constant_wire_equal_to_one(self):
        for row in (
            ({0: 1}, {0: 1}, {}),  # 1 * 1 = 0 is inconsistent.
            ({1: 1}, {0: 1}, {0: 1}),  # out = 1 needs a nonzero witness.
        ):
            with self.subTest(row=row):
                checks, _ = self.scan([row])
                # Rejecting this candidate is not an unsatisfiability proof.
                self.assertNotIn('satisfiability', checks)

    def test_zero_assignment_uses_field_arithmetic(self):
        for prime in (17, BN254):
            with self.subTest(prime=prime):
                checks, _ = self.scan([
                    ({0: prime - 1, 1: 1}, {0: prime - 1}, {0: 1}),
                ], prime=prime)  # (-1 + out) * -1 = 1 when out = 0.
                self.assertEqual(checks['satisfiability']['status'], 'pass')
                self.assertEqual(checks['satisfiability']['witness']['constraints_verified'], 1)

    def test_unverified_field_moduli_require_solver_fallback(self):
        # 15 is composite; 2**61 - 1 is prime but outside the known-prime fast path.
        # Neither should acquire a finite-field conclusion from this candidate.
        for prime in (15, 2**61 - 1):
            with self.subTest(prime=prime):
                checks, _ = self.scan([({1: 1}, {0: 1}, {})], prime=prime)
                self.assertNotIn('satisfiability', checks)

    def test_late_conflict_prevents_partial_zero_assignment_pass(self):
        rows = [({1: 1}, {0: 1}, {})] * 128
        rows.append(({0: 1}, {0: 1}, {}))
        for options in ({}, {'row_limit': 0, 'expansion_limit': 0}):
            with self.subTest(options=options):
                checks, _ = self.scan(rows, **options)
                self.assertNotIn('satisfiability', checks)

    def test_structural_expansion_limits_do_not_skip_witness_validation(self):
        rows = [({1: 1, 2: 1}, {2: 1, 3: 1}, {})]
        for options in ({'row_limit': 0}, {'expansion_limit': 0}):
            with self.subTest(options=options):
                checks, _ = self.scan(rows, **options)
                self.assertEqual(checks['satisfiability']['status'], 'pass')
                self.assertEqual(checks['satisfiability']['witness']['constraints_verified'], 1)
                self.assertFalse(checks['trivial_constraints']['complete'])

    def test_witness_requires_scanned_count_to_match_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'circuit.r1cs'
            path.write_bytes(r1cs([({1: 1}, {0: 1}, {})] * 2, prime=17))
            reader = R1CSFile(path)
            for declared in (1, 3):
                with self.subTest(declared=declared):
                    reader.metadata['constraints'] = declared
                    checks = prepare_checks(reader, Path(directory) / 'query.smt2')
                    self.assertNotIn('satisfiability', {item['id'] for item in checks})

    def test_zero_constraints_have_an_assignment(self):
        checks, _ = self.scan([])
        self.assertEqual(checks['satisfiability']['status'], 'pass')
        self.assertEqual(checks['satisfiability']['witness']['constraints_verified'], 0)
        self.assertEqual(checks['unused_wires']['status'], 'warning')

    def test_cancellation_after_valid_prefix_does_not_return_a_witness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'circuit.r1cs'
            path.write_bytes(r1cs([({1: 1}, {0: 1}, {})] * 2, prime=17))
            reader = R1CSFile(path)
            original_rows = reader.iter_constraints
            cancelled = False

            def rows():
                nonlocal cancelled
                for row in original_rows():
                    cancelled = row['index'] == 1
                    yield row

            def checkpoint():
                if cancelled:
                    raise InterruptedError('cancelled')

            reader.iter_constraints = rows
            with self.assertRaisesRegex(InterruptedError, 'cancelled'):
                prepare_checks(reader, Path(directory) / 'query.smt2', checkpoint)

    def test_finding_display_cap_preserves_counts(self):
        checks, _ = self.scan([({}, {}, {})] * 105)
        self.assertEqual(checks['trivial_constraints']['count'], 105)
        self.assertEqual(len(checks['trivial_constraints']['findings']), 100)
        self.assertTrue(checks['trivial_constraints']['truncated'])

    def test_cancel_stops_query_preparation(self):
        def cancelled():
            raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            self.scan([({}, {}, {})], checkpoint=cancelled)


class SatisfiabilityTests(unittest.TestCase):
    def test_only_exact_successful_responses_are_conclusions(self):
        for response, expected in [('sat', 'pass'), ('unsat', 'fail'), ('unknown', 'unknown'),
                                    ('', 'error'), ('sat\nunsat', 'error'), ('(error "failed")', 'error')]:
            parsed = SatisfiabilityOutput()
            for line in response.splitlines():
                parsed.consume(line)
            self.assertEqual(parsed.report(0, 1)['status'], expected)
            self.assertEqual(parsed.report(1, 1)['status'], 'error')
            self.assertEqual(parsed.report(-9, 1, 'timeout')['status'], 'unknown')
            self.assertEqual(parsed.report(-9, 1, 'cancelled')['status'], 'cancelled')

    def test_aggregate_distinguishes_advisories_and_inconsistent_constraints(self):
        checks = {key: check_result(key, 'pass', '') for key in CHECK_NAMES}
        self.assertEqual(aggregate(list(checks.values()))[0], 'safe')
        checks['signal_uniqueness']['status'] = 'warning'
        self.assertEqual(aggregate(list(checks.values()))[0], 'warning')
        checks['satisfiability']['status'] = 'unknown'
        self.assertEqual(aggregate(list(checks.values()))[0], 'unknown')
        checks['output_uniqueness']['status'] = 'fail'
        self.assertEqual(aggregate(list(checks.values()))[0], 'unsafe')
        checks['satisfiability']['status'] = 'fail'
        self.assertEqual(aggregate(list(checks.values()))[0], 'unsatisfiable')


if __name__ == '__main__':
    unittest.main()
