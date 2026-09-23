"""Read standard binary R1CS files without compiling or executing a circuit."""

from array import array
from pathlib import Path
import struct


class R1CSError(ValueError):
    """An invalid or unsupported R1CS file."""


class R1CSFile:
    """Validated metadata and byte offsets; constraints are loaded only on demand."""

    def __init__(self, path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        self.offsets = array('Q')
        with self.path.open('rb') as stream:
            self._scan(stream)

    @staticmethod
    def _read(stream, size, end):
        if size < 0 or stream.tell() + size > end:
            raise R1CSError('Truncated R1CS file or invalid section length.')
        data = stream.read(size)
        if len(data) != size:
            raise R1CSError('Truncated R1CS file.')
        return data

    def _uint32(self, stream, end):
        return struct.unpack('<I', self._read(stream, 4, end))[0]

    def _scan(self, stream):
        if self._read(stream, 4, self.size) != b'r1cs':
            raise R1CSError('This is not a binary R1CS file (missing r1cs signature).')
        version = self._uint32(stream, self.size)
        if version != 1:
            raise R1CSError(f'R1CS version {version} is not supported; expected version 1.')
        count = self._uint32(stream, self.size)
        if count > (self.size - 12) // 12:
            raise R1CSError('Invalid R1CS section count.')
        sections = {}
        for _ in range(count):
            kind, size = struct.unpack('<IQ', self._read(stream, 12, self.size))
            start = stream.tell()
            if size > self.size - start:
                raise R1CSError('Truncated R1CS section.')
            if kind in (4, 5):
                raise R1CSError('R1CS custom gates are not supported by this viewer.')
            if kind in (1, 2, 3):
                if kind in sections:
                    raise R1CSError(f'Duplicate R1CS section {kind}.')
                sections[kind] = (start, start + size)
            stream.seek(start + size)
        if stream.tell() != self.size or not all(k in sections for k in (1, 2, 3)):
            raise R1CSError('Invalid R1CS sections; header, constraints and wire map are required.')

        start, end = sections[1]
        stream.seek(start)
        self.field_size = self._uint32(stream, end)
        if not 8 <= self.field_size <= 512 or self.field_size % 8:
            raise R1CSError('Unsupported field size (expected a multiple of 8, up to 512 bytes).')
        self.prime = int.from_bytes(self._read(stream, self.field_size, end), 'little')
        if self.prime < 2:
            raise R1CSError('Invalid field modulus.')
        wires, outputs, public, private, labels, constraints = struct.unpack(
            '<IIIIQI', self._read(stream, 28, end))
        # Private input counts can include inputs removed by compiler optimization.
        if stream.tell() != end or wires < 1 + outputs + public or labels < wires:
            raise R1CSError('Invalid R1CS header counts or length.')
        self.wires = wires
        self.metadata = {
            'version': version, 'size_bytes': self.size, 'prime': str(self.prime),
            'wires': wires, 'public_outputs': outputs, 'public_inputs': public,
            'private_inputs': private, 'labels': str(labels), 'constraints': constraints,
        }

        start, end = sections[2]
        stream.seek(start)
        if constraints > (end - start) // 12:
            raise R1CSError('Constraint count exceeds the constraint section size.')
        self.constraints_end = end
        for _ in range(constraints):
            self.offsets.append(stream.tell())
            for _ in range(3):
                self._combination(stream, end, collect=False)
        if stream.tell() != end:
            raise R1CSError('Constraint count does not match the constraint section.')

        start, end = sections[3]
        if end - start != wires * 8:
            raise R1CSError('Wire map size does not match the number of variables.')
        stream.seek(start)
        for wire in range(wires):
            label, = struct.unpack('<Q', self._read(stream, 8, end))
            if label >= labels or (wire == 0 and label != 0):
                raise R1CSError('Invalid label in the R1CS wire map.')

    def _combination(self, stream, end, collect):
        count = self._uint32(stream, end)
        if count > (end - stream.tell()) // (4 + self.field_size):
            raise R1CSError('Invalid number of terms in a constraint.')
        terms = [] if collect else None
        previous = -1
        for _ in range(count):
            wire = self._uint32(stream, end)
            coefficient = int.from_bytes(self._read(stream, self.field_size, end), 'little')
            if wire >= self.wires or wire <= previous:
                raise R1CSError('Constraint variable indices must be in range and sorted without duplicates.')
            if coefficient >= self.prime:
                raise R1CSError('Constraint coefficient is outside the field modulus.')
            previous = wire
            if collect:
                terms.append({'wire': wire, 'coefficient': str(coefficient)})
        return terms

    def page(self, offset=0, limit=50):
        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError('Offset must be nonnegative and limit must be between 1 and 50.')
        total = len(self.offsets)
        constraints = []
        with self.path.open('rb') as stream:
            for index in range(offset, min(offset + limit, total)):
                stream.seek(self.offsets[index])
                constraint = {'index': index}
                for name in ('a', 'b', 'c'):
                    constraint[name] = self._combination(stream, self.constraints_end, collect=True)
                constraints.append(constraint)
        return {'offset': offset, 'limit': limit, 'total': total, 'constraints': constraints}

    def iter_constraints(self):
        """Stream constraints with one open file for checks spanning the whole circuit."""
        with self.path.open('rb') as stream:
            for index, offset in enumerate(self.offsets):
                stream.seek(offset)
                yield {'index': index, **{
                    name: self._combination(stream, self.constraints_end, collect=True)
                    for name in ('a', 'b', 'c')}}
