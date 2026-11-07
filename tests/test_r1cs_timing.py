"""Budget sampling and real supervisor stop/measurement regressions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from web_ui import picus
from web_ui.picus import PicusConfig, PicusEngine, empty_report
from web_ui.r1cs_timing import analysis_budget


class BudgetTests(unittest.TestCase):
    def test_defaults_sample_real_budget_in_range(self):
        config = PicusConfig()
        samples = [analysis_budget(config.timeout_seconds) for _ in range(100)]
        self.assertTrue(all(180 <= budget <= 195 for budget in samples))
        self.assertGreater(len(set(samples)), 1)

    def test_large_override_is_capped_and_short_explicit_limit_preserved(self):
        for requested in (195, 200, 240, 1200):
            with patch('web_ui.r1cs_timing.random.uniform', side_effect=lambda low, high: high) as sample:
                self.assertEqual(analysis_budget(requested), 195)
                sample.assert_called_once_with(180, 195)
        for requested in (0.01, 0.35, 5, 15):
            with patch('web_ui.r1cs_timing.random.uniform') as sample:
                self.assertEqual(analysis_budget(requested), requested)
                sample.assert_not_called()
        for requested in (0, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                analysis_budget(requested)


class SupervisorTimingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.reader = SimpleNamespace(path=self.directory / 'dummy.r1cs')

    def command(self, code):
        script = self.directory / 'worker.py'
        script.write_text(code, encoding='utf-8')
        return [sys.executable, str(script)]

    def test_fast_completion_is_not_delayed_or_given_a_random_elapsed_time(self):
        report = empty_report('safe', 'completed')
        for item in report['checks']:
            item.update(status='pass', reason='completed')
        report['elapsed_seconds'] = 99999  # parent must use its own actual clock
        command = self.command("print(" + repr(json.dumps({'type': 'result', 'report': report})) + ")")
        engine = PicusEngine(PicusConfig(timeout_seconds=240))
        with patch.object(engine, 'command', return_value=command), \
                patch('web_ui.r1cs_timing.random.uniform', return_value=191.5) as sample:
            started = time.monotonic()
            result = engine.analyze(self.reader, cancelled=lambda: False, progress=lambda _: None)
            elapsed = time.monotonic() - started
        self.assertEqual(result['verdict'], 'safe')
        self.assertEqual(result['analysis_budget_seconds'], 191.5)
        self.assertEqual(result['total_budget_seconds'], 200)
        self.assertLess(elapsed, 5)
        self.assertAlmostEqual(result['elapsed_seconds'], elapsed, delta=0.05)
        sample.assert_called_once()

    def test_supervisor_that_ignores_cancel_is_killed_and_reaped(self):
        engine = PicusEngine(PicusConfig(timeout_seconds=0.05))
        command = self.command('import time\ntime.sleep(30)\n')
        children = []
        real_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch.object(engine, 'command', return_value=command), \
                patch.object(picus.subprocess, 'Popen', side_effect=spawn):
            started = time.monotonic()
            report = engine.analyze(self.reader, cancelled=lambda: False, progress=lambda _: None)
            elapsed = time.monotonic() - started
        self.assertEqual((report['verdict'], report['reason']), ('unknown', 'timeout'))
        self.assertTrue(all(item['status'] == 'unknown' for item in report['checks']))
        self.assertLess(elapsed, 4)
        self.assertAlmostEqual(report['elapsed_seconds'], elapsed, delta=0.05)
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_total_watchdog_still_wins_over_an_oversized_analysis_budget(self):
        engine = PicusEngine(PicusConfig(timeout_seconds=1200))
        command = self.command('import time\ntime.sleep(30)\n')
        with patch.object(engine, 'command', return_value=command), \
                patch.object(picus, 'MAX_TASK_SECONDS', 2.5), \
                patch.object(picus, 'CLEANUP_SECONDS', 0.25), \
                patch.object(picus, 'analysis_budget', return_value=195):
            started = time.monotonic()
            report = engine.analyze(self.reader, cancelled=lambda: False, progress=lambda _: None)
            elapsed = time.monotonic() - started
        self.assertEqual((report['verdict'], report['reason']), ('unknown', 'timeout'))
        self.assertLess(elapsed, 2.5)


if __name__ == '__main__':
    unittest.main()
