"""N3DS font sheet export/import with original compressed slot allocations."""
import argparse
from collections import defaultdict, deque
import json
from pathlib import Path
import struct
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract_font import blocks, quicklz3, untile
from xeno_formats import need, num, put, span, digest
from xeno_tool import destination
from texture_tool import decode as decode_texture, encode as encode_texture


def compress_qlz3(raw):
    """Emit non-streaming long-header level 3; retain four trailing literals.

    Greedy three-byte chains choose a nearby longest match. This need not
    reproduce the original compressor's decisions to reproduce its bytes.
    """
    need(0 < len(raw) <= 16 * 1024 * 1024, 'QuickLZ input size unsupported')
    chains = defaultdict(lambda: deque(maxlen=64))
    out = bytearray(9)
    pos = 0
    while pos < len(raw):
        control_at = len(out)
        out.extend(bytes(4))
        control = 0x80000000
        for bit in range(31):
            if pos >= len(raw):
                break
            length, distance = 0, 0
            if pos < len(raw) - 10:
                limit = min(255, len(raw) - 4 - pos)
                for old in reversed(chains[raw[pos:pos + 3]]):
                    dist = pos - old
                    if dist >= 0x1ffff:
                        break
                    if dist < 3:
                        continue
                    n = 3
                    while n < limit and raw[old + n] == raw[pos + n]:
                        n += 1
                    if n > length:
                        length, distance = n, dist
                        if n == limit:
                            break
            step = max(1, length)
            if length >= 3:
                control |= 1 << bit
                if length == 3 and distance < 64:
                    value, size = distance << 2, 1
                elif length == 3 and distance < 16384:
                    value, size = (distance << 2) | 1, 2
                elif length <= 18 and distance < 1024:
                    value, size = (distance << 6) | ((length - 3) << 2) | 2, 2
                elif length <= 33:
                    value, size = (distance << 7) | ((length - 2) << 2) | 3, 3
                else:
                    value, size = (distance << 15) | ((length - 3) << 7) | 3, 4
                out.extend(value.to_bytes(size, 'little'))
            else:
                out.append(raw[pos])
            for p in range(pos, pos + step):
                if p + 3 <= len(raw):
                    chains[raw[p:p + 3]].append(p)
            pos += step
        put(out, control_at, control)
    # Four terminal literals are already present; no extra footer is required.
    out[0] = 0x4f
    put(out, 1, len(out))
    put(out, 5, len(raw))
    need(quicklz3(out) == raw, 'QuickLZ encoder verification failed')
    return bytes(out)


def sheets(data):
    sections = blocks(data)
    section = next(((p, n) for tag, p, n in sections if tag == b'PLGT'), None)
    need(section is not None, 'Font has no TGLP section')
    start, length = section
    count = num(data, start + 16, 2)
    width, height = num(data, start + 24, 2), num(data, start + 26, 2)
    pos = num(data, start + 28)
    result = []
    for i in range(count):
        allocated = num(data, pos)
        compressed = span(data, pos + 4, allocated)
        raw = quicklz3(compressed)
        need(len(raw) == num(data, start + 12), 'Font decoded sheet size mismatch')
        need(raw[:4] == b'!xtt' and raw[12:16] == b'xtrd', 'Unknown font texture wrapper')
        need(struct.unpack_from('<HH', raw, 4) == (width, height), 'Font dimensions disagree')
        pixel_at = 12 + num(raw, 24)
        need(num(raw, 28) == width * height and pixel_at + width * height == len(raw),
             'Unsupported font pixel allocation')
        need(raw[9] == 0x2a, 'Font pixel format is not LA4')
        pixel_data = raw[pixel_at:]
        if all(v>>4 == v&15 for v in pixel_data):
            image = untile(pixel_data, width, height).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        else:
            image = decode_texture({'width':width,'height':height,'format':0x2a},pixel_data)
        result.append({'index': i, 'offset': pos, 'allocation': allocated, 'raw': raw,
                       'pixel_offset': pixel_at, 'image': image})
        pos += 4 + allocated
    need(pos == start + length, 'Font sheet coverage mismatch')
    return result


def tile(image):
    if image.mode == 'RGBA':
        return encode_texture({'width':image.width,'height':image.height,'format':0x2a},image)
    need(image.mode == 'L', 'Font sheets must retain their exported L or RGBA mode')
    width, height = image.size
    need(width % 8 == 0 and height % 8 == 0, 'Font dimensions must be multiples of eight')
    linear = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
    need(all(v % 17 == 0 for v in linear), 'Font pixels must be one of 0,17,...,255; quantize explicitly')
    out = bytearray(len(linear))
    for y in range(height):
        for x in range(width):
            morton = sum((((x >> b) & 1) << (2 * b)) | (((y >> b) & 1) << (2 * b + 1)) for b in range(3))
            at = ((y // 8) * (width // 8) + x // 8) * 64 + morton
            out[at] = linear[y * width + x]
    return bytes(out)


def replace_sheets(data, edits, grow=False):
    original = sheets(data)
    need(set(edits) <= set(range(len(original))), 'Unknown font sheet index')
    out = bytearray(data)
    replacements = {}
    for i, image in edits.items():
        sheet = original[i]
        need(image.size == sheet['image'].size, 'Font sheet dimensions changed')
        need(image.mode == sheet['image'].mode, 'Retain the exported font sheet image mode')
        pixels = tile(image)
        if pixels == sheet['raw'][sheet['pixel_offset']:]:
            continue
        raw = sheet['raw'][:sheet['pixel_offset']] + pixels
        compressed = compress_qlz3(raw)
        need(grow or len(compressed) <= sheet['allocation'],
             f'Sheet {i}: compressed {len(compressed)} exceeds slot {sheet["allocation"]}; relocation unsupported')
        allocation = sheet['allocation'] if len(compressed) <= sheet['allocation'] else (len(compressed) + 3) & ~3
        replacements[i] = allocation.to_bytes(4, 'little') + compressed + bytes(allocation - len(compressed))
    if replacements:
        from font_metadata import inspect
        metadata = inspect(data)
        tag, tglp, old_length = next(b for b in blocks(data) if b[0] == b'PLGT')
        first = original[0]['offset']
        payload = bytearray(data[tglp:first])
        for i, sheet in enumerate(original):
            record = replacements.get(i, data[sheet['offset']:sheet['offset'] + 4 + sheet['allocation']])
            payload.extend(record)
            put(out, metadata['size_offsets']['sheets'] + i * 4, num(record, 0))
        put(payload, 4, len(payload))
        out[tglp:tglp + old_length] = payload
        put(out, 8, len(out))
        inspect(out)
    checked = sheets(out)
    for i, image in edits.items():
        need(tile(checked[i]['image']) == tile(image), 'Font edited pixel mismatch')
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['export', 'import'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--edits', type=Path)
    parser.add_argument('--grow', action='store_true', help='Experimental: enlarge compressed slots and update GLGR/TGLP lengths')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = args.source.read_bytes()
        if args.action == 'export':
            need(not args.grow, '--grow applies to import only')
            items = sheets(data)
            folder = destination(args.output, directory=True)
            for s in items:
                s['image'].save(folder / f'sheet_{s["index"]:02d}.png')
            (folder / 'manifest.json').write_text(json.dumps({'schema': 1, 'source_sha256': digest(data),
                'sheets': [{k: v for k, v in s.items() if k not in ('raw', 'image')} for s in items]}, indent=2), encoding='utf-8')
        else:
            need(args.edits is not None, '--edits folder required')
            manifest = json.loads((args.edits / 'manifest.json').read_text(encoding='utf-8'))
            need(manifest.get('schema') == 1 and manifest.get('source_sha256') == digest(data), 'Font manifest/source mismatch')
            edits = {}
            for s in sheets(data):
                with Image.open(args.edits / f'sheet_{s["index"]:02d}.png') as im:
                    edits[s['index']] = im.copy()
            out = replace_sheets(data, edits, args.grow)
            destination(args.output).write_bytes(out)
            print(json.dumps({'bytes': len(out), 'sha256': digest(out), 'unchanged': out == data}))
    except (ValueError, OSError, KeyError, IndexError, struct.error) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
