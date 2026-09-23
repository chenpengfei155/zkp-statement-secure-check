"""Offline readiness and real R1CS smoke tests; usable from Windows or Linux."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests')]
from cirverify.r1cs import R1CSFile
from web_ui.picus import PicusEngine
from picus_fixtures import CONSTANT, SQUARE, r1cs


def main():
    engine = PicusEngine()
    status = engine.status(refresh=True)
    print(status)
    if not status['ready']:
        return 1
    with tempfile.TemporaryDirectory(prefix='picus_check_') as directory:
        for rows, expected in ((CONSTANT, 'safe'), (SQUARE, 'unsafe')):
            path = Path(directory) / 'check.r1cs'
            path.write_bytes(r1cs(rows, prime=17))
            report = engine.analyze(R1CSFile(path), cancelled=lambda: False, progress=lambda _: None)
            print(f"Real R1CS check: expected {expected}, got {report['verdict']}")
            if report['verdict'] != expected:
                print('\n'.join(report['logs']))
                return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
