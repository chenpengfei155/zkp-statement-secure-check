"""Run one source analysis in its own process and write a JSON result."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import re
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from cirverify.source_report import build_source_report, incomplete_source_report


class DiagnosticCapture(io.TextIOBase):
    """Keep a bounded tail; progress bars cannot grow the worker's log forever."""
    def __init__(self):
        self.tail = ''
        self.stage = 'parse'
        self.lexer_error = None

    def write(self, text):
        text = str(text)
        self.tail = (self.tail + text)[-24000:]
        if self.lexer_error is None and 'token recognition error' in text.lower():
            self.lexer_error = text.strip()[:2000]
        if 'AST generated successfully' in text:
            self.stage = 'typecheck'
        elif 'Type checking passed' in text:
            self.stage = 'graph'
        elif 'CDG created successfully' in text:
            self.stage = 'detection'
        return len(text)

    def flush(self):
        pass

    def diagnostics(self):
        clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', self.tail)
        lines = [line.strip() for line in clean.replace('\r', '\n').splitlines()
                 if line.strip() and '%|' not in line and not line.startswith('[Info]')][-39:]
        return ([self.lexer_error] if self.lexer_error else []) + lines


def failure_reason(capture, error=None):
    text = capture.tail.lower()
    if isinstance(error, MemoryError) or 'memoryerror' in text:
        return 'memory_limit'
    if isinstance(error, RecursionError) or 'maximum recursion depth exceeded' in text:
        return 'recursion_limit'
    if 'include file' in text and 'not found' in text:
        return 'include_not_found'
    if 'no main function found' in text or 'main component not declared' in text:
        return 'no_main'
    if capture.lexer_error or 'error on line' in text or 'syntaxexception' in text:
        return 'syntax_error'
    return {'typecheck': 'typecheck_error', 'graph': 'graph_error',
            'detection': 'detection_error'}.get(capture.stage, 'analysis_error')


def run(source, filename=None):
    started = time.monotonic()
    capture = DiagnosticCapture()
    error = None
    # These streams belong only to this isolated worker, never to Flask.
    with redirect_stdout(capture), redirect_stderr(capture):
        try:
            from cirverify.core import detect
            graphs, reports = detect(str(source))
            if graphs is not None and reports is not None and not capture.lexer_error:
                return build_source_report(graphs, reports, time.monotonic() - started, filename)
        except Exception as exc:
            error = exc
            traceback.print_exc(limit=8)
    return incomplete_source_report(failure_reason(capture, error), filename=filename,
                                    elapsed_seconds=time.monotonic() - started,
                                    diagnostics=capture.diagnostics(), stage=capture.stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('result', type=Path)
    parser.add_argument('--filename')
    args = parser.parse_args()
    result = run(args.source, args.filename)
    args.result.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
