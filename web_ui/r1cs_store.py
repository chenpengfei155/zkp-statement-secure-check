"""Temporary uploads owned by this process; no client-provided filesystem paths."""

import atexit
from pathlib import Path
import tempfile
import threading
import time
import uuid

from cirverify.r1cs import R1CSFile


class R1CSStore:
    def __init__(self, ttl=30 * 60, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self.entries = {}
        self.pins = {}
        self.retired = {}
        self.lock = threading.RLock()
        self.directory = None
        atexit.register(self.close)

    def add(self, upload):
        with self.lock:
            self.expire()
            if self.directory is None:
                self.directory = tempfile.TemporaryDirectory(prefix='cirverify_r1cs_')
            file_id = uuid.uuid4().hex
            path = Path(self.directory.name) / (file_id + '.r1cs')
            try:
                upload.save(path)
                reader = R1CSFile(path)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            self.entries[file_id] = (reader, self.clock())
            filename = upload.filename.replace('\\', '/').rsplit('/', 1)[-1]
            return {'kind': 'r1cs', 'id': file_id,
                    'metadata': {**reader.metadata, 'filename': filename}}

    def page(self, file_id, offset, limit):
        with self.lock:
            self.expire()
            reader, _ = self.entries[file_id]
            result = reader.page(offset, limit)
            self.entries[file_id] = (reader, self.clock())
            return result

    def delete(self, file_id):
        with self.lock:
            entry = self.entries.pop(file_id, None)
            if entry:
                if self.pins.get(file_id):
                    self.retired[file_id] = entry[0]
                else:
                    entry[0].path.unlink(missing_ok=True)

    def acquire(self, file_id):
        """Keep the binary available for a background analysis until release()."""
        with self.lock:
            self.expire()
            reader, _ = self.entries[file_id]
            self.entries[file_id] = (reader, self.clock())
            self.pins[file_id] = self.pins.get(file_id, 0) + 1
            return reader

    def release(self, file_id):
        with self.lock:
            remaining = self.pins.get(file_id, 0) - 1
            if remaining > 0:
                self.pins[file_id] = remaining
                return
            self.pins.pop(file_id, None)
            retired = self.retired.pop(file_id, None)
            if retired:
                retired.path.unlink(missing_ok=True)
            elif file_id in self.entries:
                self.entries[file_id] = (self.entries[file_id][0], self.clock())

    def expire(self):
        with self.lock:
            now = self.clock()
            for file_id, (_, touched) in list(self.entries.items()):
                if now - touched >= self.ttl and not self.pins.get(file_id):
                    self.delete(file_id)

    def close(self):
        with self.lock:
            self.entries.clear()
            self.pins.clear()
            self.retired.clear()
            if self.directory is not None:
                self.directory.cleanup()
                self.directory = None
