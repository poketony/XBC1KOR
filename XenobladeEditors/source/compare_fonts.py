"""Read-only Wii/3DS RFNA comparison, including decoded glyph pixels."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract_font import quicklz3, untile


def huffman8(data):
    """Nintendo 0x28: 8-bit symbols, paired tree nodes, LE32 bit words."""
    if data[0] != 0x28:
        raise ValueError('Only Nintendo Huffman-8 is supported here')
    target = int.from_bytes(data[1:4], 'little')
    tree_end = 4 + (data[4] + 1) * 2
    if not 0 < target <= 16 * 1024 * 1024 or tree_end > len(data):
        raise ValueError('Invalid Huffman header')
    output, node = bytearray(), 5
    for pos in range(tree_end, len(data) - 3, 4):
        word = int.from_bytes(data[pos:pos + 4], 'little')
        for shift in range(31, -1, -1):
            bit = (word >> shift) & 1
            value = data[node]
            child = (node & ~1) + 2 * ((value & 63) + 1) + bit
            if not 5 <= child < tree_end:
                raise ValueError('Huffman child outside tree')
            if value & (0x80 >> bit):
                output.append(data[child])
                node = 5
            else:
                node = child
            if len(output) == target:
                return bytes(output)
    raise ValueError('Truncated Huffman stream')


def wii_i4(raw, width, height):
    if width % 8 or height % 8 or len(raw) != width * height // 2:
        raise ValueError('Unexpected Wii I4 dimensions')
    pixels, rebuilt = bytearray(width * height), bytearray(len(raw))
    for y in range(height):
        for x in range(width):
            index = ((y // 8) * (width // 8) + x // 8) * 64 + (y % 8) * 8 + x % 8
            shift = 4 if index % 2 == 0 else 0
            value = (raw[index // 2] >> shift) & 15
            pixels[y * width + x] = value * 17
            rebuilt[index // 2] |= value << shift
    if rebuilt != raw:
        raise ValueError('Wii I4 inverse mismatch')
    return Image.frombytes('L', (width, height), bytes(pixels))


def read_font(path):
    return read_font_data(path.read_bytes(),str(path.resolve()))


def read_font_data(data,source='<memory>'):
    if data[:4] not in (b'RFNA', b'ANFR'):
        raise ValueError('Expected RFNA')
    endian = '>' if data[:4] == b'RFNA' else '<'
    def read(fmt, pos):
        return struct.unpack_from(endian + fmt, data, pos)
    if read('I', 8)[0] != len(data):
        raise ValueError('File size mismatch')
    pos, count = read('HH', 12)
    sections = []
    for _ in range(count):
        tag = data[pos:pos + 4]
        tag = tag[::-1] if endian == '<' else tag
        size = read('I', pos + 4)[0]
        if size < 8 or pos + size > len(data):
            raise ValueError('Invalid section bounds')
        sections.append((tag.decode('ascii'), pos, size))
        pos += size
    if pos != len(data):
        raise ValueError('Section coverage mismatch')
    mapping = {}
    for tag, pos, size in sections:
        if tag != 'CMAP':
            continue
        lo, hi, method = read('HHH', pos + 8)
        if method == 0:
            start = read('H', pos + 20)[0]
            pairs = [(c, start + c - lo) for c in range(lo, hi + 1)]
        elif method == 1:
            pairs = [(c, read('H', pos + 20 + 2 * (c - lo))[0]) for c in range(lo, hi + 1)]
        elif method == 2:
            pairs = [read('HH', pos + 22 + 4 * i) for i in range(read('H', pos + 20)[0])]
        else:
            raise ValueError('Unsupported CMAP method')
        for code, glyph in pairs:
            if glyph != 65535:
                if code in mapping and mapping[code] != glyph:
                    raise ValueError('Conflicting CMAP')
                mapping[code] = glyph
    tglp, tglp_size = next((p, n) for tag, p, n in sections if tag == 'TGLP')
    cw, ch, baseline, max_width = data[tglp + 8:tglp + 12]
    decoded_size = read('I', tglp + 12)[0]
    count, fmt, cols, rows, width, height = read('6H', tglp + 16)
    pos = read('I', tglp + 28)[0]
    sheets, chunks = [], []
    for index in range(count):
        size = read('I', pos)[0]
        payload = data[pos + 4:pos + 4 + size]
        raw = huffman8(payload) if endian == '>' else quicklz3(payload)
        if len(raw) != decoded_size:
            raise ValueError('Decoded size mismatch')
        if endian == '>':
            image = wii_i4(raw, width, height)
        else:
            if raw[:4] != b'!xtt' or raw[12:16] != b'xtrd':
                raise ValueError('Unexpected 3DS texture header')
            image = untile(raw[12 + int.from_bytes(raw[24:28], 'little'):], width, height)
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        sheets.append(image)
        chunks.append({'offset': pos, 'stored_length': size, 'decoded_length': len(raw),
                       'compression_first_byte': payload[0]})
        pos += size + 4
    if pos != tglp + tglp_size:
        raise ValueError('Sheet coverage mismatch')
    cwdh, cwdh_size = next((p, n) for tag, p, n in sections if tag == 'CWDH')
    first, last = read('HH', cwdh + 8)
    if first != 0 or last >= count * cols * rows or 16 + 3 * (last + 1) > cwdh_size:
        raise ValueError('Invalid glyph range')
    if any(g > last for g in mapping.values()):
        raise ValueError('CMAP outside CWDH')
    glyphs, rebuilt = [], [Image.new('L', sheet.size) for sheet in sheets]
    for index in range(last + 1):
        sheet, slot = divmod(index, cols * rows)
        x, y = 1 + (slot % cols) * (cw + 1), 1 + (slot // cols) * (ch + 1)
        glyph = sheets[sheet].crop((x, y, x + cw, y + ch))
        glyphs.append(glyph)
        rebuilt[sheet].paste(glyph, (x, y))
    lost_pixels = sum(sum(value != 0 for value in ImageChops.difference(a, b).getdata())
                      for a, b in zip(sheets, rebuilt))
    metadata = {'source': source, 'sha256': hashlib.sha256(data).hexdigest(),
                'byte_order': endian, 'blocks': sections, 'glyph_count': last + 1,
                'mapped_characters': len(mapping), 'cell': [cw, ch], 'baseline': baseline,
                'max_width': max_width, 'sheet_count': count, 'sheet_size': [width, height],
                'grid': [cols, rows], 'decoded_sheet_bytes': decoded_size, 'format': fmt,
                'chunks': chunks, 'nonzero_pixels_outside_glyph_cells': lost_pixels}
    widths = data[cwdh + 16:cwdh + 16 + 3 * (last + 1)]
    return metadata, mapping, glyphs, widths, sheets


def compare(wii, n3ds, output):
    a, amap, ag, aw, ash = read_font(wii)
    b, bmap, bg, bw, bsh = read_font(n3ds)
    shared = sorted(amap.keys() & bmap.keys())
    same_code_pixels = sum(ag[amap[c]].size == bg[bmap[c]].size and
                           ag[amap[c]].tobytes() == bg[bmap[c]].tobytes() for c in shared)
    same_index_pixels = sum(x.size == y.size and x.tobytes() == y.tobytes() for x, y in zip(ag, bg))
    result = {'wii': a, 'n3ds': b, 'cmap_identical': amap == bmap,
              'width_records_identical': aw == bw, 'shared_codepoints': len(shared),
              'same_pixels_by_codepoint': same_code_pixels,
              'same_pixels_by_index': same_index_pixels,
              'compared_indices': min(len(ag), len(bg)),
              'wii_only_codepoints': [f'U+{c:04X}' for c in sorted(amap.keys() - bmap.keys())],
              'n3ds_only_codepoints': [f'U+{c:04X}' for c in sorted(bmap.keys() - amap.keys())]}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    # Same codepoints selected on both sides; no text is rendered by a system font.
    selected = [c for c in map(ord, 'あいうえおアイウエオ日本語世界ABC123') if c in amap and c in bmap]
    preview = Image.new('RGB', (len(selected) * 30 + 16, 120), '#101820')
    draw = ImageDraw.Draw(preview)
    for y, label, mapping, glyphs in [(5, 'Wii', amap, ag), (65, 'N3DS', bmap, bg)]:
        draw.text((8, y), label, fill='#a0d9ff')
        for i, code in enumerate(selected):
            preview.paste(glyphs[mapping[code]], (8 + i * 30, y + 17))
    preview.resize((preview.width * 2, preview.height * 2), Image.Resampling.NEAREST).save(output / 'comparison.png')
    print(json.dumps({k: v for k, v in result.items() if k not in ('wii', 'n3ds', 'wii_only_codepoints', 'n3ds_only_codepoints')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wii', type=Path)
    parser.add_argument('n3ds', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    compare(args.wii, args.n3ds, args.output)
