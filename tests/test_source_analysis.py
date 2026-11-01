"""Source results remain explicit when analysis cannot reach a conclusion."""

from pathlib import Path
import copy
import json
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))

from cirverify.source_report import (SOURCE_CHECKS, build_source_report,
                                     incomplete_source_report, summarize_source_analysis)
from web_ui.source_analysis import analyze_source, _valid_report
from web_ui import source_analysis

VALID = 'template T() { signal input a; signal output b; b <== a; } component main = T();'
WARNING = 'template T() { signal input a; signal output b; b <-- a; } component main = T();'


class ReportTests(unittest.TestCase):
    def test_missing_check_is_unknown_and_keeps_completed_checks(self):
        graphs = {'main': SimpleNamespace(name='main')}
        reports = {'main': {key: [] for key, _ in SOURCE_CHECKS[:-1]}}
        result = build_source_report(graphs, reports)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['verdict'], 'unknown')
        self.assertIsNone(result['warning_count'])
        self.assertEqual(result['checks'][-1]['status'], 'unknown')
        self.assertEqual(result['checks'][0]['status'], 'pass')
        self.assertNotIn('Total warnings: 0', summarize_source_analysis(result))

    def test_every_failure_has_five_explicit_uncompleted_checks(self):
        for reason in ('timeout', 'cancelled', 'no_main', 'include_not_found', 'worker_error'):
            with self.subTest(reason=reason):
                result = incomplete_source_report(reason, diagnostics=['specific cause'])
                self.assertEqual(len(result['checks']), 5)
                self.assertIsNone(result['warning_count'])
                self.assertTrue(all(check['status'] in ('unknown', 'skipped') for check in result['checks']))
                self.assertEqual(result['diagnostics'], ['specific cause'])

    def test_protocol_rejects_false_safe_and_inconsistent_counts(self):
        graphs = {'main': SimpleNamespace(name='main')}
        reports = {'main': {key: [] for key, _ in SOURCE_CHECKS}}
        baseline = build_source_report(graphs, reports, elapsed_seconds=0.1)
        self.assertTrue(_valid_report(baseline))
        mutations = [
            lambda r: r.update(warning_count=-1),
            lambda r: r.update(warning_count=False),
            lambda r: r.update(warning_count=1),
            lambda r: r.update(elapsed_seconds=float('nan')),
            lambda r: r.update(diagnostics='not a list'),
            lambda r: r['checks'][0].update(status='warning'),
            lambda r: r['checks'][0].update(count=1, findings=[{'location': 'source:1:0', 'message': 'finding'}]),
            lambda r: r['checks'][0].update(status='warning', count=1,
                                           findings=[{'location': 'source:1:0', 'message': 'finding'}]),
        ]
        for mutation in mutations:
            candidate = copy.deepcopy(baseline)
            mutation(candidate)
            self.assertFalse(_valid_report(candidate), candidate)
        incomplete = incomplete_source_report('timeout')
        self.assertTrue(_valid_report(incomplete))
        for field in ('reason', 'message', 'elapsed_seconds'):
            candidate = copy.deepcopy(incomplete)
            del candidate[field]
            self.assertFalse(_valid_report(candidate), field)


class IsolatedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'circuit.circom'

    def analyze(self, code, **kwargs):
        self.source.write_text(code, encoding='utf-8')
        return analyze_source(self.source, filename='circuit.circom', **kwargs)

    def test_actual_success_and_findings(self):
        result = self.analyze(VALID)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['verdict'], 'safe')
        self.assertEqual(result['warning_count'], 0)
        self.assertEqual(result['filename'], 'circuit.circom')
        result = self.analyze(WARNING)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['verdict'], 'warning')
        self.assertGreater(result['warning_count'], 0)

    def test_missing_dependency_syntax_and_library_remain_distinct(self):
        cases = [
            ('include "missing.circom"; ' + VALID, 'include_not_found', 'incomplete'),
            ('template Bad( {', 'syntax_error', 'incomplete'),
            ('@ ' + VALID, 'syntax_error', 'incomplete'),
            ('template Library() { signal input a; }', 'no_main', 'not_applicable'),
        ]
        for code, reason, status in cases:
            with self.subTest(reason=reason):
                result = self.analyze(code)
                self.assertEqual(result['reason'], reason, result)
                self.assertEqual(result['status'], status)
                self.assertIsNone(result['warning_count'])
                self.assertTrue(result['diagnostics'])

    def test_real_timeout_and_cancellation_clean_up_worker(self):
        worker = self.root / 'slow_worker.py'
        worker.write_text('import time\ntime.sleep(60)\n', encoding='utf-8')
        self.source.write_text(VALID, encoding='utf-8')
        processes, temporary_paths = [], []
        original_spawn = source_analysis.subprocess.Popen
        original_directory = source_analysis.tempfile.TemporaryDirectory
        def record_spawn(*args, **kwargs):
            process = original_spawn(*args, **kwargs)
            processes.append(process)
            return process
        def record_directory(*args, **kwargs):
            directory = original_directory(*args, dir=self.root, **kwargs)
            temporary_paths.append(Path(directory.name))
            return directory
        with patch.object(source_analysis, 'WORKER', worker), \
                patch.object(source_analysis.subprocess, 'Popen', side_effect=record_spawn), \
                patch.object(source_analysis.tempfile, 'TemporaryDirectory', side_effect=record_directory):
            started = time.monotonic()
            result = analyze_source(self.source, timeout_seconds=0.2)
            self.assertEqual(result['reason'], 'timeout', result)
            self.assertLess(time.monotonic() - started, 8)
            cancelled = threading.Event()
            timer = threading.Timer(0.3, cancelled.set)
            timer.start()
            try:
                result = analyze_source(self.source, cancelled=cancelled.is_set)
            finally:
                timer.cancel()
            self.assertEqual(result['status'], 'cancelled', result)
        self.assertEqual(len(processes), 2)
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertTrue(all(not path.exists() for path in temporary_paths))
        # A failed worker cannot poison the following analysis process.
        self.assertEqual(analyze_source(self.source)['status'], 'completed')

    def test_crash_bad_protocol_and_missing_file_are_results(self):
        self.source.write_text(VALID, encoding='utf-8')
        worker = self.root / 'bad_worker.py'
        scripts = ['raise RuntimeError("worker crash")\n',
                   'import pathlib,sys\npathlib.Path(sys.argv[2]).write_text("{}")\n']
        for script in scripts:
            worker.write_text(script, encoding='utf-8')
            with patch.object(source_analysis, 'WORKER', worker):
                result = analyze_source(self.source)
            self.assertEqual(result['reason'], 'worker_error', result)
            self.assertIsNone(result['warning_count'])
            self.assertTrue(result['diagnostics'])
        result = analyze_source(self.root / 'missing.circom')
        self.assertEqual(result['status'], 'incomplete')

    def test_real_worker_with_contradictory_safe_report_is_rejected(self):
        self.source.write_text(VALID, encoding='utf-8')
        graphs = {'main': SimpleNamespace(name='main')}
        reports = {'main': {key: [] for key, _ in SOURCE_CHECKS}}
        candidate = build_source_report(graphs, reports, elapsed_seconds=0.1)
        candidate['checks'][0].update(status='warning', count=1,
                                     findings=[{'location': 'source:1:0', 'message': 'finding'}])
        worker = self.root / 'contradictory_worker.py'
        worker.write_text('import pathlib,sys\npathlib.Path(sys.argv[2]).write_text('
                          + repr(json.dumps(candidate)) + ', encoding="utf-8")\n', encoding='utf-8')
        with patch.object(source_analysis, 'WORKER', worker):
            result = analyze_source(self.source)
        self.assertEqual(result['verdict'], 'unknown')
        self.assertEqual(result['reason'], 'worker_error')

    def test_cancelled_before_spawn_and_invalid_deadline(self):
        result = analyze_source(self.root / 'missing.circom', cancelled=lambda: True)
        self.assertEqual(result['status'], 'cancelled')
        for timeout in (0, -1, float('inf'), float('nan')):
            result = analyze_source(self.source, timeout_seconds=timeout)
            self.assertEqual(result['reason'], 'configuration_error')


if __name__ == '__main__':
    unittest.main()
