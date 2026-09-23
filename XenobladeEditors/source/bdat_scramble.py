"""N3DS BDAT flag-2 transform recovered from code.bin file offset 0x111c8.

The +0x16 word supplies the seed; this routine does not validate a checksum.
Only observed little-endian flag-1 layout (plain=1, scrambled=3) is supported.
"""
import argparse
import json
from pathlib import Path

from xeno_formats import need, num, span, digest, bundle_entries
from xeno_tool import destination


def regions(data):
    need(data[:4] == b'TADB' and len(data) >= 32, 'Expected N3DS TADB table')
    need(data[4] in (1, 3), 'Only N3DS flags 1/3 are supported')
    result = [(num(data, 24), num(data, 28)),
              (num(data, 6, 2), num(data, 10, 2) - num(data, 6, 2))]
    rounded = []
    for start, length in result:
        need(length >= 0 and start >= 32, 'Invalid BDAT transform region')
        # ARM loops over pairs, including the extra byte for an odd length.
        length = (length + 1) & ~1
        span(data, start, length)
        rounded.append((start, length))
    (a, an), (b, bn) = rounded
    need(not an or not bn or a + an <= b or b + bn <= a, 'Transform regions overlap')
    return rounded


def transform_table(data, encrypt=False):
    need(data[:4] == b'TADB' and len(data) >= 32 and data[4] in (1, 3), 'Expected N3DS flags 1/3 TADB')
    if bool(data[4] & 2) == encrypt:
        return bytes(data)
    areas = regions(data)
    seed = num(data, 22, 2)
    out = bytearray(data)
    for start, length in areas:
        state = [(~(seed >> 8)) & 255, (~seed) & 255]
        for i in range(length):
            value = out[start + i]
            result = value ^ state[i & 1]
            out[start + i] = result
            ciphertext = result if encrypt else value
            state[i & 1] = (state[i & 1] + ciphertext) & 255
    out[4] = data[4] | 2 if encrypt else data[4] & ~2
    return bytes(out)


def transform_bundle(data, encrypt=False):
    out = bytearray(data)
    for entry in bundle_entries(data):
        at, size = entry['offset'], entry['size']
        out[at:at + size] = transform_table(span(data, at, size), encrypt)
    return bytes(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['decrypt', 'encrypt'])
    parser.add_argument('kind', choices=['table', 'bundle'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = args.source.read_bytes()
        fn = transform_table if args.kind == 'table' else transform_bundle
        out = fn(data, args.action == 'encrypt')
        destination(args.output).write_bytes(out)
        print(json.dumps({'bytes': len(out), 'unchanged': out == data, 'sha256': digest(out)}))
    except (ValueError, OSError, IndexError) as exc:
        parser.exit(2, f'Error: {exc}\n')
