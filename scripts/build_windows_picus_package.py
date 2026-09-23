"""Maintainer-only packaging of a Windows Picus/cvc5 runtime; users only unzip it.

Requires Windows Racket with Picus dependencies and the finite-field cvc5.exe.
The only Picus patch changes three runtime file references to module references
so raco can embed their dependencies; no detection algorithm is changed.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = '138b151d3a388e5b6c040c163e0a1db04f2ceda6'
CVC5_REVISION = 'de62429fa7c03a46d5d75f9d78fc8888792a0798'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--raco', type=Path, required=True)
    parser.add_argument('--cvc5', type=Path, required=True)
    parser.add_argument('--licenses', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'vendor/picus/windows-x64')
    args = parser.parse_args()
    source, raco, solver, output = (p.resolve() for p in (args.source, args.raco, args.cvc5, args.output))
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != REVISION:
        raise ValueError('Unexpected Picus revision')
    subprocess.run(['git', '-C', str(source), 'diff', '--exit-code'], check=True, capture_output=True)
    stage = Path(tempfile.mkdtemp(prefix='picus-package-', dir=ROOT / 'build'))
    tree = stage / 'source'
    tree.mkdir()
    tracked = subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z']).decode().split('\0')
    for relative in tracked:
        if not relative or not (relative.endswith('.rkt') or Path(relative).name.startswith(('LICENSE', 'COPYING'))):
            continue
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    entry = tree / 'picus.rkt'
    original = entry.read_text(encoding='utf-8')
    embedded = original
    for name in ('selector', 'reader', 'solver'):
        old = '(define-runtime-path ' + name + '-path '
        if embedded.count(old) != 1:
            raise ValueError('Unexpected upstream module layout')
        embedded = embedded.replace(old, '(define-runtime-module-path ' + name + '-path ')
    entry.write_text(embedded, encoding='utf-8')
    patch = ''.join(difflib.unified_diff(original.splitlines(True), embedded.splitlines(True),
                                       fromfile='a/picus.rkt', tofile='b/picus.rkt'))
    (stage / 'picus-embedding.patch').write_text(patch, encoding='utf-8')
    subprocess.run([str(raco), 'exe', '-o', str(stage / 'picus.exe'), str(entry)], check=True)
    runtime = stage / 'runtime'
    subprocess.run([str(raco), 'distribute', str(runtime), str(stage / 'picus.exe')], check=True)
    shutil.copyfile(solver, runtime / 'cvc5.exe')
    shutil.copytree(args.licenses, runtime / 'licenses')
    shutil.copyfile(ROOT / 'vendor/picus/windows-x64/README.md', runtime / 'THIRD_PARTY_NOTICES.md')
    shutil.copyfile(stage / 'picus-embedding.patch', runtime / 'picus-embedding.patch')
    manifest = dict(format=1, platform='windows-x64', picus_revision=REVISION,
                    cvc5_revision=CVC5_REVISION, racket_version='8.16',
                    picus_patch='picus-embedding.patch',
                    files={str(p.relative_to(runtime)).replace('\\', '/'): digest(p)
                           for p in sorted(runtime.rglob('*')) if p.is_file()})
    (runtime / 'runtime.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output / 'runtime.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(runtime.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(runtime).as_posix())
    bundle = dict(format=1, platform='windows-x64', picus_revision=REVISION,
                  cvc5_revision=CVC5_REVISION, archive='runtime.zip',
                  sha256=digest(output / 'runtime.zip'), bytes=(output / 'runtime.zip').stat().st_size)
    (output / 'manifest.json').write_text(json.dumps(bundle, indent=2), encoding='utf-8')
    shutil.copyfile(stage / 'picus-embedding.patch', output / 'picus-embedding.patch')
    print(json.dumps({'stage': str(stage), 'runtime': str(runtime), 'bundle': bundle}, indent=2), flush=True)


if __name__ == '__main__':
    main()
