"""Independent R1CS encoder used by Picus contract and real-engine tests."""
import struct

BN254 = 21888242871839275222246405745257275088548364400416034343698204186575808495617


def r1cs(rows=(), *, prime=BN254, wires=2, outputs=1, public=0, private=0, order=(1, 2, 3)):
    width = max(8, ((prime.bit_length() + 63) // 64) * 8)
    header = struct.pack('<I', width) + prime.to_bytes(width, 'little')
    header += struct.pack('<IIIIQI', wires, outputs, public, private, wires, len(rows))
    constraints = bytearray()
    for row in rows:
        for combination in row:
            constraints += struct.pack('<I', len(combination))
            for wire, coefficient in sorted(combination.items()):
                constraints += struct.pack('<I', wire) + (coefficient % prime).to_bytes(width, 'little')
    sections = {1: header, 2: bytes(constraints), 3: struct.pack('<' + 'Q' * wires, *range(wires))}
    return b'r1cs' + struct.pack('<II', 1, len(order)) + b''.join(
        struct.pack('<IQ', kind, len(sections[kind])) + sections[kind] for kind in order)


SQUARE = [({1: 1}, {1: 1}, {0: 1})]  # out^2 = 1, two valid outputs
CONSTANT = [({1: 1}, {0: 1}, {0: 1})]  # out = 1
