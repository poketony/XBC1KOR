"""Xenoblade localization tools. All outputs are confined to codex-lab."""
import argparse
import json
from pathlib import Path
import sys

from xeno_formats import (
    FormatError, need, digest, arc_entries, bundle_entries, pkh_entries, u8_entries,
    replace_entries, sb_strings, sb_replace, bdat_strings, bdat_replace,
    rev_captions, rev_replace, rev_xor, dap_entries, dap_decode, dap_replace,
    brlyt_strings, brlyt_replace, layout_sections, tpl_info,
)
from relocate import arc_grow, bundle_grow, sb_grow, bdat_grow, brlyt_grow

LAB = Path(__file__).resolve().parent


def destination(path, directory=False):
    path = path.resolve()
    need(path.is_relative_to(LAB) and path != LAB, 'Output must be below codex-lab')
    need(not path.exists(), f'Output already exists: {path}; choose a new name')
    path.parent.mkdir(parents=True, exist_ok=True)
    if directory:
        path.mkdir()
    return path


def write_json(path, value):
    destination(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def archive_read(kind, source, body=None):
    source_data = source.read_bytes()
    if kind == 'pkh':
        need(body is not None, '--body PKB is required for PKH')
        data = body.read_bytes()
        entries = pkh_entries(source_data, data)
    else:
        need(body is None, '--body is only valid for PKH')
        data = source_data
        entries = {'arc': arc_entries, 'bundle': bundle_entries, 'u8': u8_entries, 'dap': dap_entries}[kind](data)
    return source_data, data, entries


def archive(args):
    need(not args.grow or (args.action == 'repack' and args.kind in ('arc', 'bundle')),
         '--grow is supported only for N3DS ARC/bundle repack')
    source_data, data, entries = archive_read(args.kind, args.source, args.body)
    info = {'schema': 1, 'kind': args.kind, 'source_sha256': digest(source_data),
            'body_sha256': digest(data), 'entries': entries}
    if args.action == 'list':
        if args.output:
            write_json(args.output, info)
        else:
            print(json.dumps(info, ensure_ascii=True, indent=2))
        return
    need(args.output is not None, '--output is required')
    if args.action == 'extract':
        folder = destination(args.output, directory=True)
        # Embedded names stay in metadata. Numeric filenames prevent traversal,
        # case collisions, reserved Windows names, and duplicate-name overwrite.
        for e in entries:
            payload = dap_decode(data, e) if args.kind == 'dap' else data[e['offset']:e['offset'] + e['size']]
            (folder / f'{e["index"]:06d}.bin').write_bytes(payload)
        (folder / 'manifest.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Extracted {len(entries)} entries to {folder}')
        return
    need(args.edits is not None, '--edits extraction_directory is required')
    manifest = json.loads((args.edits / 'manifest.json').read_text(encoding='utf-8'))
    need(manifest.get('schema') == 1 and manifest.get('kind') == args.kind, 'Manifest kind/version mismatch')
    need(manifest.get('source_sha256') == digest(source_data) and manifest.get('body_sha256') == digest(data),
         'Manifest does not match the explicitly supplied original')
    replacements = {}
    for e in entries:
        asset = (args.edits / f'{e["index"]:06d}.bin').resolve()
        need(asset.is_relative_to(args.edits.resolve()), 'Replacement path escapes extraction directory')
        replacements[e['index']] = asset.read_bytes()
    writer = {'arc': arc_grow, 'bundle': bundle_grow}[args.kind] if args.grow else (
        dap_replace if args.kind == 'dap' else replace_entries)
    output = writer(data, entries, replacements)
    # Parse the candidate and compare every payload (fixed-allocation entries
    # include zero padding; ARC/U8 update their explicit logical size field).
    parser = {'arc': arc_entries, 'bundle': bundle_entries, 'u8': u8_entries, 'dap': dap_entries}.get(args.kind)
    parsed = pkh_entries(source_data, output) if args.kind == 'pkh' else parser(output)
    need(len(parsed) == len(entries), 'Repacked entry count changed')
    for e in parsed:
        replacement = replacements[e['index']]
        observed = dap_decode(output, e) if args.kind == 'dap' else output[e['offset']:e['offset'] + e['size']]
        expected = replacement if args.kind == 'dap' or 'size_field' in e else replacement + bytes(e['size'] - len(replacement))
        need(observed == expected, 'Repacked payload differs from replacement')
    destination(args.output).write_bytes(output)
    print(f'Wrote {args.output}; size {len(output)}; sha256 {digest(output)}')
    if args.kind == 'pkh':
        print('PKH allocation/offsets are unchanged. Pair this PKB with the supplied original PKH.')


def text_records(kind, data, encoding):
    if kind == 'sb':
        plain, endian, entries = sb_strings(data, encoding)
        return {'endian': endian, 'scrambled': bool(data[6] & 2)}, entries
    if kind == 'bdat':
        return bdat_strings(data, encoding)
    if kind == 'brlyt':
        return brlyt_strings(data)
    start, size, entries, tail = rev_captions(data)
    return {'caption_offset': start, 'caption_bytes': size, 'tail_hex': tail.hex()}, entries


def text_command(args):
    need(not args.grow or (args.action == 'import' and args.kind in ('sb', 'bdat', 'brlyt')),
         '--grow is supported only for N3DS SB/BDAT/BRLYT import')
    data = args.source.read_bytes()
    metadata, entries = text_records(args.kind, data, args.encoding)
    if args.action == 'export':
        result = {'schema': 1, 'kind': args.kind, 'source_sha256': digest(data),
                  'encoding_policy': args.encoding, 'metadata': metadata, 'entries': entries}
        write_json(args.output, result)
        print(f'Exported {len(entries)} string records')
        return
    need(args.edits is not None, '--edits translated.json is required')
    edits = json.loads(args.edits.read_text(encoding='utf-8'))
    need(edits.get('schema') == 1 and edits.get('kind') == args.kind, 'Text file kind/version mismatch')
    need(edits.get('source_sha256') == digest(data), 'Text file belongs to a different source')
    need(edits.get('encoding_policy') == args.encoding, 'Use the same encoding policy as the export')
    edited_entries = edits.get('entries', [])
    need(len(edited_entries) == len(entries), 'Adding/removing string records is unsupported')
    translations = {}
    for original, edited in zip(entries, edited_entries):
        need(edited.get('index') == original['index'] and edited.get('raw_hex') == original['raw_hex'],
             'Only edit text values; indices and raw bytes must stay unchanged')
        value = edited.get('text')
        if value is not None:
            translations[original['index']] = value
        else:
            need(original['text'] is None, 'Cannot delete a text value')
    if args.grow:
        writer = {'sb': sb_grow, 'bdat': bdat_grow, 'brlyt': brlyt_grow}[args.kind]
        result = writer(data, translations) if args.kind == 'brlyt' else writer(data, translations, args.encoding)
    elif args.kind in ('rev', 'brlyt'):
        result = {'rev': rev_replace, 'brlyt': brlyt_replace}[args.kind](data, translations)
    else:
        result = {'sb': sb_replace, 'bdat': bdat_replace}[args.kind](data, translations, args.encoding)
    destination(args.output).write_bytes(result)
    print(f'Wrote {args.output}; sha256 {digest(result)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    ar = sub.add_parser('archive', help='List/extract/repack; fixed allocation by default, experimental --grow available')
    ar.add_argument('action', choices=['list', 'extract', 'repack'])
    ar.add_argument('kind', choices=['arc', 'bundle', 'pkh', 'u8', 'dap'])
    ar.add_argument('source', type=Path)
    ar.add_argument('--body', type=Path)
    ar.add_argument('--edits', type=Path)
    ar.add_argument('--output', type=Path)
    ar.add_argument('--grow', action='store_true', help='Experimental N3DS ARC/bundle relocation; game validation required')
    tx = sub.add_parser('text', help='Export/import strings; preserves original allocation limits')
    tx.add_argument('action', choices=['export', 'import'])
    tx.add_argument('kind', choices=['sb', 'bdat', 'rev', 'brlyt'])
    tx.add_argument('source', type=Path)
    tx.add_argument('--encoding', choices=['auto', 'cp932', 'utf-8'], default='auto')
    tx.add_argument('--edits', type=Path)
    tx.add_argument('--output', type=Path, required=True)
    tx.add_argument('--grow', action='store_true', help='Experimental N3DS string growth; game validation required')
    rv = sub.add_parser('rev-xor', help='Apply the reversible Wii REV filename-key transform')
    rv.add_argument('source', type=Path)
    rv.add_argument('--key', required=True, help='Original filename stem, without extension')
    rv.add_argument('--output', type=Path, required=True)
    ins = sub.add_parser('inspect', help='Inspect UI headers without claiming pixel/animation decoding')
    ins.add_argument('kind', choices=['tpl', 'brlyt', 'brlan'])
    ins.add_argument('source', type=Path)
    ins.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'archive':
            archive(args)
        elif args.command == 'text':
            text_command(args)
        elif args.command == 'inspect':
            data = args.source.read_bytes()
            if args.kind == 'tpl':
                info = tpl_info(data)
            else:
                endian, sections = layout_sections(data)
                info = {'endian': endian, 'sections': sections}
            write_json(args.output, {'kind': args.kind, 'sha256': digest(data), 'info': info})
        else:
            data = args.source.read_bytes()
            result = rev_xor(data, args.key)
            need(data[:4] == b'rev\0' or result[:4] == b'rev\0', 'Key did not produce a REV header')
            destination(args.output).write_bytes(result)
            print(f'Wrote {args.output}; sha256 {digest(result)}')
    except (ValueError, OSError, UnicodeError, KeyError, TypeError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
