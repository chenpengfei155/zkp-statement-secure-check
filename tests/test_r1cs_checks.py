from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui.r1cs_checks import prepare_checks, aggregate, CHECK_NAMES, check_result
from web_ui.picus_worker import SatisfiabilityOutput
from picus_fixtures import r1cs


class StructuralTests(unittest.TestCase):
    def scan(self, rows, **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'circuit.r1cs'
            path.write_bytes(r1cs(rows, wires=4, prime=17))
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

    def test_modular_cancellation_and_duplicate_equations(self):
        checks, _ = self.scan([
            ({1: 16}, {0: 16}, {1: 1}),  # -w1 * -1 - w1 = 0 (mod 17)
            ({1: 1, 2: 1}, {0: 1}, {2: 1}),
            ({0: 1}, {1: 1}, {}),  # same polynomial as row 1
            ({1: 1}, {0: 1}, {0: 1}),  # w1 - 1 differs
        ])
        self.assertEqual(checks['trivial_constraints']['findings'], [{'constraint': 0}])
        self.assertEqual(checks['duplicate_constraints']['findings'], [{'constraint': 2, 'duplicate_of': 1}])

    def test_budget_never_claims_complete_or_invents_unused_wires(self):
        checks, _ = self.scan([({1: 1, 2: 1}, {2: 1, 3: 1}, {})], row_limit=0)
        self.assertTrue(all(item['status'] == 'unknown' for item in checks.values()))
        self.assertEqual(checks['unused_wires']['count'], 0)
        checks, _ = self.scan([({1: 1}, {0: 1}, {})], index_limit=0)
        self.assertEqual(checks['duplicate_constraints']['status'], 'unknown')

    def test_finding_display_cap_preserves_counts(self):
        checks, _ = self.scan([({}, {}, {})] * 105)
        self.assertEqual(checks['trivial_constraints']['count'], 105)
        self.assertEqual(len(checks['trivial_constraints']['findings']), 100)
        self.assertTrue(checks['trivial_constraints']['truncated'])
        self.assertEqual(checks['duplicate_constraints']['count'], 104)

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
