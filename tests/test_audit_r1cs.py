"""Audit orchestration tests; no installed solver or benchmark is executed."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
spec = importlib.util.spec_from_file_location('audit_r1cs', ROOT / 'scripts/audit_r1cs.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
from web_ui.picus import empty_report
from web_ui.r1cs_checks import CHECK_NAMES, aggregate, check_result


def determinate_report():
    checks = [check_result(key, 'pass', 'Verified by test fixture.') for key in CHECK_NAMES]
    return {'engine': 'picus', 'verdict': aggregate(checks)[0], 'reason': 'completed', 'checks': checks}


class AuditR1CSTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.output = self.root / 'audit'
        self.sample = self.inputs / 'test.r1cs'
        self.sample.write_bytes(b'fixture, never parsed by this stubbed test')
        self.calls = 0
        self.code = 'code-v1'
        self.runtime = 'runtime-v1'
        self.report = determinate_report()
        self.addCleanup(patch.stopall)
        patch.object(audit, 'implementation_hash', side_effect=lambda: self.code).start()
        patch.object(audit, 'runtime_hash', side_effect=lambda *_: self.runtime).start()
        patch.object(audit, 'run_file', side_effect=self.run_file).start()

    def run_file(self, path, attempt, stop, config, worker_timeout):
        self.calls += 1
        return {'status': audit.report_status(self.report), 'stage': 'finished',
                'reason': self.report['reason'], 'report': self.report.copy()}

    def run_cli(self, *extra):
        with redirect_stdout(io.StringIO()):
            status = audit.main(['--r1cs', str(self.inputs), '--output', str(self.output), *extra])
        return status, json.loads((self.output / 'summary.json').read_text(encoding='utf-8'))

    def test_resume_fingerprints_input_implementation_runtime_and_config(self):
        status, summary = self.run_cli()
        self.assertEqual(status, 0)
        self.assertTrue(summary['all_checks_complete'])
        self.assertEqual(self.calls, 1)
        _, summary = self.run_cli()
        self.assertEqual(summary['reused'], 1)
        self.assertEqual(self.calls, 1)
        self.sample.write_bytes(b'changed input')
        self.run_cli()
        self.assertEqual(self.calls, 2)
        self.code = 'code-v2'
        self.run_cli()
        self.assertEqual(self.calls, 3)
        self.runtime = 'runtime-v2'
        self.run_cli()
        self.assertEqual(self.calls, 4)
        self.run_cli('--query-timeout-ms', '4000')
        self.assertEqual(self.calls, 5)
        # Every replacement preserves its predecessor's actual report.
        predecessors = list((self.output / 'attempts').glob('*/previous-report.json'))
        self.assertEqual(len(predecessors), 4)
        self.assertTrue(all(json.loads(path.read_text())['audit']['identity'] for path in predecessors))

    def test_incomplete_is_reported_and_retry_is_explicit(self):
        self.report = empty_report('unknown', 'timeout')
        _, summary = self.run_cli()
        self.assertFalse(summary['all_checks_complete'])
        self.assertEqual(summary['counts_by_status'], {'incomplete': 1})
        self.assertEqual(summary['counts_by_reason'], {'timeout': 1})
        self.run_cli()
        self.assertEqual(self.calls, 1)
        self.run_cli('--retry-incomplete')
        self.assertEqual(self.calls, 2)

    def test_stop_marker_and_changed_during_run_never_reuse_a_pass(self):
        self.output.mkdir()
        marker = self.output / 'stop.request'
        marker.touch()
        status, summary = self.run_cli()
        self.assertEqual(status, 2)
        self.assertEqual(summary['processed'], 0)
        self.assertEqual(self.calls, 0)
        marker.unlink()
        original = self.run_file

        def changing_run(*args):
            result = original(*args)
            self.sample.write_bytes(self.sample.read_bytes() + b' changed')
            return result

        with patch.object(audit, 'run_file', side_effect=changing_run):
            _, summary = self.run_cli()
        self.assertEqual(summary['counts_by_reason'], {'changed_during_run': 1})
        self.assertFalse(summary['all_checks_complete'])
        self.run_cli()
        self.assertEqual(self.calls, 2)

    def test_legitimate_skips_and_partial_findings(self):
        report = determinate_report()
        report['checks'][1] = check_result('output_uniqueness', 'skipped', 'No outputs.', reason='no_outputs')
        report['verdict'] = aggregate(report['checks'], True)[0]
        self.assertEqual(audit.report_status(report), 'completed')
        report['checks'][1] = check_result('output_uniqueness', 'fail', 'Counterexample.')
        report['checks'][2] = check_result('signal_uniqueness', 'unknown', 'Unknown.', reason='timeout')
        report['verdict'] = aggregate(report['checks'])[0]
        self.assertEqual(audit.report_status(report), 'incomplete')
        self.assertEqual(audit.report_status({'verdict': 'safe', 'checks': []}), 'incomplete')

    def test_partial_warning_or_failure_is_not_a_conclusive_audit(self):
        for status in ('warning', 'fail', 'pass'):
            for details in ({'complete': False, 'reason': 'completed'},
                            {'reason': 'scan_limit'}, {'reason': 'not_started'}):
                with self.subTest(status=status, details=details):
                    report = determinate_report()
                    report['checks'][3] = check_result('unused_wires', status,
                        'Finding from an incomplete structural scan.', **details)
                    report['verdict'] = aggregate(report['checks'])[0]
                    self.assertEqual(audit.report_status(report), 'incomplete')
        self.report = determinate_report()
        self.report['checks'][3] = check_result('unused_wires', 'warning',
            'Some unused wires found, scan incomplete.', complete=False, reason='scan_limit')
        self.report['verdict'], self.report['reason'] = aggregate(self.report['checks'])
        _, summary = self.run_cli()
        self.assertFalse(summary['all_checks_complete'])
        self.assertEqual(summary['counts_by_status'], {'incomplete': 1})

    @unittest.skipUnless(os.name == 'nt', 'requires Windows Job Objects')
    def test_real_worker_returns_validation_failure_without_starting_a_solver(self):
        # This is deliberately not an R1CS payload: validation fails before the
        # engine is constructed, even though --home has no installed runtime.
        command = [sys.executable, '-X', 'utf8', str(ROOT / 'scripts/audit_r1cs.py'),
                   '--r1cs', str(self.inputs), '--output', str(self.output),
                   '--home', str(self.root / 'empty-runtime'), '--worker-timeout', '10']
        result = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads((self.output / 'summary.json').read_text())
        self.assertEqual(summary['counts_by_reason'], {'validation_error': 1})
        self.assertFalse(summary['all_checks_complete'])
        report = json.loads(Path(summary['records'][0]['report_path']).read_text())
        self.assertEqual(report['stage'], 'validation')
        self.assertTrue(report['audit']['identity']['input_sha256'])
        self.assertFalse(report['audit']['changed_during_run'])
        attempt = Path(report['audit']['attempt_path'])
        self.assertTrue((attempt / 'result.json').is_file())
        self.assertTrue((attempt / 'stderr.log').is_file())


if __name__ == '__main__':
    unittest.main()
