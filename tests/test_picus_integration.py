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

    def linux(self, *args, **kwargs):
        prefix = ['wsl.exe', '-d', self.engine.config.distro, '--exec'] if os.name == 'nt' else []
        return subprocess.run(prefix + list(args), capture_output=True, encoding='utf8', timeout=150, **kwargs)

    def artifacts(self):
        result = self.linux('python3', '-c',
            'import glob,json,os\n'
            'def owned(p):\n'
            ' try: return os.readlink(p).startswith("/tmp/cirverify_picus_")\n'
            ' except OSError: return False\n'
            'print(json.dumps({"dirs":sorted(glob.glob("/tmp/cirverify_picus_*")),'
            '"processes":[p for p in glob.glob("/proc/[0-9]*/cwd") if owned(p)]}))')
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
            client.delete('/r1cs/' + upload['id'])
        # Run the unmodified upstream entry point independently of our supervisor.
        reader = self.file(data)
        probe = self.linux('python3', '-c',
            'import os,pathlib,subprocess,sys; p=pathlib.Path(sys.argv[1]).expanduser();'
            'e=dict(os.environ,PLTSTDERR="error none@picus",SOLVER_PATH=str(p/"bin/cvc5"));'
            'sys.exit(subprocess.call([str(p/"racket-8.16/bin/racket"),str(p/"Picus/picus.rkt"),'
            '"--json","-","--truncate","off","--solver","cvc5","--timeout","5000",sys.argv[2]],env=e))',
            self.engine.config.home, self.engine.linux_path(reader.path))
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

    def test_timeout_cancel_and_parent_eof_reap_processes(self):
        before = self.artifacts()
        reader = self.file(r1cs(SQUARE * 5000))
        tiny = PicusEngine(PicusConfig(timeout_seconds=0.01))
        report = self.analyze(reader, engine=tiny)
        self.assertEqual((report['verdict'], report['reason']), ('unknown', 'timeout'), report)
        flag = threading.Event()
        report = self.analyze(reader, cancelled=flag.is_set, progress=lambda _: flag.set())
        self.assertEqual(report['verdict'], 'cancelled', report)
        process = subprocess.Popen(self.engine.command('run') + ['--file', self.engine.linux_path(reader.path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, errors = process.communicate(input=b'', timeout=20)  # parent pipe EOF
        self.assertEqual(process.returncode, 0, errors)
        result = [json.loads(line) for line in output.splitlines() if json.loads(line)['type'] == 'result'][0]['report']
        self.assertEqual(result['verdict'], 'cancelled', result)
        self.assertEqual(self.artifacts(), before)


if __name__ == '__main__':
    unittest.main()
