"""Real pinned Picus/cvc5 checks. Set CIRVERIFY_TEST_PICUS=1 after installation."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from cirverify.r1cs import R1CSFile
from web_ui import app as web
from web_ui.picus import PicusConfig, PicusEngine
from web_ui.picus_runtime import picus_command
from web_ui.picus_worker import environment
from web_ui.r1cs_store import R1CSStore
from picus_fixtures import BN254, CONSTANT, SQUARE, r1cs


@unittest.skipUnless(os.getenv('CIRVERIFY_TEST_PICUS') == '1', 'requires the installed pinned Picus engine')
class RealPicusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = PicusEngine()
        status = cls.engine.status(refresh=True)
        if not status['ready']:
            raise AssertionError(status['reason'])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='Picus 中文 path ')
        self.addCleanup(self.directory.cleanup)

    def file(self, data):
        path = Path(self.directory.name) / '电路 test.r1cs'
        path.write_bytes(data)
        return R1CSFile(path)

    def analyze(self, reader, engine=None, cancelled=lambda: False, progress=lambda _: None):
        return (engine or self.engine).analyze(reader, cancelled=cancelled, progress=progress)

    def artifacts(self):
        if os.name == 'nt':
            result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                "@(Get-Process picus,cvc5 -ErrorAction SilentlyContinue | "
                "Select-Object Id,Path) | ConvertTo-Json -Compress"],
                capture_output=True, encoding='utf8', timeout=10)
            # Only inspect descendants belonging to this installation.
            processes = json.loads(result.stdout or '[]')
            if isinstance(processes, dict):
                processes = [processes]
            home = str(Path(self.engine.config.home).resolve()).lower()
            return {'dirs': sorted(str(p) for p in Path(tempfile.gettempdir()).glob('cirverify_picus_*')),
                    'processes': sorted(p['Id'] for p in processes if (p['Path'] or '').lower().startswith(home))}
        result = subprocess.run([sys.executable, '-c',
            'import glob,json,os\n'
            'def owned(p):\n'
            ' try: return os.readlink(p).startswith("/tmp/cirverify_picus_")\n'
            ' except OSError: return False\n'
            'print(json.dumps({"dirs":sorted(glob.glob("/tmp/cirverify_picus_*")),'
            '"processes":[p for p in glob.glob("/proc/[0-9]*/cwd") if owned(p)]}))'],
            capture_output=True, encoding='utf8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_demo_web_matches_original_command(self):
        data = (ROOT / 'demo1.r1cs').read_bytes()
        store = R1CSStore()
        self.addCleanup(store.close)
        with patch.object(web, 'r1cs_store', store), patch.object(web, 'picus_engine', self.engine), patch.object(web, 'maintenance_started', True):
            client = web.app.test_client()
            upload = client.post('/upload', data={'file': (BytesIO(data), 'demo1.r1cs')}).json
            self.assertEqual((upload['metadata']['constraints'], upload['metadata']['wires']), (6, 7))
            response = client.post(f"/r1cs/{upload['id']}/analyze")
            self.assertEqual(response.status_code, 200, response.json)
            stream = client.get('/progress/' + response.json['session_id'], buffered=True).get_data(as_text=True)
            events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith('data: ')]
            report = next(event['result'] for event in events if event['type'] == 'complete')
            self.assertEqual(report['verdict'], 'safe', report)
            self.assertEqual(len(report['checks']), 6)
            self.assertTrue(all(check['status'] == 'pass' for check in report['checks']), report)
            client.delete('/r1cs/' + upload['id'])
        # Run the pinned artifact directly, independently of our supervisor.
        reader = self.file(data)
        prefix = Path(self.engine.config.home).expanduser().resolve()
        probe = subprocess.run(picus_command(prefix) +
            ['--json', '-', '--truncate', 'off', '--solver', 'cvc5', '--timeout', '5000', str(reader.path)],
            env=environment(prefix), capture_output=True, encoding='utf8', timeout=30,
            **self.engine.process_options())
        self.assertEqual(probe.returncode, 8, probe.stdout + probe.stderr)
        self.assertIn('The circuit is properly constrained', probe.stdout)

    def test_square_counterexample_satisfies_original_constraints(self):
        for prime in (BN254, 17):
            with self.subTest(prime=prime):
                report = self.analyze(self.file(r1cs(SQUARE, prime=prime)))
                self.assertEqual(report['verdict'], 'unsafe', report)
                self.assertIsNotNone(report['counterexample'], report)
                row = report['counterexample']['outputs'][0]
                self.assertEqual(row['wire'], 1)
                first, second = int(row['first']), int(row['second'])
                self.assertNotEqual(first, second)
                self.assertEqual(first * first % prime, 1)
                self.assertEqual(second * second % prime, 1)

    def test_other_modulus_section_order_and_zero_constraints(self):
        report = self.analyze(self.file(r1cs(CONSTANT, prime=17, order=(3, 2, 1))))
        self.assertEqual(report['verdict'], 'safe', report)
        report = self.analyze(self.file(r1cs(prime=17)))
        self.assertEqual(report['verdict'], 'unsafe', report)

    def test_internal_ambiguity_with_unique_output_and_valid_counterexample(self):
        # out = input, t^2 = 1. Inputs are fixed, output is unique, t is not.
        rows = [({1: 1}, {0: 1}, {2: 1}), ({3: 1}, {3: 1}, {0: 1})]
        report = self.analyze(self.file(r1cs(rows, prime=17, wires=4, private=1)))
        checks = {item['id']: item for item in report['checks']}
        self.assertEqual(report['verdict'], 'warning', report)
        self.assertEqual(checks['output_uniqueness']['status'], 'pass')
        self.assertEqual(checks['satisfiability']['status'], 'pass')
        self.assertEqual(checks['signal_uniqueness']['status'], 'warning')
        cex = checks['signal_uniqueness']['counterexample']
        self.assertIsNotNone(cex, report)
        internal = next(row for row in cex['internal'] if row['wire'] == 3)
        self.assertNotEqual(internal['first'], internal['second'])
        self.assertTrue(all(int(internal[k]) ** 2 % 17 == 1 for k in ('first', 'second')))
        output = next(row for row in cex['outputs'] if row['wire'] == 1)
        self.assertEqual(output['first'], output['second'])

    def test_unsatisfiable_is_not_a_vacuous_uniqueness_pass(self):
        rows = [({1: 1}, {0: 1}, {}), ({1: 1}, {0: 1}, {0: 1})]
        report = self.analyze(self.file(r1cs(rows, prime=17)))
        checks = {item['id']: item for item in report['checks']}
        self.assertEqual(report['verdict'], 'unsatisfiable', report)
        self.assertEqual(checks['satisfiability']['status'], 'fail')
        self.assertEqual(checks['output_uniqueness']['status'], 'skipped')
        self.assertEqual(checks['signal_uniqueness']['status'], 'skipped')

    def test_no_outputs_still_runs_remaining_checks(self):
        report = self.analyze(self.file(r1cs([({1: 1}, {1: 1}, {0: 1})], wires=2, outputs=0, prime=17)))
        checks = {item['id']: item for item in report['checks']}
        self.assertEqual(checks['output_uniqueness']['status'], 'skipped')
        self.assertEqual(checks['satisfiability']['status'], 'pass')
        self.assertEqual(checks['signal_uniqueness']['status'], 'warning', report)
        report = self.analyze(self.file(r1cs(wires=1, outputs=0, prime=17)))
        self.assertEqual(report['verdict'], 'not_applicable', report)

    def test_structural_advisories_do_not_change_unique_output_to_unsafe(self):
        report = self.analyze(self.file(r1cs(CONSTANT * 2 + [({}, {}, {})], prime=17)))
        checks = {item['id']: item for item in report['checks']}
        self.assertEqual(report['verdict'], 'warning', report)
        self.assertEqual(checks['output_uniqueness']['status'], 'pass')
        self.assertEqual(checks['duplicate_constraints']['count'], 1)
        self.assertEqual(checks['trivial_constraints']['count'], 1)

    def test_cancellation_in_each_solver_stage_reaps_children(self):
        before = self.artifacts()
        for stage in ('Constraint Satisfiability:', 'Output Uniqueness:', 'All-Signal Uniqueness'):
            with self.subTest(stage=stage):
                flag = threading.Event()
                def progress(event):
                    if event['message'].startswith(stage):
                        flag.set()
                report = self.analyze(self.file(r1cs(SQUARE, prime=17)), cancelled=flag.is_set, progress=progress)
                self.assertTrue(flag.is_set(), report)
                self.assertEqual(report['verdict'], 'cancelled', report)
                self.assertEqual(self.artifacts(), before)

    def test_timeout_preserves_completed_satisfiability_and_structural_checks(self):
        before = self.artifacts()
        engine = PicusEngine(PicusConfig(timeout_seconds=0.35))
        report = self.analyze(self.file(r1cs(SQUARE, prime=17)), engine=engine)
        checks = {item['id']: item for item in report['checks']}
        self.assertEqual(report['verdict'], 'unknown', report)
        self.assertEqual(checks['satisfiability']['status'], 'pass', report)
        for key in ('unused_wires', 'trivial_constraints', 'duplicate_constraints'):
            self.assertEqual(checks[key]['status'], 'pass', report)
        self.assertEqual(self.artifacts(), before)

    def test_timeout_cancel_and_parent_eof_reap_processes(self):
        before = self.artifacts()
        reader = self.file(r1cs(SQUARE * 5000))
        tiny = PicusEngine(PicusConfig(timeout_seconds=0.01))
        report = self.analyze(reader, engine=tiny)
        self.assertEqual((report['verdict'], report['reason']), ('unknown', 'timeout'), report)
        flag = threading.Event()
        report = self.analyze(reader, cancelled=flag.is_set, progress=lambda _: flag.set())
        self.assertEqual(report['verdict'], 'cancelled', report)
        process = subprocess.Popen(self.engine.command('run') + ['--file', str(reader.path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, errors = process.communicate(input=b'', timeout=20)  # parent pipe EOF
        self.assertEqual(process.returncode, 0, errors)
        result = [json.loads(line) for line in output.splitlines() if json.loads(line)['type'] == 'result'][0]['report']
        self.assertEqual(result['verdict'], 'cancelled', result)
        self.assertEqual(self.artifacts(), before)

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime isolation')
    def test_without_path_or_external_racket_configuration(self):
        # Absolute Python and runtime paths work even without any external tools.
        with patch.dict(os.environ, PATH='', PLTUSERHOME=self.directory.name,
                        PLTCOLLECTS='nonexistent', PLTCONFIGDIR='nonexistent'):
            self.assertTrue(self.engine.status(refresh=True)['ready'])
            report = self.analyze(self.file(r1cs(SQUARE, prime=BN254)))
        self.assertEqual(report['verdict'], 'unsafe', report)
        self.assertEqual(report['platform'], 'windows-native')

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime installation')
    def test_missing_runtime_is_reported_without_fallback(self):
        engine = PicusEngine(PicusConfig(home=str(Path(self.directory.name) / 'missing')))
        result = engine.status(refresh=True)
        self.assertFalse(result['ready'])
        self.assertEqual(result['platform'], 'windows-native')
        self.assertIn('setup_picus.ps1', result['reason'])


if __name__ == '__main__':
    unittest.main()
