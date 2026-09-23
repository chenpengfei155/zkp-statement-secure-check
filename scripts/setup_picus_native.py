"""Install the bundled Windows x64 Picus runtime offline, without admin or WSL."""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web_ui.picus_runtime import CVC5_REVISION, REVISION, default_home, relative_file, sha256, validate_runtime


def extract_bundle(archive, target):
    """Reject traversal, links, duplicate Windows paths, and oversized archives."""
    with zipfile.ZipFile(archive) as package:
        seen = set()
        expanded = 0
        for item in package.infolist():
            name = item.filename.rstrip('/')
            relative = relative_file(name)
            folded = name.casefold()
            if folded in seen or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Invalid runtime archive entry.')
            seen.add(folded)
            expanded += item.file_size
            if expanded > 512 * 1024 * 1024:
                raise ValueError('The runtime archive is unexpectedly large.')
            path = target / relative
            if not path.resolve().is_relative_to(target.resolve()):
                raise ValueError('The runtime archive escapes the installation directory.')
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, path.open('wb') as destination:
                    shutil.copyfileobj(source, destination)


def self_check(prefix):
    env = dict(os.environ, CIRVERIFY_PICUS_HOME=str(prefix), PYTHONUTF8='1')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check_picus.py')],
                            env=env, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
                            capture_output=True, encoding='utf-8', errors='replace')
    print(result.stdout, end='')
    if result.returncode:
        print(result.stderr, end='', file=sys.stderr)
        raise RuntimeError('Native Picus self-check failed; the previous installation was kept.')


def install(prefix, bundle_dir):
    prefix = prefix.expanduser().resolve()
    bundle = json.loads((bundle_dir / 'manifest.json').read_text(encoding='utf-8'))
    if (bundle.get('format') != 1 or bundle.get('platform') != 'windows-x64'
            or bundle.get('picus_revision') != REVISION or bundle.get('cvc5_revision') != CVC5_REVISION):
        raise ValueError('Unsupported bundled Picus version.')
    archive = bundle_dir / relative_file(bundle['archive'])
    if sha256(archive) != bundle['sha256']:
        raise ValueError('The bundled runtime failed its SHA-256 check. Obtain a complete project copy.')
    if prefix.exists():
        if not (prefix / 'runtime.json').is_file():
            raise ValueError(f'Refusing to replace a directory not owned by this installer: {prefix}')
        try:
            validate_runtime(prefix)
            with zipfile.ZipFile(archive) as package:
                bundled_runtime = json.loads(package.read('runtime.json'))
            if json.loads((prefix / 'runtime.json').read_text(encoding='utf-8')) == bundled_runtime:
                self_check(prefix)
                print(f'Native Picus is already installed and ready: {prefix}')
                return
        except (OSError, ValueError):
            pass
    prefix.parent.mkdir(parents=True, exist_ok=True)
    lock = prefix.parent / (prefix.name + '.install-lock')
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'Another installation is running (lock: {lock}).') from None
    try:
        with tempfile.TemporaryDirectory(prefix=prefix.name + '.staging-', dir=prefix.parent) as temporary:
            stage = Path(temporary)
            assert stage.resolve().parent == prefix.parent
            runtime = stage / 'runtime'
            runtime.mkdir()
            extract_bundle(archive, runtime)
            validate_runtime(runtime)
            self_check(runtime)
            backup = None
            if prefix.exists():
                backup = prefix.with_name(prefix.name + '.backup-' + uuid.uuid4().hex[:8])
                assert backup.parent == prefix.parent
                prefix.rename(backup)
            try:
                runtime.rename(prefix)
            except BaseException:
                if backup is not None:
                    backup.rename(prefix)
                raise
            print(f'Installed native Windows Picus: {prefix}')
            if backup is not None:
                print(f'Previous installation preserved: {backup}')
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(os.getenv('CIRVERIFY_PICUS_HOME', default_home())))
    parser.add_argument('--bundle-dir', type=Path, default=ROOT / 'vendor/picus/windows-x64')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Use scripts/setup_picus.sh for a native Linux installation.')
    try:
        install(args.home, args.bundle_dir.resolve())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f'Picus installation failed: {error}', file=sys.stderr)
        return 1
    print('No WSL is needed. Restart the CirVerify web server to refresh its environment check.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
