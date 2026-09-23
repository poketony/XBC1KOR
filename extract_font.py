"""Extract Xenoblade Chronicles 3D BRFNA font textures (read-only input)."""

from pathlib import Path
import argparse
import csv
import hashlib
import json
import struct

from PIL import Image, ImageChops, ImageDraw


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def quicklz3(data):
    """Decode a non-streaming QuickLZ level-3 block.

    Format reference: https://github.com/ReSpeak/quicklz/blob/master/Format.md
    """
    if data[0] != 0x4f:
        raise ValueError(f'Unsupported QuickLZ flags: {data[0]:02x}')
    size, target = struct.unpack_from('<II', data, 1)
    if not 9 <= size <= len(data) or target > 16 * 1024 * 1024:
        raise ValueError('Invalid QuickLZ sizes')
    data = data[:size]
    out = bytearray()
    pos, control = 9, 1
    while len(out) < target:
        if control == 1:
            control = u32(data, pos)
            pos += 4
            if control < 0x80000000:
                raise ValueError('Missing QuickLZ control sentinel')
        if control & 1:
            value = int.from_bytes(data[pos:pos + 4], 'little')
            if value & 3 == 0:
                distance, count, used = (value & 0xff) >> 2, 3, 1
            elif value & 3 == 1:
                distance, count, used = (value & 0xffff) >> 2, 3, 2
            elif value & 3 == 2:
                distance, count, used = (value & 0xffff) >> 6, ((value >> 2) & 15) + 3, 2
            elif value & 0x7f != 3:
                distance, count, used = (value & 0xffffff) >> 7, ((value >> 2) & 31) + 2, 3
            else:
                distance, count, used = value >> 15, ((value >> 7) & 255) + 3, 4
            if pos + used > size or not 0 < distance <= len(out) or len(out) + count > target:
                raise ValueError(f'Invalid QuickLZ match at {pos:#x}')
            pos += used
            for _ in range(count):
                out.append(out[-distance])
        else:
            out.append(data[pos])
            pos += 1
        control >>= 1
    return bytes(out)


def blocks(data):
    if data[:8] != b'ANFR\xff\xfe\x04\x01' or u32(data, 8) != len(data):
        raise ValueError('Expected the 3DS little-endian RFNA 1.4 variant')
    pos, count = struct.unpack_from('<HH', data, 12)
    result = []
    for _ in range(count):
        size = u32(data, pos + 4)
        if size < 8 or pos + size > len(data):
            raise ValueError(f'Invalid block at {pos:#x}')
        result.append((data[pos:pos + 4], pos, size))
        pos += size
    if pos != len(data):
        raise ValueError('Block lengths do not cover the file')
    return result


def read_cmap(data, sections):
    mapping = {}
    for tag, pos, size in sections:
        if tag != b'PAMC':
            continue
        block = data[pos:pos + size]
        first, last, method = struct.unpack_from('<HHH', block, 8)
        if method == 0:
            start = struct.unpack_from('<H', block, 20)[0]
            pairs = [(code, start + code - first) for code in range(first, last + 1)]
        elif method == 1:
            pairs = [(code, struct.unpack_from('<H', block, 20 + 2 * (code - first))[0])
                     for code in range(first, last + 1)]
        elif method == 2:
            count = struct.unpack_from('<H', block, 20)[0]
            pairs = [struct.unpack_from('<HH', block, 22 + 4 * i) for i in range(count)]
        else:
            raise ValueError(f'Unsupported CMAP method {method}')
        for code, glyph in pairs:
            if glyph != 0xffff:
                if code in mapping and mapping[code] != glyph:
                    raise ValueError(f'Conflicting mapping for {code:#x}')
                mapping[code] = glyph
    return mapping


def untile(raw, width, height):
    """8x8 Morton tiles, one byte per pixel; this font repeats each nibble."""
    if len(raw) != width * height or width % 8 or height % 8:
        raise ValueError('Unexpected texture dimensions')
    if any((value >> 4) != (value & 15) for value in raw):
        raise ValueError('This extractor currently requires equal pixel nibbles')
    pixels = bytearray(len(raw))
    rebuilt = bytearray(len(raw))
    for y in range(height):
        for x in range(width):
            morton = sum((((x >> bit) & 1) << (2 * bit)) |
                         (((y >> bit) & 1) << (2 * bit + 1)) for bit in range(3))
            address = ((y // 8) * (width // 8) + x // 8) * 64 + morton
            pixels[y * width + x] = raw[address]
            rebuilt[address] = pixels[y * width + x]
    if rebuilt != raw:
        raise ValueError('Texture round-trip mismatch')
    return Image.frombytes('L', (width, height), bytes(pixels))


def extract(source, output, reference=None):
    data = source.read_bytes()
    sections = blocks(data)
    tglp, tglp_size = next((pos, size) for tag, pos, size in sections if tag == b'PLGT')
    cell_width, cell_height, baseline, max_width = data[tglp + 8:tglp + 12]
    sheet_size = u32(data, tglp + 12)
    count, format_id, columns, rows, width, height = struct.unpack_from('<6H', data, tglp + 16)
    pos = u32(data, tglp + 28)
    cwdh = next(pos for tag, pos, _ in sections if tag == b'HDWC')
    first, last = struct.unpack_from('<HH', data, cwdh + 8)
    if first != 0 or last >= count * columns * rows:
        raise ValueError('Unexpected glyph range')
    mapping = read_cmap(data, sections)
    if not mapping or max(mapping.values()) > last:
        raise ValueError('CMAP glyph index outside CWDH range')
    output.mkdir(parents=True, exist_ok=True)
    glyph_dir = output / 'glyphs'
    glyph_dir.mkdir(exist_ok=True)
    sheets, records = [], []
    reference_equal = None
    for index in range(count):
        size = u32(data, pos)
        raw = quicklz3(data[pos + 4:pos + 4 + size])
        if len(raw) != sheet_size or raw[:4] != b'!xtt' or raw[12:16] != b'xtrd':
            raise ValueError('Unexpected decompressed texture container')
        if struct.unpack_from('<HH', raw, 4) != (width, height):
            raise ValueError('Container and font dimensions disagree')
        # In this variant, xtrd+0x0c points to pixels relative to xtrd.
        pixel_offset = 12 + u32(raw, 24)
        if u32(raw, 28) != width * height:
            raise ValueError('Unexpected texture byte count')
        tiled = raw[pixel_offset:]
        image = untile(tiled, width, height)
        if index == 0 and reference:
            expected = Image.open(reference).convert('RGBA')
            actual = Image.merge('RGBA', (image, image, image, image))
            reference_equal = expected.size == actual.size and all(
                high == 0 for low, high in ImageChops.difference(expected, actual).getextrema())
            if not reference_equal:
                raise ValueError('First sheet differs from the reference dump')
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        image.save(output / f'sheet_{index:02}.png')
        (output / f'sheet_{index:02}.bin').write_bytes(raw)
        sheets.append(image)
        records.append({'index': index, 'file_offset': pos, 'stored_size': size,
                        'quicklz_size': u32(data, pos + 5), 'decoded_size': len(raw),
                        'pixel_offset': pixel_offset, 'sha256': hashlib.sha256(raw).hexdigest()})
        pos += size + 4
    if pos != tglp + tglp_size:
        raise ValueError('Sheet lengths do not cover TGLP')
    reverse = {}
    for code, glyph in sorted(mapping.items()):
        reverse.setdefault(glyph, []).append(code)
    glyph_images = []
    rebuilt_sheets = [Image.new('L', image.size) for image in sheets]
    with (output / 'glyph_map.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['glyph_index', 'codepoints', 'characters', 'sheet', 'x', 'y',
                         'cell_width', 'cell_height', 'left', 'ink_width', 'advance', 'png'])
        for index in range(last + 1):
            sheet, slot = divmod(index, columns * rows)
            x = 1 + slot % columns * (cell_width + 1)
            y = 1 + slot // columns * (cell_height + 1)
            glyph = sheets[sheet].crop((x, y, x + cell_width, y + cell_height))
            glyph_images.append(glyph)
            rebuilt_sheets[sheet].paste(glyph, (x, y))
            codes = reverse.get(index, [])
            name = f'{index:04d}' + ''.join(f'_U{code:04X}' for code in codes) + '.png'
            rgba = Image.new('RGBA', glyph.size, (255, 255, 255, 255))
            rgba.putalpha(glyph)
            rgba.save(glyph_dir / name)
            left, ink, advance = struct.unpack_from('<bBB', data, cwdh + 16 + index * 3)
            writer.writerow([index, ' '.join(f'U+{c:04X}' for c in codes),
                             ''.join(chr(c) for c in codes), sheet, x, y,
                             cell_width, cell_height, left, ink, advance, f'glyphs/{name}'])
    for original, rebuilt in zip(sheets, rebuilt_sheets):
        if ImageChops.difference(original, rebuilt).getbbox() is not None:
            raise ValueError('Glyph crops lose nonzero pixels from a sheet')
    gallery = Image.new('RGB', (width * 5, (height + 24) * ((count + 4) // 5)), '#20252b')
    draw = ImageDraw.Draw(gallery)
    for index, image in enumerate(sheets):
        x, y = index % 5 * width, index // 5 * (height + 24)
        draw.text((x + 8, y + 5), f'Sheet {index:02}', fill='white')
        gallery.paste(image, (x, y + 24))
    gallery.save(output / 'all_sheets.png')
    sample_lines = ['あいうえお アイウエオ', '日本語 漢字 世界 物語',
                    'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz 0123456789']
    preview = Image.new('RGB', (1000, 4 * 100), '#111820')
    draw = ImageDraw.Draw(preview)
    for line, text in enumerate(sample_lines):
        draw.text((12, line * 100 + 8), f'CMAP sample {line + 1}', fill='#80c8ff')
        x = 12
        for char in text:
            if ord(char) not in mapping:
                raise ValueError(f'Preview character missing: {char}')
            glyph = glyph_images[mapping[ord(char)]].resize((cell_width * 2, cell_height * 2), Image.Resampling.NEAREST)
            preview.paste('white', (x, line * 100 + 32), glyph)
            x += 26 if char.isascii() else 40
    preview.save(output / 'reading_sample.png')
    report = {'source': str(source.resolve()), 'source_sha256': hashlib.sha256(data).hexdigest(),
              'sheet_count': count, 'sheet_dimensions': [width, height],
              'cell_dimensions': [cell_width, cell_height], 'grid': [columns, rows],
              'glyph_count': last + 1, 'mapped_characters': len(mapping),
              'unmapped_glyphs': last + 1 - len(reverse), 'tglp_format': format_id,
              'baseline': baseline, 'max_width': max_width,
              'glyph_crops_reconstruct_all_sheets_exactly': True,
              'reference_rgba_exact_match': reference_equal, 'sheets': records}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'sheets'}, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, nargs='?', default=Path('menufont.brfna'))
    parser.add_argument('--output', type=Path, default=Path('font_extracted'))
    parser.add_argument('--reference', type=Path, help='Optional unflipped Citra RGBA sheet-0 dump')
    args = parser.parse_args()
    extract(args.source, args.output, args.reference)
