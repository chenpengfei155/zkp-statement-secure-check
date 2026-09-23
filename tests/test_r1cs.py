"""Run from the project root: python -m unittest discover -s tests -v."""

from io import BytesIO
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
from cirverify.r1cs import R1CSFile, R1CSError
from web_ui import app as web
from web_ui.r1cs_store import R1CSStore


def circuit(count=1, prime=17, wire=1, coefficient=1, extra=(), version=1,
            declared_count=None, duplicate=False):
    """A tiny independent fixture: w1 * w1 = w1, with sections out of order."""
    fs = 8 if prime < 2**64 else 32
    header = struct.pack('<I', fs) + prime.to_bytes(fs, 'little')
    header += struct.pack('<IIIIQI', 2, 1, 0, 0, 2, count if declared_count is None else declared_count)
    term = struct.pack('<II', 1, wire) + coefficient.to_bytes(fs, 'little')
    sections = [(2, term * 3 * count), (1, header), (3, struct.pack('<QQ', 0, 1)), *extra]
    if duplicate:
        sections.append((1, header))
    return b'r1cs' + struct.pack('<II', version, len(sections)) + b''.join(
        struct.pack('<IQ', kind, len(data)) + data for kind, data in sections)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def read(self, data):
        path = Path(self.directory.name) / 'test.r1cs'
        path.write_bytes(data)
        return R1CSFile(path)

    def test_demo_metadata_and_constraints(self):
        reader = R1CSFile(ROOT / 'demo1.r1cs')
        self.assertEqual([reader.metadata[k] for k in
                          ('constraints', 'wires', 'public_outputs', 'public_inputs', 'private_inputs')],
                         [6, 7, 2, 0, 4])
        items = reader.page()['constraints']
        self.assertEqual(items[0]['a'], [{'wire': 0, 'coefficient': str(reader.prime - 1)},
                                         {'wire': 3, 'coefficient': '1'}])
        self.assertEqual(items[2]['c'][0], {'wire': 1, 'coefficient': str(reader.prime - 1)})

    def test_different_primes_unknown_sections_and_big_integer_precision(self):
        for prime in (17, 18446744069414584321, 2**127 - 1):
            with self.subTest(prime=prime):
                reader = self.read(circuit(prime=prime, coefficient=prime-1, extra=[(99, b'extension')]))
                self.assertEqual(reader.metadata['prime'], str(prime))
                self.assertEqual(reader.page()['constraints'][0]['a'][0]['coefficient'], str(prime-1))

    def test_zero_and_multiple_pages(self):
        self.assertEqual(self.read(circuit(count=0)).page()['constraints'], [])
        reader = self.read(circuit(count=123))
        pages = [reader.page(offset)['constraints'] for offset in (0, 50, 100)]
        self.assertEqual([len(p) for p in pages], [50, 50, 23])
        self.assertEqual([c['index'] for p in pages for c in p], list(range(123)))
        self.assertEqual(reader.page(123)['constraints'], [])

    def test_invalid_files_are_rejected(self):
        data = circuit()
        variants = [b'not a circuit', data[:3], data[:-1], data + b'extra',
                    circuit(version=2), circuit(wire=2), circuit(coefficient=17),
                    circuit(declared_count=2), circuit(declared_count=0), circuit(duplicate=True),
                    circuit(extra=[(4, b'')]), circuit(extra=[(5, b'')])]
        for item in variants:
            with self.subTest(data=item[:30]), self.assertRaises(R1CSError):
                self.read(item)

    def test_section_boundaries_are_validated(self):
        data = bytearray(circuit())
        struct.pack_into('<Q', data, 16, 2**63)
        with self.assertRaises(R1CSError):
            self.read(data)

    def test_page_bounds(self):
        reader = self.read(circuit())
        for offset, limit in ((-1, 50), (0, 0), (0, 51)):
            with self.assertRaises(ValueError):
                reader.page(offset, limit)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.store = R1CSStore(clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.store_patch = patch.object(web, 'r1cs_store', self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.worker_patch = patch.object(web, 'maintenance_started', True)
        self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.client = web.app.test_client()

    def upload(self, data=None, name='test.r1cs'):
        return self.client.post('/upload', data={'file': (BytesIO(circuit() if data is None else data), name)})

    def test_upload_page_delete_and_filename_is_not_a_path(self):
        response = self.upload(name='../../test.r1cs')
        self.assertEqual(response.status_code, 200)
        file_id = response.json['id']
        self.assertEqual(response.json['metadata']['filename'], 'test.r1cs')
        path = self.store.entries[file_id][0].path
        self.assertEqual(path.parent, Path(self.store.directory.name))
        self.assertEqual(self.client.get(f'/r1cs/{file_id}/constraints').json['total'], 1)
        self.assertEqual(self.client.delete(f'/r1cs/{file_id}').status_code, 200)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(f'/r1cs/{file_id}/constraints').status_code, 404)
        self.assertEqual(self.client.delete(f'/r1cs/{file_id}').status_code, 200)

    def test_access_refreshes_expiry_and_cleanup_runs_without_page_access(self):
        file_id = self.upload().json['id']
        path = self.store.entries[file_id][0].path
        self.now = 1799
        self.assertEqual(self.client.get(f'/r1cs/{file_id}/constraints').status_code, 200)
        self.now = 1801
        web.expire_resources()
        self.assertTrue(path.exists())
        self.now = 3600
        web.expire_resources()
        self.assertFalse(path.exists())
        self.assertIn('upload it again', self.client.get(f'/r1cs/{file_id}/constraints').json['error'])

    def test_failed_upload_cleans_its_file(self):
        self.assertEqual(self.upload(b'fake r1cs').status_code, 400)
        self.assertEqual(list(Path(self.store.directory.name).iterdir()), [])
        self.assertFalse(self.store.entries)

    def test_circom_utf8_and_invalid_uploads(self):
        response = self.upload('// 中文\ncomponent main = Example();'.encode(), 'demo.circom')
        self.assertEqual(response.json['kind'], 'circom')
        self.assertIn('中文', response.json['code'])
        for data, name in ((b'abc', 'demo.sym'), (b'\xff', 'bad.circom'), (circuit(), 'fake.circom')):
            self.assertEqual(self.upload(data, name).status_code, 400)
        self.assertEqual(self.client.post('/upload').status_code, 400)

    def test_request_limit_is_json_413(self):
        with patch.dict(web.app.config, MAX_CONTENT_LENGTH=512):
            response = self.upload(b'x' * 1024)
        self.assertEqual(response.status_code, 413)
        self.assertIn('100 MiB', response.json['error'])

    def test_invalid_pagination_and_same_name_upload_isolation(self):
        first = self.upload().json['id']
        second = self.upload(circuit(count=60)).json['id']
        self.assertNotEqual(first, second)
        for query in ('offset=-1', 'limit=0', 'limit=51', 'offset=abc'):
            self.assertEqual(self.client.get(f'/r1cs/{second}/constraints?{query}').status_code, 400)
        self.client.delete(f'/r1cs/{first}')
        self.assertEqual(len(self.client.get(f'/r1cs/{second}/constraints?offset=50').json['constraints']), 10)

    def test_circom_analysis_survives_late_stream_connection_and_cleans_temp(self):
        paths = []
        original = web.detect
        def record(path):
            paths.append(Path(path))
            return original(path)
        with patch.object(web, 'detect', side_effect=record):
            response = self.client.post('/analyze', json={'code': (ROOT / 'demo1.circom').read_text(encoding='utf-8')})
            session_id = response.json['session_id']
            thread = web.analysis_threads.get(session_id)
            if thread:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())
        self.assertIn(session_id, web.progress_queues)
        self.assertTrue(paths)
        self.assertTrue(all(not p.exists() for p in paths))
        response = self.client.get(f'/progress/{session_id}', buffered=True)
        events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith('data: ')]
        self.assertFalse([e for e in events if e['type'] == 'error'])
        result = next(e['result'] for e in events if e['type'] == 'complete')
        self.assertIn('Total warnings: 0', result)
        self.assertNotIn(session_id, web.progress_queues)

    def test_source_error_and_invalid_requests(self):
        self.assertEqual(self.client.post('/analyze', json={'code': ''}).status_code, 400)
        with patch.object(web, 'detect', return_value=(None, None)):
            session_id = self.client.post('/analyze', json={'code': 'invalid'}).json['session_id']
            response = self.client.get(f'/progress/{session_id}', buffered=True)
        self.assertIn('Analysis failed', response.get_data(as_text=True))
        self.assertNotIn(session_id, web.progress_queues)


if __name__ == '__main__':
    unittest.main()
