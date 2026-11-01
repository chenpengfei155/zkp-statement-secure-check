"""Worker result preservation tests; no files, child processes or solvers run."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from web_ui import picus_worker as worker
from web_ui.r1cs_checks import check_result


class NoThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class WorkerResultTests(unittest.TestCase):
    def run_worker(self, *, phase, reason='timeout', initial_sat=True, propagated=False,
                   error_type=InterruptedError, expired=False, cancelled=False,
                   outputs=1, native_unsafe=False):
        events = []
        clock = [0.0]
        cancel_event = worker.threading.Event()
        args = SimpleNamespace(home='.tools/picus', file='unused.r1cs', job_dir='unused-job',
                               timeout_seconds=120, query_timeout_ms=5000, memory_mib=4096)
        metadata = {'public_outputs': outputs, 'public_inputs': 0, 'private_inputs': 0,
                    'wires': 2, 'constraints': 0, 'prime': '17'}
        reader = SimpleNamespace(metadata=metadata)
        prepared = [check_result('unused_wires', 'warning', 'Advisory already completed.', reason='completed'),
                    check_result('trivial_constraints', 'pass', 'Already completed.', reason='completed')]
        if initial_sat:
            prepared.append(check_result('satisfiability', 'pass', 'Verified zero witness.',
                                         reason='completed', method='verified_zero_assignment'))

        def interrupt():
            if expired:
                clock[0] = 121.0
            if cancelled:
                cancel_event.set()
            raise error_type(reason)

        def find(*args, **kwargs):
            if phase == 'candidate':
                interrupt()
            if propagated:
                # Empty constraints make this a complete valid assignment.
                return {'values': [1, 0], 'alternative_values': None, 'input_seed': 0,
                        'inputs_seeded': True, 'seeded_input_count': 0,
                        'released_input_wires': [], 'unresolved_fill': 0, 'constraints_verified': 0}
            return None

        def prove(*args, **kwargs):
            if phase == 'proof':
                interrupt()
            return {'output_unique': False, 'signal_unique': False, 'known_count': 1,
                    'reason': 'incomplete_proof'}

        def run_child(*args, **kwargs):
            if phase == 'native_uniqueness':
                interrupt()
            if phase in ('strong_exception', 'strong_oom'):
                parsed = args[1]
                if parsed.strong:
                    if phase == 'strong_exception':
                        interrupt()
                    parsed.consume('out of memory\n', stderr=True)
                    return parsed.report(3, 2.5)
                parsed.consume(json.dumps({'logger_name': 'picus', 'level': 'INFO',
                                           'msg': 'The circuit is underconstrained' if native_unsafe else
                                                  'The circuit is properly constrained'}))
                return parsed.report(9 if native_unsafe else 8, 0.5)
            if phase == 'after_unsat':
                # The complete UNSAT result and legitimate skips are published
                # before the final task checkpoint notices the elapsed deadline.
                clock[0] = 121.0
                return check_result('satisfiability', 'fail', 'Proved inconsistent.', reason='completed')
            raise AssertionError('Unexpected solver-stage request')

        with ExitStack() as stack:
            stack.enter_context(patch.object(worker.threading, 'Thread', NoThread))
            stack.enter_context(patch.object(worker.threading, 'Event', lambda: cancel_event))
            stack.enter_context(patch.object(worker.shutil, 'copyfile', lambda *args: None))
            stack.enter_context(patch.object(worker, 'environment', lambda *args: {}))
            stack.enter_context(patch.object(worker, 'R1CSFile', lambda *args, **kwargs: reader))
            stack.enter_context(patch.object(worker, 'prepare_checks', lambda *args, **kwargs: prepared))
            stack.enter_context(patch.object(worker, 'find_verified_assignment', find))
            stack.enter_context(patch.object(worker, 'prove_uniqueness', prove))
            stack.enter_context(patch.object(worker, 'write_satisfiability_query', lambda *args: None))
            stack.enter_context(patch.object(worker, 'run_child', run_child))
            stack.enter_context(patch.object(worker, 'emit', lambda kind, **data: events.append((kind, data))))
            stack.enter_context(patch.object(worker.time, 'monotonic', lambda: clock[0]))
            # These unit tests exercise the portable coordinator. Avoid native
            # Linux prctl/resource setup when the suite runs off Windows.
            stack.enter_context(patch.object(worker.os, 'name', 'nt'))
            stack.enter_context(patch.object(worker, 'Path', type(ROOT)))
            worker.run(args)
        report = next(data['report'] for kind, data in events if kind == 'result')
        return report, {check['id']: check for check in report['checks']}

    def assert_interrupted_pending_checks(self, report, checks, reason):
        expected = 'unknown' if reason == 'timeout' else 'cancelled'
        self.assertEqual(report['verdict'], expected)
        for key in ('output_uniqueness', 'signal_uniqueness'):
            self.assertEqual(checks[key]['status'], expected)
            self.assertEqual(checks[key]['reason'], reason)
        self.assertEqual(checks['unused_wires']['status'], 'warning')
        self.assertEqual(checks['trivial_constraints']['status'], 'pass')

    def test_completed_zero_sat_survives_optional_candidate_interruptions(self):
        for reason in ('timeout', 'cancelled'):
            with self.subTest(reason=reason):
                report, checks = self.run_worker(phase='candidate', reason=reason)
                self.assert_interrupted_pending_checks(report, checks, reason)
                self.assertEqual(checks['satisfiability']['status'], 'pass')
                self.assertEqual(checks['satisfiability']['method'], 'verified_zero_assignment')

    def test_completed_sat_survives_optional_uniqueness_proof_interruptions(self):
        for propagated in (False, True):
            for reason in ('timeout', 'cancelled'):
                with self.subTest(propagated=propagated, reason=reason):
                    report, checks = self.run_worker(phase='proof', reason=reason,
                                                     initial_sat=not propagated, propagated=propagated)
                    self.assert_interrupted_pending_checks(report, checks, reason)
                    self.assertEqual(checks['satisfiability']['status'], 'pass')
                    self.assertEqual(checks['satisfiability']['method'],
                                     'verified_propagated_assignment' if propagated else 'verified_zero_assignment')

    def test_pending_sat_is_still_marked_interrupted(self):
        for reason in ('timeout', 'cancelled'):
            with self.subTest(reason=reason):
                report, checks = self.run_worker(phase='candidate', reason=reason, initial_sat=False)
                self.assert_interrupted_pending_checks(report, checks, reason)
                self.assertEqual(checks['satisfiability']['status'],
                                 'unknown' if reason == 'timeout' else 'cancelled')

    def test_pending_native_uniqueness_is_still_marked_interrupted(self):
        for reason in ('timeout', 'cancelled'):
            with self.subTest(reason=reason):
                report, checks = self.run_worker(phase='native_uniqueness', reason=reason)
                self.assert_interrupted_pending_checks(report, checks, reason)
                self.assertEqual(checks['satisfiability']['status'], 'pass')

    def test_completed_fail_and_legitimate_skips_survive_a_late_timeout(self):
        report, checks = self.run_worker(phase='after_unsat', initial_sat=False)
        self.assertEqual(report['verdict'], 'unsatisfiable')
        self.assertEqual(checks['satisfiability']['status'], 'fail')
        for key in ('output_uniqueness', 'signal_uniqueness'):
            self.assertEqual(checks[key]['status'], 'skipped')
            self.assertEqual(checks[key]['reason'], 'unsatisfiable')
        self.assertEqual(checks['unused_wires']['status'], 'warning')
        self.assertEqual(checks['trivial_constraints']['status'], 'pass')

    def test_worker_memory_error_preserves_completed_proofs(self):
        for phase in ('candidate', 'proof', 'strong_exception'):
            with self.subTest(phase=phase):
                report, checks = self.run_worker(phase=phase, error_type=MemoryError, reason='allocation failed')
                self.assertEqual(report['verdict'], 'unknown')
                self.assertEqual(report['reason'], 'memory_limit')
                self.assertEqual(checks['satisfiability']['status'], 'pass')
                self.assertEqual(checks['trivial_constraints']['status'], 'pass')
                self.assertEqual(checks['unused_wires']['status'], 'warning')
                self.assertEqual(checks['signal_uniqueness']['reason'], 'memory_limit')
                self.assertIn('memory', checks['signal_uniqueness']['message'])
                self.assertIn('MemoryError: allocation failed', checks['signal_uniqueness']['logs'])
                if phase == 'strong_exception':
                    self.assertEqual(checks['output_uniqueness']['status'], 'pass')

    def test_pending_sat_memory_error_is_unknown_and_control_takes_priority(self):
        for expired, cancelled, expected in ((False, False, 'memory_limit'),
                                             (True, False, 'timeout'),
                                             (True, True, 'cancelled')):
            with self.subTest(expected=expected):
                report, checks = self.run_worker(phase='candidate', error_type=MemoryError,
                    initial_sat=False, expired=expired, cancelled=cancelled)
                self.assertEqual(report['reason'], expected)
                for key in ('satisfiability', 'output_uniqueness', 'signal_uniqueness'):
                    self.assertEqual(checks[key]['reason'], expected)
                    self.assertEqual(checks[key]['status'], 'cancelled' if cancelled else 'unknown')

    def test_native_memory_error_keeps_logs_exit_and_prior_proofs(self):
        report, checks = self.run_worker(phase='strong_oom')
        self.assertEqual((report['verdict'], report['reason']), ('unknown', 'memory_limit'))
        self.assertEqual(checks['satisfiability']['status'], 'pass')
        self.assertEqual(checks['output_uniqueness']['status'], 'pass')
        strong = checks['signal_uniqueness']
        self.assertEqual((strong['status'], strong['reason'], strong['exit_code']), ('unknown', 'memory_limit', 3))
        self.assertEqual(strong['logs'], ['stderr: out of memory'])
        self.assertIn('memory', strong['message'])

    def test_ordinary_worker_exception_remains_an_error(self):
        report, checks = self.run_worker(phase='candidate', error_type=ValueError, reason='out of memory')
        self.assertEqual(report['verdict'], 'error')
        self.assertEqual(checks['signal_uniqueness']['reason'], 'runtime_error')
        self.assertEqual(checks['satisfiability']['status'], 'pass')

    def test_memory_error_preserves_completed_failure_and_legitimate_skip(self):
        report, checks = self.run_worker(phase='strong_exception', error_type=MemoryError, native_unsafe=True)
        self.assertEqual(report['verdict'], 'unsafe')
        self.assertEqual(checks['output_uniqueness']['status'], 'fail')
        self.assertEqual(checks['signal_uniqueness']['reason'], 'memory_limit')
        report, checks = self.run_worker(phase='candidate', error_type=MemoryError, outputs=0)
        self.assertEqual(report['verdict'], 'unknown')
        self.assertEqual(checks['output_uniqueness']['status'], 'skipped')
        self.assertEqual(checks['output_uniqueness']['reason'], 'no_outputs')


class MemoryDiagnosticTests(unittest.TestCase):
    def parsers(self):
        return ((worker.PicusOutput, 'verdict', 3), (worker.SatisfiabilityOutput, 'status', 1))

    def test_explicit_runtime_oom_is_unknown_with_original_evidence(self):
        diagnostics = ('out of memory\n', 'std::bad_alloc\n', '  what():  std::bad_alloc\n',
                       "terminate called after throwing an instance of 'std::bad_alloc'\n")
        for factory, key, code in self.parsers():
            for diagnostic in diagnostics:
                with self.subTest(parser=factory.__name__, diagnostic=diagnostic):
                    parsed = factory()
                    parsed.consume(diagnostic, stderr=True)
                    report = parsed.report(code, 2.75)
                    self.assertEqual((report[key], report['reason']), ('unknown', 'memory_limit'))
                    self.assertEqual(report['exit_code'], code)
                    self.assertEqual(report['elapsed_seconds'], 2.75)
                    self.assertEqual(report['logs'], ['stderr: ' + diagnostic.rstrip()])

    def test_exit_codes_and_mentions_are_not_memory_diagnostics(self):
        for factory, key, _ in self.parsers():
            for diagnostic in ('', 'not out of memory', 'user label: out of memory',
                               'bad_alloc mentioned by a circuit', 'ordinary exception'):
                with self.subTest(parser=factory.__name__, diagnostic=diagnostic):
                    parsed = factory()
                    if diagnostic:
                        parsed.consume(diagnostic, stderr=True)
                    report = parsed.report(3, 1)
                    self.assertEqual(report[key], 'error')
                    self.assertEqual(report['reason'], 'process_error')

    def test_invalid_protocol_is_not_reclassified_as_resource_exhaustion(self):
        for factory, key, code in self.parsers():
            for invalid in ('not a protocol response', 'out of memory'):
                with self.subTest(parser=factory.__name__, invalid=invalid):
                    parsed = factory()
                    parsed.consume(invalid)
                    parsed.consume('out of memory', stderr=True)
                    self.assertEqual(parsed.report(code, 0)[key], 'error')
        parsed = worker.PicusOutput()
        parsed.consume(json.dumps({'logger_name': 'picus', 'level': 'INFO',
                                   'msg': 'The circuit is underconstrained'}))
        parsed.consume('out of memory', stderr=True)
        self.assertEqual(parsed.report(8, 0)['verdict'], 'error')

    def test_cancel_and_timeout_precede_memory_diagnostic(self):
        for factory, key, code in self.parsers():
            for reason, expected in (('cancelled', 'cancelled'), ('timeout', 'unknown')):
                with self.subTest(parser=factory.__name__, reason=reason):
                    parsed = factory()
                    parsed.consume('out of memory', stderr=True)
                    report = parsed.report(code, 1, reason)
                    self.assertEqual((report[key], report['reason']), (expected, reason))

    def test_resource_failure_cannot_become_a_success(self):
        parsed = worker.PicusOutput()
        parsed.consume(json.dumps({'logger_name': 'picus', 'level': 'INFO',
                                   'msg': 'The circuit is properly constrained'}))
        parsed.consume('out of memory', stderr=True)
        self.assertEqual(parsed.report(8, 0)['verdict'], 'unknown')
        parsed = worker.SatisfiabilityOutput()
        parsed.consume('sat')
        parsed.consume('std::bad_alloc', stderr=True)
        self.assertEqual(parsed.report(0, 0)['status'], 'unknown')

    def test_picus_user_message_does_not_trigger_memory_classification(self):
        parsed = worker.PicusOutput()
        parsed.consume(json.dumps({'logger_name': 'picus', 'level': 'ERROR', 'msg': 'out of memory'}))
        self.assertEqual(parsed.report(3, 0)['verdict'], 'error')


if __name__ == '__main__':
    unittest.main()
