"""Every accepted source analysis yields a report, including failure outcomes."""
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from web_ui import app as web

VALID = 'template T() { signal input a; signal output b; b <== a; } component main = T();'


class SourceWebTests(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()

    def result(self, payload):
        response = self.client.post('/analyze', json=payload)
        self.assertEqual(response.status_code, 200, response.json)
        session = response.json['session_id']
        thread = web.analysis_threads.get(session)
        if thread:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive(), 'Source analysis did not finish')
        stream = self.client.get(f'/progress/{session}', buffered=True)
        events = [json.loads(line[6:]) for line in stream.get_data(as_text=True).splitlines()
                  if line.startswith('data: ')]
        self.assertEqual([e['type'] for e in events], ['complete', 'done'])
        self.assertNotIn(session, web.progress_queues)
        return events[0]['report']

    def test_folder_dependencies_and_editor_changes_reach_worker(self):
        library = 'template T() { signal input a; signal output b; b <== a; }'
        report = self.result({
            'filename': 'project/entries/main.circom',
            'code': 'include "../lib.circom"; component main = T();',
            'files': [
                {'path': 'project/entries/main.circom', 'code': 'invalid old editor content'},
                {'path': 'project/lib.circom', 'code': library},
            ],
        })
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(report['filename'], 'project/entries/main.circom')

    def test_missing_dependency_and_timeout_return_unknown_report(self):
        report = self.result({'code': 'include "missing.circom"; ' + VALID})
        self.assertEqual(report['reason'], 'include_not_found')
        self.assertTrue(all(check['status'] == 'unknown' for check in report['checks']))
        self.assertIsNone(report['warning_count'])
        with patch.dict(web.app.config, SOURCE_TIMEOUT_SECONDS=0.001):
            report = self.result({'code': VALID})
        self.assertEqual(report['reason'], 'timeout', report)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(self.result({'code': VALID})['status'], 'completed')

    def test_diagnostic_line_numbers_preserve_leading_blank_lines(self):
        report = self.result({'code': '\n\n\ntemplate Bad( {'})
        self.assertEqual(report['reason'], 'syntax_error')
        self.assertTrue(any('line 4' in line for line in report['diagnostics']), report)

    def test_mixed_folder_keeps_one_report_per_file_after_invalid_file(self):
        files = [('bad.circom', 'template Bad( {'), ('good.circom', VALID),
                 ('lib.circom', 'template Library() {}'),
                 ('missing.circom', 'include "absent.circom"; ' + VALID)]
        data = {}
        for index, (name, code) in enumerate(files):
            data[f'file_{index}'] = (BytesIO(code.encode()), name)
            data[f'path_{index}'] = 'project/' + name
        response = self.client.post('/analyze_folder', data=data)
        self.assertEqual(response.status_code, 200, response.json)
        body = response.json
        self.assertEqual(body['total_files'], 4)
        self.assertEqual(body['success_count'], 1)
        self.assertEqual(body['incomplete_count'], 2)
        self.assertEqual(body['skipped_count'], 1)
        self.assertEqual([r['report']['verdict'] for r in body['results']],
                         ['unknown', 'safe', 'not_applicable', 'unknown'])
        self.assertTrue(all('report' in r for r in body['results']))

    def test_uploaded_paths_cannot_escape_project(self):
        for path in ('../escape.circom', '/absolute.circom', 'C:/outside.circom',
                     'folder/../escape.circom'):
            with self.subTest(path=path):
                response = self.client.post('/analyze', json={
                    'code': VALID, 'filename': path, 'files': [{'path': path, 'code': VALID}]})
                self.assertEqual(response.status_code, 400)
                response = self.client.post('/analyze_folder', data={
                    'file_0': (BytesIO(VALID.encode()), 'input.circom'), 'path_0': path})
                self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
