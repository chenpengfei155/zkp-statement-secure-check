"""Native process ownership and offline installation failure handling."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import setup_picus_native as installer
from web_ui.picus_runtime import CVC5_REVISION, REVISION, sha256
from web_ui.windows_process import ProcessJob, checked_run


@unittest.skipUnless(os.name == 'nt', 'Windows Job Objects')
class ProcessTests(unittest.TestCase):
    def alive(self, pid):
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)

    def test_job_kills_grandchild_even_after_parent_exits(self):
        job = ProcessJob()
        process = job.spawn([sys.executable, '-c',
            'import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
            'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True)'])
        try:
            child = int(process.stdout.readline())
            process.wait(timeout=5)
            self.assertTrue(self.alive(child))
        finally:
            job.close()
            process.communicate(timeout=5)
        self.assertFalse(self.alive(child))

    def test_supervisor_crash_closes_job_and_kills_children(self):
        code = ('from web_ui.windows_process import ProcessJob; import subprocess,sys,time; '
                'j=ProcessJob(); p=j.spawn([sys.executable,"-c","import time; time.sleep(60)"]); '
                'print(p.pid,flush=True); time.sleep(60)')
        process = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            child = int(process.stdout.readline())
            self.assertTrue(self.alive(child))
        finally:
            process.kill()
            process.communicate(timeout=5)
        deadline = time.monotonic() + 5
        while self.alive(child) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.alive(child))

    def test_bounded_environment_probe_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            checked_run([sys.executable, '-c', 'import time; time.sleep(60)'],
                        env=dict(os.environ), timeout=0.2)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)

    def bundle(self, name='runtime.zip'):
        archive = self.base / name
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('runtime.json', '{}')
        manifest = dict(format=1, platform='windows-x64', picus_revision=REVISION,
                        cvc5_revision=CVC5_REVISION, archive=name, sha256=sha256(archive))
        (self.base / 'manifest.json').write_text(json.dumps(manifest))
        return archive

    def test_corrupt_bundle_fails_before_installing(self):
        archive = self.bundle()
        archive.write_bytes(b'corrupted')
        prefix = self.base / 'installed'
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            installer.install(prefix, self.base)
        self.assertFalse(prefix.exists())

    def test_archive_cannot_escape_or_duplicate_windows_paths(self):
        for names in (['../escape'], ['C:/escape'], ['..\\escape'], ['a', 'A']):
            with self.subTest(names=names):
                archive = self.base / 'bad.zip'
                with zipfile.ZipFile(archive, 'w') as package:
                    for name in names:
                        info = zipfile.ZipInfo('entry')
                        info.filename = name  # do not normalize Windows separators in the fixture
                        package.writestr(info, 'test')
                with self.assertRaises(ValueError):
                    installer.extract_bundle(archive, self.base / 'extract')
        self.assertFalse((self.base / 'escape').exists())

    def test_does_not_replace_an_unrelated_directory(self):
        self.bundle()
        prefix = self.base / 'user-files'
        prefix.mkdir()
        (prefix / 'document').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'not owned'):
            installer.install(prefix, self.base)
        self.assertEqual((prefix / 'document').read_text(), 'keep')

    def test_failed_self_check_keeps_previous_install_and_cleans_staging(self):
        self.bundle()
        prefix = self.base / 'picus'
        prefix.mkdir()
        (prefix / 'runtime.json').write_text('{"previous": true}')
        with patch.object(installer, 'validate_runtime', return_value={}), \
             patch.object(installer, 'self_check', side_effect=RuntimeError('failed probe')):
            with self.assertRaisesRegex(RuntimeError, 'failed probe'):
                installer.install(prefix, self.base)
        self.assertEqual(json.loads((prefix / 'runtime.json').read_text()), {'previous': True})
        self.assertFalse(list(self.base.glob('picus.staging-*')))
        self.assertFalse((self.base / 'picus.install-lock').exists())


if __name__ == '__main__':
    unittest.main()
