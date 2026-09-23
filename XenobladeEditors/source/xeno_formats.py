"""Small, bounds-checked readers and conservative writers for localization assets.

No filesystem mutations here. All functions operate on caller-supplied bytes.
"""
import hashlib
import zlib


class FormatError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise FormatError(message)


def span(data, offset, size):
    need(0 <= offset <= len(data) and 0 <= size <= len(data) - offset,
         f'Range outside file: {offset:#x}+{size:#x} / {len(data):#x}')
    return data[offset:offset + size]


def num(data, offset, size=4, endian='little'):
    return int.from_bytes(span(data, offset, size), endian)


def put(data, offset, value, size=4, endian='little'):
    span(data, offset, size)
    need(0 <= value < 1 << (8 * size), 'Integer does not fit the original field')
    data[offset:offset + size] = value.to_bytes(size, endian)


def cbytes(data, offset, limit=None):
    end = len(data) if limit is None else limit
    span(data, offset, end - offset)
    stop = data.find(b'\0', offset, end)
    need(stop >= 0, f'Unterminated string at {offset:#x}')
    return data[offset:stop]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_entries(data, entries, floor):
    occupied = []
    for entry in entries:
        offset, size = entry['offset'], entry['size']
        span(data, offset, size)
        need(offset >= floor or size == 0, 'Payload overlaps metadata')
        if size:
            occupied.append((offset, offset + size))
    occupied.sort()
    need(all(a[1] <= b[0] for a, b in zip(occupied, occupied[1:])), 'Overlapping payloads are unsupported')
    return entries


def arc_entries(data):
    need(data[:4] in (b'cram', b'marc'), 'Expected cram/marc ARC')
    endian = 'little' if data[:4] == b'cram' else 'big'
    count, names = num(data, 4, endian=endian), num(data, 12, endian=endian)
    if count == 0:
        return []
    span(data, 16, count * 20)
    need(names >= 16 + count * 16, 'Invalid ARC name base')
    entries = []
    names_end = 16 + count * 20
    for i in range(count):
        p = 16 + i * 16
        at = names + num(data, 16 + count * 16 + i * 4, endian=endian)
        name = cbytes(data, at)
        names_end = max(names_end, at + len(name) + 1)
        entries.append({'index': i, 'name': name.decode('cp932'),
                        'offset': num(data, p + 8, endian=endian),
                        'size': num(data, p + 12, endian=endian),
                        'offset_field': p + 8, 'size_field': p + 12,
                        'endian': endian, 'type_hex': data[p + 4:p + 8].hex()})
    return validate_entries(data, entries, names_end)


def bundle_entries(data):
    candidates = []
    for endian, magic in [('little', b'TADB'), ('big', b'BDAT')]:
        count = num(data, 0, endian=endian)
        if not 0 < count <= (len(data) - 8) // 4:
            continue
        offsets = [num(data, 8 + 4 * i, endian=endian) for i in range(count)]
        if not all(8 + 4 * count <= p <= len(data) - 32 and data[p:p + 4] == magic for p in offsets):
            continue
        if offsets != sorted(set(offsets)):
            continue
        declared = num(data, 4, endian=endian)
        # Original 3DS headers can overstate the physical length. Keep that
        # discrepancy as evidence; do not silently append invented bytes.
        last_end = len(data) if endian == 'little' else min(declared, len(data))
        need(last_end >= offsets[-1] + 32, 'Invalid bundle end')
        entries = [{'index': i, 'name': f'{i:04d}.bdat', 'offset': p,
                    'size': (offsets[i + 1] if i + 1 < count else last_end) - p,
                    'endian': endian} for i, p in enumerate(offsets)]
        candidates.append(validate_entries(data, entries, 8 + count * 4))
    need(len(candidates) == 1, 'Not an unambiguous Wii/3DS BDAT bundle')
    return candidates[0]


def pkh_entries(header, body, afs_index=None):
    need(header[:4] == b'\x00\xfe\x12\x00', 'Unsupported PKH signature')
    info, declared, count = [num(header, p, endian='big') for p in (8, 12, 16)]
    need(declared == len(header), 'PKH declared size mismatch')
    sizes_at = info + count * 8
    offsets_at = sizes_at + count * 2
    if offsets_at == declared:
        # Audio packs keep byte offsets and logical lengths in the AFS body.
        # PKH retains rounded sector sizes, not a cumulative offset directory.
        directory=body if afs_index is None else afs_index
        need(directory[:4]==b'AFS\0','PKH 위치 표가 없습니다. AFS 본문 인덱스가 필요합니다.')
        need(num(directory,4)==count,'PKH/AFS entry count mismatch')
        span(directory,8,count*8)
        entries=[]
        for i in range(count):
            offset,size=num(directory,8+i*8),num(directory,12+i*8)
            allocation=num(header,sizes_at+i*2,2,'big')*0x800
            need((size+0x7ff)//0x800*0x800==allocation,'PKH/AFS size mismatch')
            entries.append({'index':i,'name':f'{i:06d}.bin','offset':offset,
                            'size':size,'allocation_size':allocation,'endian':'little','container':'afs'})
        return validate_entries(body,entries,8+count*8)
    need(offsets_at + count * 4 <= declared, 'Incomplete PKH offset table')
    span(header, info, count * 14)
    entries = [{'index': i, 'name': f'{i:06d}.bin',
                'offset': num(header, offsets_at + i * 4, endian='big') * 0x800,
                'size': num(header, sizes_at + i * 2, 2, 'big') * 0x800,
                'endian': 'big'} for i in range(count)]
    return validate_entries(body, entries, 0)


def u8_entries(data):
    need(data[:4] == bytes.fromhex('55 aa 38 2d'), 'Expected Wii U8 archive')
    root = num(data, 4, endian='big')
    count = num(data, root + 8, endian='big')
    need(num(data, root, endian='big') >> 24 == 1, 'Invalid U8 root')
    span(data, root, count * 12)
    strings = root + count * 12
    payload_start = num(data, 12, endian='big')
    stack, entries = [(count, '')], []
    for i in range(1, count):
        while stack and i >= stack[-1][0]:
            stack.pop()
        need(bool(stack), 'Invalid U8 directory tree')
        p = root + i * 12
        tag = num(data, p, endian='big')
        name = cbytes(data, strings + (tag & 0xffffff), payload_start).decode('cp932')
        name = stack[-1][1] + name
        if tag >> 24 == 1:
            end = num(data, p + 8, endian='big')
            need(i < end <= stack[-1][0], 'Invalid U8 subtree')
            stack.append((end, name + '/'))
        else:
            need(tag >> 24 == 0, 'Invalid U8 node type')
            entries.append({'index': len(entries), 'name': name,
                            'offset': num(data, p + 4, endian='big'),
                            'size': num(data, p + 8, endian='big'),
                            'offset_field': p + 4, 'size_field': p + 8, 'endian': 'big'})
    return validate_entries(data, entries, payload_start)


def dap_entries(data):
    need(data[:4] == b'DAP1', 'Expected a Wii DAP1 archive')
    count = num(data, 4, endian='big')
    span(data, 8, count * 24)
    entries = []
    for i in range(count):
        p = 8 + i * 24
        offset, stored, size = [num(data, p + j, endian='big') for j in (8, 12, 16)]
        need(stored >= 4, 'Invalid DAP compressed size')
        span(data, offset, stored + 4)
        need(num(data, offset + 4, endian='big') == size, 'DAP inner and outer sizes disagree')
        name = span(data, p, 8).rstrip(b'\0').decode('ascii')
        typ = span(data, offset, 3).rstrip(b'\0').decode('ascii')
        entries.append({'index': i, 'name': name + '.' + typ, 'type': typ,
                        'offset': offset, 'size': stored + 4, 'decoded_size': size,
                        'record': p, 'endian': 'big'})
    return validate_entries(data, entries, 8 + count * 24)


def dap_decode(data, entry):
    need(entry['decoded_size'] <= 256 * 1024 * 1024, 'DAP output exceeds the tool limit')
    blob = span(data, entry['offset'] + 8, entry['size'] - 8)
    decoder = zlib.decompressobj()
    result = decoder.decompress(blob, entry['decoded_size'] + 1)
    need(len(result) == entry['decoded_size'] and decoder.eof and not any(decoder.unused_data),
         'DAP zlib size or stream boundary mismatch')
    return result


def dap_replace(data, entries, replacements):
    by_id = {e['index']: e for e in entries}
    need(set(replacements) <= set(by_id), 'Unknown DAP entry')
    out = bytearray(data)
    for index, payload in replacements.items():
        e = by_id[index]
        if payload == dap_decode(data, e):
            continue
        encoded = zlib.compress(payload, 9)
        capacity = e['size'] - 8
        need(len(encoded) <= capacity, f'DAP entry {index}: recompressed bytes exceed original allocation')
        out[e['offset'] + 8:e['offset'] + e['size']] = encoded + bytes(capacity - len(encoded))
        put(out, e['record'] + 12, len(encoded) + 4, endian='big')
        put(out, e['record'] + 16, len(payload), endian='big')
        put(out, e['offset'] + 4, len(payload), endian='big')
    return bytes(out)


def replace_entries(data, entries, replacements):
    """Keep offsets and allocations. Growth beyond the original payload is rejected."""
    output = bytearray(data)
    by_id = {entry['index']: entry for entry in entries}
    need(set(replacements) <= set(by_id), 'Unknown entry index')
    for index, payload in replacements.items():
        entry = by_id[index]
        offset, size = entry['offset'], entry['size']
        need(len(payload) <= size, f'Entry {index}: {len(payload)} bytes exceed original {size}; relocation not implemented')
        if payload == data[offset:offset + size]:
            continue
        output[offset:offset + size] = payload + bytes(size - len(payload))
        if 'size_field' in entry:
            put(output, entry['size_field'], len(payload), endian=entry['endian'])
    return bytes(output)


def sb_plain(data):
    need(data[:4] == b'SB  ', 'Expected SB script')
    candidates = [e for e in ('little', 'big') if num(data, 8, endian=e) == 0x40]
    need(len(candidates) == 1, 'Unknown SB byte order')
    endian = candidates[0]
    output = bytearray(data)
    if data[6] & 2:
        for field, endfield in [(12, 16), (24, 28)]:
            section, end = num(data, field, endian=endian), num(data, endfield, endian=endian)
            h, n, width = [num(data, section + p, endian=endian) for p in (0, 4, 8)]
            start = section + h + n * width
            span(data, start, end - start)
            for p in range(start, end - 3, 4):
                v = num(data, p, endian=endian)
                put(output, p, ((v >> 2) | (v << 30)) & 0xffffffff, endian=endian)
        output[6] &= 0xfd
    return bytes(output), endian


def decode_text(raw, encoding, endian):
    candidates = ([encoding] if encoding != 'auto' else
                  (['utf-8', 'cp932'] if endian == 'little' else ['cp932', 'utf-8']))
    for codec in candidates:
        try:
            return raw.decode(codec), codec
        except UnicodeDecodeError:
            pass
    return None, None


def sb_strings(data, encoding='auto'):
    plain, endian = sb_plain(data)
    section = num(plain, 24, endian=endian)
    h, n, width = [num(plain, section + p, endian=endian) for p in (0, 4, 8)]
    need(h == 12 and width in (2, 4), 'Unsupported SB string pointer table')
    base = section + h
    span(plain, base, n * width)
    entries = []
    for i in range(n):
        pos = base + num(plain, base + i * width, width, endian)
        need(pos >= base + n * width, 'SB string points into pointer table')
        raw = cbytes(plain, pos)
        text, codec = decode_text(raw, encoding, endian)
        entries.append({'index': i, 'offset': pos, 'raw_hex': raw.hex(), 'text': text,
                        'encoding': codec, 'pointer_field': base + i * width, 'pointer_width': width})
    return plain, endian, entries


def sb_replace(data, translations, encoding='auto'):
    plain, endian, entries = sb_strings(data, encoding)
    need(set(translations) <= set(range(len(entries))), 'Unknown SB string index')
    changes = {}
    for i, text in translations.items():
        need(isinstance(text, str) and '\0' not in text, 'Translation must be text without NUL')
        e = entries[i]
        codec = e['encoding'] if encoding == 'auto' else encoding
        need(codec is not None, 'Specify encoding for an undecodable record')
        payload = text.encode(codec)
        original = bytes.fromhex(e['raw_hex'])
        if text == e['text']:
            continue  # Preserve non-canonical CP932 byte spellings.
        need(len(payload) <= len(original), f'SB string {i} exceeds original byte length')
        if e['offset'] in changes:
            need(changes[e['offset']] == payload, 'Conflicting translations for an aliased string')
        changes[e['offset']] = payload
    if not changes:
        return data
    # Editing a shared/suffix substring could otherwise alter unrelated strings.
    for e in entries:
        for start, payload in changes.items():
            if e['offset'] != start:
                need(not (start < e['offset'] <= start + len(cbytes(plain, start))), 'Substring-aliased SB edit unsupported')
                need(not (e['offset'] < start <= e['offset'] + len(bytes.fromhex(e['raw_hex']))), 'Substring-aliased SB edit unsupported')
    out = bytearray(plain)
    for start, payload in changes.items():
        length = len(cbytes(plain, start))
        out[start:start + length + 1] = payload + bytes(length + 1 - len(payload))
    if data[6] & 2:
        for field, endfield in [(12, 16), (24, 28)]:
            section, end = num(data, field, endian=endian), num(data, endfield, endian=endian)
            h, n, width = [num(data, section + p, endian=endian) for p in (0, 4, 8)]
            for p in range(section + h + n * width, end - 3, 4):
                v = num(out, p, endian=endian)
                put(out, p, ((v << 2) | (v >> 30)) & 0xffffffff, endian=endian)
        out[6] = data[6]
    result = bytes(out)
    check = sb_strings(result, encoding)[2]
    for i, text in translations.items():
        need(check[i]['text'] == text, 'SB edit failed verification')
    return result


def rev_xor(data, basename):
    key = basename.split('.')[0].encode('ascii')
    need(bool(key), 'Empty REV filename key')
    return bytes(value ^ ((61 - i) & 255) ^ key[i % len(key)] for i, value in enumerate(data))


def rev_captions(data):
    need(data[:4] == b'rev\0' and num(data, 4) == 19 and num(data, 8) == 132,
         'Caption reader supports the observed N3DS REV v19 / 0x84 header only')
    start, size = num(data, 80), num(data, 84)
    if size == 0:
        return start, size, [], b''
    payload = span(data, start, size)
    pos, entries = 0, []
    while pos < len(payload):
        if payload[pos:pos + 2] == b'\xb8\x18':
            need(all(c in (0, 0xe3) for c in payload[pos + 2:]), 'Unexpected REV terminator suffix')
            return start, size, entries, payload[pos:]
        timestamp = num(payload, pos, 2, 'big')
        raw = cbytes(payload, pos + 2)
        entries.append({'index': len(entries), 'offset': start + pos, 'time_raw': timestamp,
                        'text': raw.decode('utf-8'), 'raw_hex': raw.hex(), 'encoding': 'utf-8'})
        pos += len(raw) + 3
    raise FormatError('REV caption terminator not found')


def rev_replace(data, translations):
    start, size, entries, tail = rev_captions(data)
    need(set(translations) <= set(range(len(entries))), 'Unknown caption index')
    if all(entries[i]['text'] == text for i, text in translations.items()):
        return data
    payload = bytearray()
    for e in entries:
        text = translations.get(e['index'], e['text'])
        need(isinstance(text, str) and '\0' not in text, 'Invalid caption text')
        payload += e['time_raw'].to_bytes(2, 'big') + text.encode('utf-8') + b'\0'
    payload += tail
    need(len(payload) <= size, 'Captions exceed original region; relocation is not yet verified')
    payload += bytes([0xe3]) * (size - len(payload))
    out = bytearray(data)
    out[start:start + size] = payload
    check = rev_captions(out)[2]
    need([e['time_raw'] for e in check] == [e['time_raw'] for e in entries], 'Caption timing changed')
    for i, text in translations.items():
        need(check[i]['text'] == text, 'Caption edit verification failed')
    return bytes(out)


def bdat_strings(data, encoding='auto'):
    need(data[:4] in (b'BDAT', b'TADB'), 'Expected a single BDAT table, not its bundle')
    endian = 'little' if data[:4] == b'TADB' else 'big'
    flag = data[4]
    need(not flag & 2, 'Scrambled BDAT table: use bdat_scramble.py decrypt first')
    name_start, stride = num(data, 6, 2, endian), num(data, 8, 2, endian)
    # The supplied Wii flag-0 variant differs structurally from 3DS flag-1.
    if flag == 0 and endian == 'big':
        rows_at, rows, hash_at, slots = [num(data, p, 2, endian) for p in (10, 12, 14, 16)]
        pointer_width, string_start, string_end = 2, rows_at + rows * stride, len(data)
    else:
        need(flag == 1, 'Unsupported BDAT flags/layout')
        hash_at, slots, rows_at, rows = [num(data, p, 2, endian) for p in (10, 12, 14, 16)]
        pointer_width = 4
        string_start = num(data, 24, endian=endian)
        string_end = string_start + num(data, 28, endian=endian)
    span(data, rows_at, rows * stride)
    span(data, hash_at, slots * 2)
    span(data, string_start, string_end - string_start)
    name = cbytes(data, name_start, hash_at).decode('ascii')
    nodes, columns = set(), []
    for slot in range(slots):
        node = num(data, hash_at + 2 * slot, 2, endian)
        chain = set()
        while node:
            need(node not in chain, 'Cyclic BDAT column chain')
            chain.add(node)
            need(name_start <= node < hash_at, 'BDAT column node outside name region')
            info, next_node = num(data, node, 2, endian), num(data, node + 2, 2, endian)
            if node not in nodes:
                nodes.add(node)
                field_name = cbytes(data, node + 4, hash_at).decode('ascii')
                kind, value_type = span(data, info, 2)
                field_offset = num(data, info + 2, 2, endian)
                length = num(data, info + 4, 2, endian) if kind == 2 else 1
                if kind in (1, 2) and value_type == 7:
                    need(field_offset + length * pointer_width <= stride, 'String field outside row')
                    columns.append((field_name, field_offset, length))
            node = next_node
    entries = []
    for row in range(rows):
        for field_name, field_offset, length in columns:
            for array_index in range(length):
                field = rows_at + row * stride + field_offset + array_index * pointer_width
                offset = num(data, field, pointer_width, endian)
                if offset == 0:
                    continue
                need(string_start <= offset < string_end, 'BDAT string pointer outside string region')
                raw = cbytes(data, offset, string_end)
                text, codec = decode_text(raw, encoding, endian)
                entries.append({'index': len(entries), 'row_index': row,
                                'row_id': row + num(data, 18, 2, endian), 'column': field_name,
                                'array_index': array_index, 'offset': offset, 'pointer_field': field,
                                'text': text, 'encoding': codec, 'raw_hex': raw.hex()})
    metadata = {'name': name, 'endian': endian, 'flags': flag, 'rows': rows, 'row_bytes': stride,
                'string_pointer_bytes': pointer_width, 'string_start': string_start,
                'string_end': string_end, 'string_columns': [c[0] for c in columns]}
    return metadata, entries


def bdat_replace(data, translations, encoding='auto'):
    metadata, entries = bdat_strings(data, encoding)
    need(set(translations) <= set(range(len(entries))), 'Unknown BDAT string index')
    changes = {}
    for i, text in translations.items():
        e = entries[i]
        if text == e['text']:
            continue
        need(isinstance(text, str) and '\0' not in text, 'Invalid BDAT text')
        codec = e['encoding'] if encoding == 'auto' else encoding
        need(codec is not None, 'Undecodable BDAT text needs explicit encoding')
        raw = text.encode(codec)
        need(len(raw) <= len(bytes.fromhex(e['raw_hex'])), f'BDAT string {i} exceeds original bytes')
        if e['offset'] in changes:
            need(changes[e['offset']] == raw, 'Aliased BDAT edits disagree')
        changes[e['offset']] = raw
    if not changes:
        return data
    for e in entries:
        for start in changes:
            if start != e['offset']:
                need(not start < e['offset'] <= start + len(cbytes(data, start)), 'Substring-alias edit unsupported')
                need(not e['offset'] < start <= e['offset'] + len(bytes.fromhex(e['raw_hex'])), 'Substring-alias edit unsupported')
    out = bytearray(data)
    for start, raw in changes.items():
        size = len(cbytes(data, start)) + 1
        out[start:start + size] = raw + bytes(size - len(raw))
    # Preserve +0x16. The recovered N3DS loader uses it as a scramble seed
    # only when flag & 2 is set; its producer-side derivation is still unknown.
    check = bdat_strings(out, encoding)[1]
    for i, text in translations.items():
        need(check[i]['text'] == text, 'BDAT edit verification failed (possibly an alias)')
    return bytes(out)


def layout_sections(data):
    need(data[:4] in (b'TYLR', b'NALR', b'RLYT', b'RLAN'), 'Expected BRLYT/BRLAN')
    endian = 'little' if data[:4] in (b'TYLR', b'NALR') else 'big'
    need(num(data, 4, 2, endian) == 0xfeff, 'Layout byte-order marker mismatch')
    end = num(data, 8, endian=endian)
    need(end == len(data), 'Layout declared size mismatch')
    pos, count = num(data, 12, 2, endian), num(data, 14, 2, endian)
    need(pos >= 16, 'Invalid layout header size')
    sections = []
    for i in range(count):
        size = num(data, pos + 4, endian=endian)
        need(size >= 8, 'Invalid layout section size')
        section = span(data, pos, size)
        tag = section[:4][::-1] if endian == 'little' else section[:4]
        sections.append({'index': i, 'tag': tag.decode('ascii'), 'offset': pos, 'size': size})
        pos += size
    need(pos == end, 'Layout sections do not cover declared file')
    return endian, sections


def brlyt_strings(data):
    need(data[:4] in (b'TYLR', b'RLYT'), 'Expected BRLYT, not animation')
    endian, sections = layout_sections(data)
    codec = 'utf-16-le' if endian == 'little' else 'utf-16-be'
    entries = []
    for section in sections:
        if section['tag'] != 'txt1':
            continue
        start, size = section['offset'], section['size']
        c = span(data, start, size)
        need(size >= 116, 'Unsupported txt1 header')
        capacity, used = num(c, 76, 2, endian), num(c, 78, 2, endian)
        at = num(c, 88, endian=endian)
        need(116 <= at and used <= capacity and capacity % 2 == 0 and used % 2 == 0,
             'Invalid txt1 string allocation')
        # Runtime capacity may exceed the text physically stored in txt1 (HOME menu).
        span(c, at, used)
        raw = span(c, at, used)
        need(not raw or raw[-2:] == b'\0\0', 'Unterminated txt1 string')
        text_bytes = raw[:-2] if raw else raw
        text = text_bytes.decode(codec)
        need('\0' not in text, 'Embedded NUL in txt1 text')
        entries.append({'index': len(entries), 'pane': cbytes(c, 12, 36).decode('ascii'),
                        'section_offset': start, 'offset': start + at,
                        'capacity_bytes': capacity, 'used_bytes': used,
                        'stored_bytes': min(capacity,size-at),
                        'text': text, 'raw_hex': text_bytes.hex(), 'encoding': codec})
    return {'endian': endian, 'sections': len(sections)}, entries


def brlyt_replace(data, translations):
    metadata, entries = brlyt_strings(data)
    need(set(translations) <= set(range(len(entries))), 'Unknown BRLYT string index')
    out = bytearray(data)
    for i, text in translations.items():
        e = entries[i]
        if text == e['text']:
            continue
        need(isinstance(text, str) and '\0' not in text, 'Invalid BRLYT text')
        raw = text.encode(e['encoding']) + b'\0\0'
        need(len(raw) <= e['stored_bytes'], f'BRLYT string {i} exceeds stored txt1 buffer')
        start = e['offset']
        out[start:start + e['stored_bytes']] = raw + bytes(e['stored_bytes'] - len(raw))
        put(out, e['section_offset'] + 78, len(raw), 2, metadata['endian'])
    check = brlyt_strings(out)[1]
    for i, text in translations.items():
        need(check[i]['text'] == text, 'BRLYT edit verification failed')
    return bytes(out)


def tpl_info(data):
    need(data[:4] in (bytes.fromhex('30 af 20 00'), bytes.fromhex('00 20 af 30')), 'Expected TPL')
    endian = 'little' if data[0] == 0x30 else 'big'
    count, base = num(data, 4, endian=endian), num(data, 8, endian=endian)
    span(data, base, count * 8)
    images = []
    for i in range(count):
        at, palette = num(data, base + i * 8, endian=endian), num(data, base + i * 8 + 4, endian=endian)
        span(data, at, 36)
        offset = num(data, at + 8, endian=endian)
        signature = span(data, offset, 4)
        images.append({'index': i, 'height': num(data, at, 2, endian),
                       'width': num(data, at + 2, 2, endian),
                       'format_raw': num(data, at + 4, endian=endian),
                       'payload_offset': offset, 'palette_header_offset': palette,
                       'payload_signature_hex': signature.hex(),
                       'xtt_wrapper': signature == b'!xtt'})
    return {'endian': endian, 'images': images, 'pixel_decode_supported': False}
