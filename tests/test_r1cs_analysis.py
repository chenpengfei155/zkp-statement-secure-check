"""Regression cases for sound, limited R1CS diagnostics and their web lifecycle."""
from io import BytesIO
import json
from pathlib import Path
import struct
import random
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
from cirverify.r1cs import R1CSFile
from cirverify.r1cs_analysis import analyze_r1cs, AnalysisCancelled
from web_ui import app as web
from web_ui.r1cs_store import R1CSStore


def binary(rows, wires=2, outputs=1, prime=17):
    size = max(8, ((prime.bit_length() + 63) // 64) * 8)
    header = struct.pack('<I', size) + prime.to_bytes(size, 'little')
    header += struct.pack('<IIIIQI', wires, outputs, 0, 0, wires, len(rows))
    constraints = b''
    for row in rows:
        for terms in row:
            constraints += struct.pack('<I', len(terms))
            for wire, coefficient in sorted(terms.items()):
                constraints += struct.pack('<I', wire) + (coefficient % prime).to_bytes(size, 'little')
    sections = [(2, constraints), (3, b''.join(struct.pack('<Q', w) for w in range(wires))), (1, header)]
    return b'r1cs' + struct.pack('<II', 1, len(sections)) + b''.join(
        struct.pack('<IQ', kind, len(data)) + data for kind, data in sections)


class ConstraintChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def analyze(self, rows, *, wires=2, outputs=1, prime=17, **options):
        path = Path(self.directory.name) / 'fixture.r1cs'
        path.write_bytes(binary(rows, wires, outputs, prime))
        return analyze_r1cs(R1CSFile(path), **options)

    def test_demo_and_truthful_source_coverage(self):
        reader = R1CSFile(ROOT / 'demo1.r1cs')
        self.assertEqual(list(reader.iter_constraints()), reader.page()['constraints'])
        report = analyze_r1cs(reader)
        self.assertEqual(report['counts'], {'error': 0, 'warning': 0, 'info': 0})
        self.assertEqual(report['stats']['constraints_scanned'], 6)
        self.assertEqual(len(report['coverage']), 9)
        self.assertEqual(sum(c['status'] == 'unavailable' for c in report['coverage']), 7)
        self.assertIn('No findings does not establish circuit safety', report['limitations'])

    def test_zero_constraints_and_zero_product_do_not_constrain_output(self):
        for rows in ([], [({1: 1}, {}, {})], [({1: 1}, {0: 1}, {1: 1})]):
            with self.subTest(rows=rows):
                report = self.analyze(rows)
                self.assertEqual(report['stats']['unconstrained_outputs'], 1)
                self.assertEqual(report['counts']['warning'], 1)
                self.assertEqual(report['stats']['tautologies'], len(rows))

    def test_cancellation_and_normalization_modulo_file_prime(self):
        for prime in (17, 97, 2**127-1):
            report = self.analyze([({1: 2}, {0: (prime+1)//2}, {1: 1})], prime=prime)
            self.assertEqual(report['stats']['tautologies'], 1)
        with self.assertRaises(AnalysisCancelled):
            self.analyze([({1: 1}, {1: 1}, {})], cancelled=lambda: True)

    def test_constant_and_joint_linear_contradictions(self):
        report = self.analyze([({0: 1}, {0: 1}, {})])
        self.assertEqual(report['findings'][0]['code'], 'impossible_constraint')
        # x+y=1 and 2x+2y=3 are jointly inconsistent, separately satisfiable.
        report = self.analyze([({1: 1, 2: 1}, {0: 1}, {0: 1}),
                               ({1: 2, 2: 2}, {0: 1}, {0: 3})], wires=3)
        self.assertEqual(report['counts']['error'], 1)
        self.assertEqual(report['findings'][0]['code'], 'contradictory_linear_constraints')
        self.assertEqual(report['findings'][0]['constraints'], [0, 1])
        consistent = self.analyze([({1: 1}, {0: 1}, {0: 1}), ({1: 2}, {0: 1}, {0: 2})])
        self.assertEqual(consistent['counts']['error'], 0)

    def test_occurrence_is_not_a_claim_of_output_uniqueness(self):
        # x*x=1 permits both 1 and -1: deliberately outside these limited checks.
        report = self.analyze([({1: 1}, {1: 1}, {0: 1})])
        self.assertEqual(report['counts']['warning'], 0)
        self.assertNotEqual(report['verdict'], 'safe')
        self.assertIn('output-uniqueness', report['limitations'])

    def test_resource_limits_are_inconclusive_and_do_not_invent_absence(self):
        rows = [({1: 1}, {1: 1}, {0: 1})]
        for options in ({'timeout': 0}, {'max_products': 0}, {'max_row_products': 0}):
            with self.subTest(options=options):
                report = self.analyze(rows, **options)
                self.assertEqual(report['status'], 'partial')
                self.assertEqual(report['verdict'], 'inconclusive')
                self.assertEqual(report['counts']['warning'], 0)
        report = self.analyze([({1: 1}, {0: 1}, {})]*2, max_linear_operations=0)
        self.assertEqual(report['status'], 'partial')

    def test_unused_wires_are_informational_and_findings_are_bounded(self):
        report = self.analyze([({1: 1}, {0: 1}, {})], wires=30)
        self.assertEqual(report['counts']['warning'], 0)
        self.assertEqual(report['stats']['unused_other_wires'], 28)
        self.assertEqual(len(report['findings'][0]['wires']), 20)
        self.assertTrue(report['findings'][0]['evidence_truncated'])
        report = self.analyze([({}, {}, {})]*60, max_findings=5)
        self.assertEqual(len(report['findings']), 5)
        self.assertEqual(report['counts']['info'], 60)
        self.assertTrue(report['findings_truncated'])

    def test_claimed_contradictions_and_free_outputs_against_exhaustive_small_fields(self):
        rng = random.Random(20260923)
        for prime in (3, 5, 7):
            for _ in range(30):
                rows = [tuple({w: rng.randrange(prime) for w in range(3)} for _ in range(3))
                        for _ in range(2)]
                report = self.analyze(rows, wires=3, prime=prime)
                solutions = []
                for x in range(prime):
                    for y in range(prime):
                        witness = [1, x, y]
                        def evaluate(terms):
                            return sum(value * witness[wire] for wire, value in terms.items()) % prime
                        if all((evaluate(a) * evaluate(b) - evaluate(c)) % prime == 0 for a, b, c in rows):
                            solutions.append((x, y))
                if report['counts']['error']:
                    self.assertFalse(solutions)
                if report['stats']['unconstrained_outputs'] and solutions:
                    self.assertEqual({x for x, _ in solutions}, set(range(prime)))


class AnalysisWebTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.store = R1CSStore(clock=lambda: self.now)
        self.addCleanup(self.store.close)
        for name, value in [('r1cs_store', self.store), ('maintenance_started', True)]:
            p = patch.object(web, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.client = web.app.test_client()

    def upload(self, content=None):
        result = self.client.post('/upload', data={'file': (
            BytesIO((ROOT / 'demo1.r1cs').read_bytes() if content is None else content), 'demo.r1cs')})
        self.assertEqual(result.status_code, 200)
        return result.json['id']

    def events(self, session):
        result = self.client.get('/progress/' + session, buffered=True)
        return [json.loads(line[6:]) for line in result.get_data(as_text=True).splitlines()
                if line.startswith('data: ')]

    def test_analyze_uploaded_file_and_late_sse_consumption(self):
        file_id = self.upload()
        with patch.object(web, 'detect', side_effect=AssertionError('Must not call source detector')):
            response = self.client.post(f'/r1cs/{file_id}/analyze')
            self.assertEqual(response.status_code, 200)
            session = response.json['session_id']
            worker = web.analysis_threads.get(session)
            if worker:
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
        report = next(event['result'] for event in self.events(session) if event['type'] == 'complete')
        self.assertEqual(report['kind'], 'r1cs')
        self.assertEqual(report['stats']['constraints_scanned'], 6)
        self.assertEqual(len(report['coverage']), 9)
        self.assertNotIn(session, web.progress_queues)
        self.assertFalse(self.store.pins)

    def test_deleted_or_expired_upload_cannot_be_analyzed(self):
        file_id = self.upload()
        self.client.delete('/r1cs/' + file_id)
        self.assertEqual(self.client.post(f'/r1cs/{file_id}/analyze').status_code, 404)
        file_id = self.upload()
        self.now = 1801
        self.assertEqual(self.client.post(f'/r1cs/{file_id}/analyze').status_code, 404)
        self.assertFalse(web.r1cs_analysis_lock.locked())

    def test_cancel_delete_and_concurrent_request_lifecycle(self):
        file_id = self.upload()
        path = self.store.entries[file_id][0].path
        started, finish = threading.Event(), threading.Event()
        def controlled(reader, *, cancelled, progress):
            started.set()
            self.assertTrue(finish.wait(5))
            self.assertTrue(reader.path.exists())
            return analyze_r1cs(reader, cancelled=cancelled, progress=progress)
        with patch.object(web, 'analyze_r1cs', side_effect=controlled):
            session = self.client.post(f'/r1cs/{file_id}/analyze').json['session_id']
            worker = web.analysis_threads[session]
            try:
                self.assertTrue(started.wait(2))
                self.assertEqual(self.client.post(f'/r1cs/{file_id}/analyze').status_code, 409)
                self.now = 1801
                self.store.expire()
                self.assertIn(file_id, self.store.entries)
                self.client.delete('/r1cs/' + file_id)
                self.assertTrue(path.exists())
                self.assertNotIn(file_id, self.store.entries)
                self.client.post('/stop/' + session)
            finally:
                finish.set()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
        events = self.events(session)
        self.assertTrue(any(e['type'] == 'cancelled' for e in events))
        self.assertFalse(any(e['type'] == 'complete' for e in events))
        self.assertFalse(path.exists())
        self.assertFalse(self.store.pins)
        self.assertFalse(web.r1cs_analysis_lock.locked())

    def test_failed_analysis_releases_lease_and_worker(self):
        file_id = self.upload()
        with patch.object(web, 'analyze_r1cs', side_effect=ValueError('failure')), patch.object(web.app.logger, 'exception'):
            session = self.client.post(f'/r1cs/{file_id}/analyze').json['session_id']
            events = self.events(session)
        self.assertTrue(any(e['type'] == 'error' for e in events))
        self.assertFalse(self.store.pins)
        self.assertFalse(web.r1cs_analysis_lock.locked())


if __name__ == '__main__':
    unittest.main()
