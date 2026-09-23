"""Maintainers: collect the sources/notices accompanying the native runtime.

This is NOT called by the end-user installer. It downloads upstream source
archives, and accepts the exact local build trees used to create the binaries.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cvc5-source', type=Path, required=True)
    parser.add_argument('--cvc5-archive', type=Path, help='Optional git archive of the pinned cvc5 tree')
    parser.add_argument('--racket', type=Path, required=True)
    parser.add_argument('--picus', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'vendor/picus/windows-x64')
    args = parser.parse_args()
    cache = ROOT / 'build/picus-source-materials'
    cache.mkdir(exist_ok=True)
    licenses = cache / 'licenses'
    licenses.mkdir(exist_ok=True)
    downloads = {
        'racket-8.16-src.tgz': 'https://codeload.github.com/racket/racket/tar.gz/refs/tags/v8.16',
        'libiconv-1.15.tar.gz': 'https://ftp.gnu.org/gnu/libiconv/libiconv-1.15.tar.gz',
        'mpfr-3.1.6.tar.gz': 'https://www.mpfr.org/mpfr-3.1.6/mpfr-3.1.6.tar.gz',
        'z3-LICENSE': 'https://raw.githubusercontent.com/Z3Prover/z3/z3-4.8.8/LICENSE.txt',
    }
    def fetch(item):
        name, url = item
        path = cache / name
        if not path.exists():
            with urllib.request.urlopen(url, timeout=90) as response, path.with_suffix('.download').open('wb') as destination:
                shutil.copyfileobj(response, destination)
            path.with_suffix('.download').rename(path)
        print(f'Source: {name} {digest(path)}', flush=True)
        return path
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, downloads.items()))
    shutil.copyfile(cache / 'z3-LICENSE', licenses / 'Z3-LICENSE')
    shutil.copyfile(args.picus / 'LICENSE', licenses / 'Picus-LICENSE')
    shutil.copyfile(args.cvc5_source / 'COPYING', licenses / 'cvc5-COPYING')
    shutil.copytree(args.cvc5_source / 'licenses', licenses / 'cvc5', dirs_exist_ok=True)
    for path in args.racket.rglob('*'):
        if path.is_file() and path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')) and path.suffix not in ('.zo', '.dep'):
            target = licenses / 'racket' / path.relative_to(args.racket)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    archives = list((args.cvc5_source / 'build/deps/src').glob('*.tar.*'))
    archives += list((args.cvc5_source / 'build/deps/src').glob('*.tgz'))
    archives += [cache / name for name in downloads if name != 'z3-LICENSE']
    for archive in archives:
        with tarfile.open(archive) as source:
            for member in source.getmembers():
                if member.isfile() and Path(member.name).name.upper().startswith(('COPYING', 'LICENSE', 'NOTICE')):
                    target = licenses / archive.name / member.name
                    if not target.resolve().is_relative_to(licenses.resolve()):
                        raise ValueError('Invalid source archive path')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as stream, target.open('wb') as output:
                        shutil.copyfileobj(stream, output)
    args.output.mkdir(parents=True, exist_ok=True)
    sourcezip = args.output / 'sources.zip'
    with zipfile.ZipFile(sourcezip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for archive in archives:
            package.write(archive, 'upstream/' + archive.name)
        # Include the actual modified cvc5 source, plus all CoCoA changes.
        if args.cvc5_archive:
            package.write(args.cvc5_archive, 'upstream/cvc5-source.tgz')
            package.write(args.cvc5_source / 'cmake/FindCoCoA.cmake', 'build-record/FindCoCoA.cmake')
        else:
            for directory, dirs, files in os.walk(args.cvc5_source):
                dirs[:] = [d for d in dirs if d not in ('build', '.git', '__pycache__')]
                for name in files:
                    if name == '.git':
                        continue
                    path = Path(directory) / name
                    package.write(path, 'cvc5/' + path.relative_to(args.cvc5_source).as_posix())
        for path in args.picus.rglob('*.rkt'):
            package.write(path, 'Picus/' + path.relative_to(args.picus).as_posix())
        package.write(args.picus / 'LICENSE', 'Picus/LICENSE')
        # The Racket package sources actually used by raco, including Rosette.
        for path in (args.racket / 'share/pkgs').rglob('*'):
            if path.is_file() and path.suffix in ('.rkt', '.ss', '.scm', '.scrbl'):
                package.write(path, 'racket-packages/' + path.relative_to(args.racket / 'share/pkgs').as_posix())
        for path in licenses.rglob('*'):
            if path.is_file():
                package.write(path, 'licenses/' + path.relative_to(licenses).as_posix())
        for name in ('windows_picus_cocoa.patch', 'build_windows_picus_package.py'):
            package.write(ROOT / 'scripts' / name, 'cirverify/' + name)
        package.write(args.output / 'picus-embedding.patch', 'cirverify/picus-embedding.patch')
        # Preserve build provenance and probe helpers alongside the sources.
        probe = ROOT / 'build/windows-picus-probe'
        for name in ('build-cross.sh', 'resume-build.sh', 'cxx-wrapper.py', 'patch-cocoa.py',
                     'pe-probe-client.py', 'pe-probe-host.py', 'racket-packages.txt'):
            if (probe / name).is_file():
                package.write(probe / name, 'build-record/' + name)
    (args.output / 'SOURCE_HASHES.md').write_text(
        '# Bundled source checksums\n\n' +
        '\n'.join(f'- `{p.name}`: `{digest(p)}`' for p in
                  archives + ([args.cvc5_archive] if args.cvc5_archive else []) + [sourcezip]) +
        '\n\nUpstream download locations:\n\n' +
        '\n'.join(f'- {name}: {url}' for name, url in downloads.items()) + '\n', encoding='utf-8')
    print(json.dumps({'sources': str(sourcezip), 'bytes': sourcezip.stat().st_size,
                      'licenses': str(licenses)}), flush=True)


if __name__ == '__main__':
    main()
