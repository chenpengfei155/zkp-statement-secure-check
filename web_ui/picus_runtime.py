"""Pinned native runtime layout and installation integrity checks."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import struct

REVISION = '138b151d3a388e5b6c040c163e0a1db04f2ceda6'
CVC5_REVISION = 'de62429fa7c03a46d5d75f9d78fc8888792a0798'
ROOT = Path(__file__).resolve().parents[1]


def default_home():
    return str(ROOT / '.tools/picus') if os.name == 'nt' else '~/.local/share/cirverify-picus'


def sha256(path):
    with open(path, 'rb') as stream:
        result = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
        return result.hexdigest()


def relative_file(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or any(part in ('', '.', '..') or ':' in part or '\\' in part
                                            for part in name.split('/')):
        raise ValueError('Invalid runtime archive path.')
    return Path(*path.parts)


def validate_runtime(prefix):
    if os.name != 'nt' or struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('This runtime requires 64-bit Windows and 64-bit Python.')
    manifest = json.loads((prefix / 'runtime.json').read_text(encoding='utf-8'))
    if (manifest.get('format') != 1 or manifest.get('platform') != 'windows-x64'
            or manifest.get('picus_revision') != REVISION or manifest.get('cvc5_revision') != CVC5_REVISION):
        raise ValueError('The installed native Picus version is not supported. Run scripts/setup_picus.ps1.')
    files = manifest.get('files')
    if not isinstance(files, dict) or not {'picus.exe', 'cvc5.exe'} <= files.keys():
        raise ValueError('The native runtime manifest is incomplete.')
    for name, expected in files.items():
        path = prefix / relative_file(name)
        if not path.resolve().is_relative_to(prefix.resolve()) or sha256(path) != expected:
            raise ValueError(f'The native runtime file is missing or damaged: {name}. Run scripts/setup_picus.ps1.')
    return manifest


def solver_path(prefix):
    return prefix / ('cvc5.exe' if os.name == 'nt' else 'bin/cvc5')


def picus_command(prefix):
    if os.name == 'nt':
        return [str(prefix / 'picus.exe')]
    return [str(prefix / 'racket-8.16/bin/racket'), str(prefix / 'Picus/picus.rkt')]
