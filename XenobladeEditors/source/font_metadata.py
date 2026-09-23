"""Read RFNA GLGR groups and edit existing sparse CMAP keys without relocation."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract_font import blocks, read_cmap
from xeno_formats import need, num, put, span, cbytes, digest
from xeno_tool import destination


def inspect(data):
    sections = blocks(data)
    need(sections[0][0:2] == (b'RGLG', 16), 'Expected GLGR immediately after RFNA header')
    g, length = sections[0][1:]
    end = g + length
    group_count, ns, nw, nc = [num(data, g + p, 2) for p in (14, 16, 18, 20)]
    counts = {'sheets': ns, 'widths': nw, 'cmaps': nc}
    cursor = (41 + group_count * 2) & ~3
    sizes, size_offsets, bitmap_offsets, strides = {}, {}, {}, {}
    for kind, count in counts.items():
        size_offsets[kind] = cursor
        sizes[kind] = [num(data, cursor + i * 4) for i in range(count)]
        cursor += count * 4
    for kind, count in counts.items():
        bitmap_offsets[kind] = cursor
        strides[kind] = ((count + 31) // 32) * 4
        cursor += group_count * strides[kind]
    need(cursor <= end, 'GLGR arrays extend outside block')
    groups = []
    for i in range(group_count):
        name_at = num(data, g + 22 + i * 2, 2)
        need(cursor <= name_at < end, 'GLGR name outside string area')
        item = {'name': cbytes(data, name_at, end).decode('ascii')}
        for kind, count in counts.items():
            at = bitmap_offsets[kind] + i * strides[kind]
            item[kind] = [j for j in range(count)
                          if num(data, at + (j // 32) * 4) & (1 << (31 - j % 32))]
        groups.append(item)
    need(len({g['name'] for g in groups}) == group_count, 'Duplicate GLGR group names')
    tglps = [(p, n) for tag, p, n in sections if tag == b'PLGT']
    need(len(tglps) == 1, 'Expected one TGLP')
    t, tlen = tglps[0]
    need(num(data, t + 16, 2) == ns, 'GLGR/TGLP sheet counts disagree')
    need(num(data, g + 8) == num(data, t + 12), 'GLGR/TGLP decoded sizes disagree')
    per_sheet = num(data, t + 20, 2) * num(data, t + 22, 2)
    need(num(data, g + 12, 2) == per_sheet, 'GLGR/TGLP glyph grid disagrees')
    pos = num(data, t + 28)
    for size in sizes['sheets']:
        need(t + 32 <= pos < t + tlen and num(data, pos) == size, 'GLGR sheet allocation mismatch')
        span(data, pos + 4, size)
        pos += 4 + size
    need(pos == t + tlen, 'GLGR sheet sizes do not cover TGLP')
    for kind, tag in [('widths', b'HDWC'), ('cmaps', b'PAMC')]:
        need(sizes[kind] == [n for k, p, n in sections if k == tag], f'GLGR {kind} sizes disagree')
    glyphs_with_width = set()
    for tag, p, n in sections:
        if tag == b'HDWC':
            need(n >= 16, 'Truncated CWDH')
            first, last = num(data, p + 8, 2), num(data, p + 10, 2)
            need(first <= last and 16 + (last - first + 1) * 3 <= n, 'Invalid CWDH range')
            glyphs_with_width.update(range(first, last + 1))
        if tag == b'PAMC':
            need(n >= 22, 'Truncated CMAP')
            first, last, method = [num(data, p + off, 2) for off in (8, 10, 12)]
            need(first <= last and method in (0, 1, 2), 'Invalid CMAP header')
            required = (22 if method == 0 else 20 + (last - first + 1) * 2
                        if method == 1 else 22 + num(data, p + 20, 2) * 4)
            need(required <= n, 'CMAP records exceed block')
    mapping = read_cmap(data, sections)
    need(all(v < ns * per_sheet for v in mapping.values()), 'CMAP glyph outside sheet grid')
    need(set(mapping.values()) <= glyphs_with_width, 'CMAP glyph has no CWDH width')
    return {'groups': groups, 'counts': counts, 'sizes': sizes, 'size_offsets': size_offsets,
            'bitmap_offsets': bitmap_offsets, 'strides': strides, 'glyphs_per_sheet': per_sheet,
            'mapping': mapping, 'game_runtime_tested': False}


def remap_sparse(data, remap):
    """Rename keys in method-2 CMAPs; keep glyph indices and record counts.

    Changes are simultaneous, allowing swaps. Reject keys shared across blocks,
    occupied targets and range changes. Preserve ascending sparse key order.
    """
    info = inspect(data)
    need(all(type(k) is int and type(v) is int and 0 <= v < 0xffff
             and not 0xd800 <= v <= 0xdfff for k, v in remap.items()), 'Expected BMP scalar code points')
    need(set(remap) <= set(info['mapping']), 'Source character is absent')
    final_keys = [remap.get(k, k) for k in info['mapping']]
    need(len(set(final_keys)) == len(final_keys), 'Remap collides with an existing character')
    out = bytearray(data)
    seen = set()
    for tag, p, n in blocks(data):
        if tag != b'PAMC':
            continue
        method = num(data, p + 12, 2)
        block_mapping = read_cmap(data, [(tag, p, n)])
        affected = set(remap) & set(block_mapping)
        if not affected:
            continue
        need(not seen & affected, 'Source character occurs in multiple CMAP blocks')
        seen |= affected
        need(method == 2, 'Key changes currently require a sparse method-2 CMAP')
        count = num(data, p + 20, 2)
        need(22 + count * 4 <= n, 'Sparse CMAP records exceed block')
        pairs = [(num(data, p + 22 + i * 4, 2), num(data, p + 24 + i * 4, 2)) for i in range(count)]
        pairs = sorted((remap.get(code, code), glyph) for code, glyph in pairs)
        need(len({k for k, v in pairs}) == count, 'Duplicate sparse CMAP keys')
        first, last = num(data, p + 8, 2), num(data, p + 10, 2)
        need(all(first <= code <= last for code, glyph in pairs), 'New key outside original CMAP range')
        for i, (code, glyph) in enumerate(pairs):
            put(out, p + 22 + i * 4, code, 2)
            put(out, p + 24 + i * 4, glyph, 2)
    expected = {remap.get(k, k): v for k, v in info['mapping'].items()}
    need(inspect(out)['mapping'] == expected, 'CMAP remap verification failed')
    return bytes(out)


def replace_groups(data, edits):
    info = inspect(data)
    lookup = {g['name']: i for i, g in enumerate(info['groups'])}
    need(len({g['name'] for g in edits}) == len(edits), 'Duplicate group edit')
    out = bytearray(data)
    for edit in edits:
        need(edit['name'] in lookup, 'Unknown GLGR group')
        i = lookup[edit['name']]
        need(set(edit) <= {'name', *info['counts']}, 'Unknown group field')
        for kind, count in info['counts'].items():
            if kind not in edit or edit[kind] == info['groups'][i][kind]:
                continue
            selected = edit[kind]
            need(isinstance(selected, list) and all(type(v) is int and 0 <= v < count for v in selected)
                 and len(set(selected)) == len(selected), 'Invalid group selection')
            base = info['bitmap_offsets'][kind] + i * info['strides'][kind]
            # Preserve unused low bits in the final word.
            for j in range(count):
                at = base + (j // 32) * 4
                mask = 1 << (31 - j % 32)
                value = num(out, at)
                put(out, at, value | mask if j in selected else value & ~mask)
    checked = inspect(out)
    for edit in edits:
        result = checked['groups'][lookup[edit['name']]]
        for kind in info['counts']:
            if kind in edit:
                need(result[kind] == sorted(edit[kind]), 'Group write verification failed')
    return bytes(out)


def add_mapping(data, additions):
    """Grow one existing sparse CMAP; retain old codes, glyph slots and groups."""
    info = inspect(data)
    if not additions:
        return bytes(data)
    need(all(type(k) is int and 0 <= k < 0xffff and not 0xd800 <= k <= 0xdfff
             and type(v) is int for k, v in additions.items()), 'Expected BMP code points and integer glyph indices')
    need(not set(additions) & set(info['mapping']), 'Added code already exists; use remap for replacement')
    allowed = set()
    for tag, p, n in blocks(data):
        if tag == b'HDWC': allowed.update(range(num(data, p + 8, 2), num(data, p + 10, 2) + 1))
    need(set(additions.values()) <= allowed, 'Added mapping has no existing glyph width')
    cmaps = [(p, n) for tag, p, n in blocks(data) if tag == b'PAMC']
    candidates = [(i, p, n) for i, (p, n) in enumerate(cmaps)
                  if num(data, p + 12, 2) == 2 and num(data, p + 8, 2) <= min(additions)
                  and max(additions) <= num(data, p + 10, 2)]
    need(candidates, 'No existing sparse CMAP covers the added code range')
    index, p, n = candidates[-1]
    need(all(index in g['cmaps'] for g in info['groups']), 'Target CMAP is not selected by every group')
    count = num(data, p + 20, 2)
    pairs = [(num(data, p + 22 + i * 4, 2), num(data, p + 24 + i * 4, 2)) for i in range(count)]
    pairs = sorted(pairs + list(additions.items()))
    need(len(pairs) < 65536 and len({k for k, v in pairs}) == len(pairs), 'Sparse count overflow or duplicate key')
    block = bytearray(data[p:p + 22])
    put(block, 20, len(pairs), 2)
    for code, glyph in pairs:
        block.extend(code.to_bytes(2, 'little') + glyph.to_bytes(2, 'little'))
    block.extend(bytes((-len(block)) % 4))
    put(block, 4, len(block))
    out = bytearray(data)
    out[p:p + n] = block
    put(out, 8, len(out))
    put(out, info['size_offsets']['cmaps'] + index * 4, len(block))
    checked = inspect(out)
    need(checked['mapping'] == {**info['mapping'], **additions}, 'Added CMAP verification failed')
    return bytes(out)


def replace_widths(data, edits):
    """Replace raw three-byte CWDH records for existing glyph slots only."""
    inspect(data)
    locations = {}
    for tag, p, n in blocks(data):
        if tag != b'HDWC': continue
        first, last = num(data, p + 8, 2), num(data, p + 10, 2)
        for glyph in range(first, last + 1):
            need(glyph not in locations, 'Overlapping CWDH glyph ranges')
            locations[glyph] = p + 16 + (glyph - first) * 3
    need(set(edits) <= set(locations), 'Unknown CWDH glyph index')
    out = bytearray(data)
    for glyph, raw in edits.items():
        need(isinstance(raw, bytes) and len(raw) == 3, 'CWDH edits require three raw bytes')
        at = locations[glyph]
        out[at:at + 3] = raw
    inspect(out)
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['export', 'import'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--edits', type=Path)
    parser.add_argument('--grow', action='store_true', help='Allow add_mapping records to enlarge an existing sparse CMAP')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = args.source.read_bytes()
        if args.action == 'export':
            need(not args.grow, '--grow applies to import only')
            report = inspect(data)
            report.update({'schema': 1, 'source_sha256': digest(data), 'remap': [], 'add_mapping': [], 'width_edits': []})
            destination(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            need(args.edits is not None, '--edits JSON required')
            edits = json.loads(args.edits.read_text(encoding='utf-8'))
            need(edits.get('schema') == 1 and edits.get('source_sha256') == digest(data), 'Metadata source hash mismatch')
            rows = edits.get('remap', [])
            remap = {r['from']: r['to'] for r in rows}
            need(len(rows) == len(remap), 'Duplicate remap source')
            out = replace_groups(remap_sparse(data, remap), edits.get('groups', []))
            added = edits.get('add_mapping', [])
            need(not added or args.grow, 'add_mapping requires explicit --grow')
            additions = {r['codepoint']: r['glyph'] for r in added}
            need(len(additions) == len(added), 'Duplicate added code point')
            out = add_mapping(out, additions)
            widths = edits.get('width_edits', [])
            width_edits = {r['glyph']: bytes.fromhex(r['raw_hex']) for r in widths}
            need(len(width_edits) == len(widths), 'Duplicate CWDH edit')
            out = replace_widths(out, width_edits)
            destination(args.output).write_bytes(out)
            print(json.dumps({'bytes': len(out), 'sha256': digest(out), 'unchanged': out == data}))
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
