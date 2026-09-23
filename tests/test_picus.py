import json
import os
from io import BytesIO
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from web_ui import app as web
from web_ui.picus import PicusEngine, empty_report, valid_checks
from web_ui.picus_worker import PicusOutput, SatisfiabilityOutput, REVISION
from web_ui.r1cs_store import R1CSStore
from picus_fixtures import BN254, CONSTANT, r1cs


class LogTests(unittest.TestCase):
    def test_suite_rejects_missing_or_inconclusive_checks_under_safe_headline(self):
        report = empty_report('safe', 'completed')
        self.assertFalse(valid_checks(report))
        for check in report['checks']:
            check['status'] = 'pass'
        self.assertTrue(valid_checks(report))
        report['checks'][0]['status'] = 'skipped'
        self.assertFalse(valid_checks(report))
        report['checks'][0]['status'] = 'unknown'
        self.assertFalse(valid_checks(report))
        report['verdict'] = 'unknown'
        self.assertTrue(valid_checks(report))
        report['checks'].pop()
        self.assertFalse(valid_checks(report))

    def event(self, output, text, level='INFO'):
        output.consume(json.dumps({'msg': text, 'logger_name': 'picus', 'level': level}))

    def test_exit_codes_require_matching_terminal_log(self):
        for code, message, expected in (
            (8, 'The circuit is properly constrained', 'safe'),
            (9, 'The circuit is underconstrained', 'unsafe'),
            (0, 'Cannot determine whether the circuit is properly constrained', 'unknown')):
            with self.subTest(code=code):
                output = PicusOutput()
                self.event(output, message)
                self.assertEqual(output.report(code, 1)['verdict'], expected)
                self.assertEqual(output.report(1, 1)['verdict'], 'error')
        for code in (0, 8, 9, 1, 10, 50, -9):
            self.assertEqual(PicusOutput().report(code, 0)['verdict'], 'error')

    def test_corrupt_protocol_or_error_is_never_safe(self):
        for malformed in ('not json', '{}', '[]', '{"msg": 5}', 'null'):
            output = PicusOutput()
            output.consume(malformed)
            self.event(output, 'The circuit is properly constrained')
            self.assertEqual(output.report(8, 0)['verdict'], 'error')
        output = PicusOutput()
        self.event(output, 'solver failed', 'ERROR')
        self.event(output, 'The circuit is properly constrained')
        self.assertEqual(output.report(8, 0)['verdict'], 'error')

    def test_counterexample_preserves_big_integers_and_missing_values(self):
        output = PicusOutput()
        for line in ('The circuit is underconstrained', 'inputs:', f'3: {BN254-2}',
                     'first possible outputs:', '1: 1', '2: 5',
                     'second possible outputs:', f'1: {BN254-1}',
                     'first internal variables:', '4: 7', 'Exiting Picus with the code 9'):
            self.event(output, line)
        report = output.report(9, 2)
        self.assertEqual(report['verdict'], 'unsafe')
        self.assertEqual(report['counterexample']['inputs'], [{'wire': 3, 'value': str(BN254-2)}])
        self.assertEqual(report['counterexample']['outputs'][0]['second'], str(BN254-1))
        self.assertIsNone(report['counterexample']['outputs'][1]['second'])

    def test_bad_counterexample_keeps_conclusion_and_logs(self):
        output = PicusOutput()
        for line in ('The circuit is underconstrained', 'first possible outputs:', '1: ???'):
            self.event(output, line)
        report = output.report(9, 1)
        self.assertEqual(report['verdict'], 'unsafe')
        self.assertIsNone(report['counterexample'])
        self.assertTrue(any('1: ???' in line for line in report['logs']))

    def test_cancel_timeout_and_log_caps(self):
        output = PicusOutput()
        for _ in range(1002):
            self.event(output, 'x' * 2000)
        report = output.report(-15, 120, 'timeout')
        self.assertEqual(report['verdict'], 'unknown')
        self.assertTrue(report['logs_truncated'])
        self.assertEqual(len(report['logs']), 1000)
        self.assertEqual(len(report['logs'][0]), 1000)
        self.assertEqual(output.report(-15, 1, 'cancelled')['verdict'], 'cancelled')

    def test_missing_runtime_is_an_install_error(self):
        engine = PicusEngine()
        with patch.object(engine, 'command', side_effect=FileNotFoundError('picus.exe')):
            result = engine.status()
        self.assertFalse(result['ready'])
        self.assertIn('setup_picus.ps1', result['reason'])

    def test_invalid_configuration_does_not_prevent_app_startup(self):
        for value in ('invalid', '-1', 'nan', 'inf'):
            with patch.dict(os.environ, CIRVERIFY_PICUS_TIMEOUT=value):
                engine = PicusEngine()
            self.assertFalse(engine.status()['ready'])
            self.assertIn('configuration', engine.status()['reason'])


class WebPicusTests(unittest.TestCase):
    def setUp(self):
        self.store = R1CSStore()
        self.addCleanup(self.store.close)
        self.engine = Mock()
        self.engine.status.return_value = {'ready': True, 'engine': 'picus', 'revision': REVISION}
        self.engine.analyze.return_value = empty_report('safe', 'completed')
        for name, value in [('r1cs_store', self.store), ('picus_engine', self.engine),
                            ('maintenance_started', True)]:
            p = patch.object(web, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.client = web.app.test_client()

    def upload(self, data=None):
        return self.client.post('/upload', data={'file': (BytesIO(r1cs(CONSTANT) if data is None else data), 'test.r1cs')}).json['id']

    def result(self, session):
        response = self.client.get(f'/progress/{session}', buffered=True)
        events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith('data: ')]
        return next(event['result'] for event in events if event['type'] == 'complete')

    def test_engine_endpoint_and_dispatch_do_not_call_source_analyzer(self):
        self.assertTrue(self.client.get('/r1cs/engine').json['ready'])
        identifier = self.upload()
        with patch.object(web, 'detect') as detect:
            response = self.client.post(f'/r1cs/{identifier}/analyze', json={'command': 'ignored'})
            report = self.result(response.json['session_id'])
        self.assertEqual(report['verdict'], 'safe')
        detect.assert_not_called()
        self.assertFalse(self.store.pins)
        self.assertFalse(web.r1cs_analysis_lock.locked())

    def test_unready_engine_and_missing_upload_release_lock(self):
        self.engine.status.return_value = {'ready': False, 'reason': 'Please install Picus'}
        identifier = self.upload()
        response = self.client.post(f'/r1cs/{identifier}/analyze')
        self.assertEqual(response.status_code, 503)
        self.assertIn('install', response.json['error'])
        self.assertFalse(self.store.pins)
        self.assertFalse(web.r1cs_analysis_lock.locked())
        self.assertEqual(self.client.post('/r1cs/missing/analyze').status_code, 404)

    def test_no_outputs_and_optimized_inputs(self):
        identifier = self.upload(r1cs(wires=1, outputs=0))
        response = self.client.post(f'/r1cs/{identifier}/analyze')
        self.result(response.json['session_id'])
        self.engine.status.assert_called_once()
        self.engine.analyze.assert_called_once()  # satisfiability/strong checks still apply
        identifier = self.upload(r1cs(CONSTANT, private=2))
        response = self.client.post(f'/r1cs/{identifier}/analyze')
        self.assertEqual(response.status_code, 422)
        self.assertIn('--O0', response.json['error'])

    def test_cancel_delete_pin_and_lock_until_worker_finishes(self):
        started, cleanup = threading.Event(), threading.Event()
        def run(reader, *, cancelled, progress):
            started.set()
            cleanup.wait(5)
            self.assertTrue(cancelled())
            self.assertTrue(reader.path.exists())
            return empty_report('cancelled', 'cancelled')
        self.engine.analyze.side_effect = run
        identifier = self.upload()
        path = self.store.entries[identifier][0].path
        session = self.client.post(f'/r1cs/{identifier}/analyze').json['session_id']
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.client.post(f'/r1cs/{identifier}/analyze').status_code, 409)
            self.client.post(f'/stop/{session}')
            self.client.delete(f'/r1cs/{identifier}')
            self.assertTrue(path.exists())
            self.assertTrue(web.r1cs_analysis_lock.locked())
        finally:
            cleanup.set()
        self.assertEqual(self.result(session)['verdict'], 'cancelled')
        self.assertFalse(path.exists())
        self.assertFalse(web.r1cs_analysis_lock.locked())
        self.assertFalse(self.store.pins)

    def test_runtime_failure_reports_error_and_releases_lock(self):
        self.engine.analyze.side_effect = RuntimeError('solver unavailable')
        identifier = self.upload()
        session = self.client.post(f'/r1cs/{identifier}/analyze').json['session_id']
        report = self.result(session)
        self.assertEqual(report['verdict'], 'error')
        self.assertIn('solver unavailable', report['logs'])
        self.assertFalse(web.r1cs_analysis_lock.locked())

    def test_deleted_during_environment_check_never_starts(self):
        identifier = self.upload()
        def checking():
            self.store.delete(identifier)
            return {'ready': True}
        self.engine.status.side_effect = checking
        self.assertEqual(self.client.post(f'/r1cs/{identifier}/analyze').status_code, 404)
        self.engine.analyze.assert_not_called()
        self.assertFalse(self.store.pins)
        self.assertFalse(web.r1cs_analysis_lock.locked())


if __name__ == '__main__':
    unittest.main()
