"""Read-only REV investigation. Unknown fields retain numeric names, not guessed semantics."""
import math
from collections import Counter
from xeno_formats import num, span, need, tpl_info
from rev_lh import decompress as lh_decode

TABLES = [bytes(v ^ k for v in range(256)) for k in range(256)]


def xor_view(data, filename):
    key = filename.split('.')[0].encode('ascii')
    need(bool(key), 'Empty filename key')
    period = math.lcm(256, len(key))
    out = bytearray(len(data))
    for i in range(min(period, len(data))):
        out[i::period] = data[i::period].translate(TABLES[((61-i)&255) ^ key[i % len(key)]])
    return bytes(out)


def records(data, start, endian, end=None):
    end = len(data) if end is None else end
    out = []
    at = start
    while at + 8 <= end:
        kind, size = num(data, at, endian=endian), num(data, at+4, endian=endian)
        need(8 <= size <= end-at, f'Invalid record at {at:#x}: {kind:#x}/{size:#x}')
        out.append({'offset': at, 'kind': kind, 'size': size})
        at += size
        if kind == 0xffffffff:
            return out, at
    raise ValueError('No record terminator')


def captions(data, start, size, encoding):
    if not size:
        return []
    payload = span(data, start, size)
    pos, out = 0, []
    while pos+2 <= len(payload):
        t = num(payload, pos, 2, 'big')
        if t == 0xb818:
            need(all(v in (0, 0xe3) for v in payload[pos+2:]), 'Caption suffix')
            return out
        end = payload.find(b'\0', pos+2)
        need(end >= 0, 'Caption missing NUL')
        raw = payload[pos+2:end]
        out.append({'offset': start+pos, 'time_raw': t, 'text': raw.decode(encoding), 'raw_hex': raw.hex()})
        pos = end+1
    raise ValueError('Caption missing terminator')


def red_items(raw, start, endian):
    """Walk embedded RED named chunks to their zero-size closing record."""
    u = lambda at: num(raw, at, endian=endian)
    name = span(raw, start+8, 24).split(b'\0')[0].decode('ascii')
    at, items = start+32, []
    for _ in range(10000):
        size, kind = u(at), num(raw, at+4, 2, endian)
        label = span(raw, at+8, 24).split(b'\0')[0].decode('ascii')
        if size == 0:
            need(label == name, 'RED closing name mismatch')
            need(kind == 0, 'RED closing kind')
            declared_counts = {'esb': raw[start+6], 'rem': raw[start+7]}
            need(declared_counts['esb'] == sum(i['kind']==3 for i in items), 'RED ESB count')
            need(declared_counts['rem'] == sum(i['kind']==4 for i in items), 'RED REM count')
            return {'name': name, 'offset': start, 'declared_payload_size': u(start),
                    'physical_size': at+32-start, 'declared_counts':declared_counts, 'items': items}
        need(size >= 32, 'Short RED item')
        payload = span(raw, at+32, size-32)
        item = {'name': label, 'kind': kind, 'offset': at, 'size': size, 'payload_offset': at+32}
        if kind in (1, 4):
            # N3DS TPL and REM payloads use absolute 0x80 alignment.
            off = at+32 if endian == 'big' else (at+32+127)&~127
            need(off < at+size, 'Resource alignment outside entry')
            need(all(v == 0xe3 for v in raw[at+32:off]), 'Unexpected resource leading padding')
            item['resource_offset'] = off
            if kind == 1:
                item['texture_offset'] = off
                item['texture'] = tpl_info(span(raw, off, at+size-off))
            else:
                need(raw[off:off+4] == (b'bres' if endian == 'big' else b'mgbr'), 'REM signature')
                item['embedded_size'] = num(raw, off+8, endian=endian)
                need(item['embedded_size'] <= at+size-off, 'REM size outside entry')
        items.append(item)
        at += size
    raise ValueError('Too many RED entries')


def res_info(data, endian):
    need(data[:4] == b'res\0', 'RES signature')
    u = lambda at: num(data, at, endian=endian)
    need(u(4) == 0x48, 'RES header size')
    result = {'index': u(8), 'resource_count': u(12), 'resource_size_sum': u(16),
              'sections': [], 'resources': []}
    for field in range(0x14, 0x48, 4):
        at = u(field)
        if not at: continue
        section = {'header_field': field, 'offset': at, 'kind': u(at), 'record_size': u(at+4)}
        if field == 0x14:
            end = at+20*result['resource_count']
            span(data, at, end-at)
            for p in range(at, end, 20):
                need(u(p) == 6 and u(p+4) == 20, 'RES resource record')
                target, declared = u(p+12), u(p+16)
                sig = span(data, target, 4)
                size = num(data, target+8, endian=endian) if sig in (b'bres', b'mgbr') else declared
                span(data, target, size)
                result['resources'].append({'index':u(p+8), 'offset':target, 'size_field':declared,
                                            'embedded_size':size, 'signature_hex':sig.hex()})
            section['end'] = end
            need(len(result['resources']) == result['resource_count'], 'RES resource count')
        elif section['kind'] in (7, 8, 9, 13):
            need(section['record_size'] == 12, 'RES reference header')
            count = u(at+8)
            stride = {7:8, 8:12, 9:8, 13:4}[section['kind']]
            span(data, at+12, count*stride)
            section['rows'] = [[u(at+12+i*stride+j) for j in range(0,stride,4)] for i in range(count)]
            section['end'] = at+12+count*stride
        else:
            section['unknown'] = True
        result['sections'].append(section)
    return result


def cut_payloads(raw, cuts, platform):
    """Yield one decoded view at a time, retaining only the current LH block."""
    block, base = b'', -1
    for cut in cuts:
        target = cut['offset']
        if platform == 'wii':
            relative = target-base
            if base >= 0 and 0 <= relative <= len(block)-0x48 and block[relative:relative+4] == b'res\0':
                yield cut, memoryview(block)[relative:], {'storage':'lh_virtual_offset','block_offset':base,'relative':relative}
            else:
                block, consumed = lh_decode(span(raw, target, cut['size_field']))
                base = target
                yield cut, block, {'storage':'lh_block','block_offset':base,'relative':0,
                                   'compressed_consumed':consumed,'decoded_size':len(block)}
        else:
            yield cut, memoryview(raw)[target:], {'storage':'plain','block_offset':target,'relative':0}


def inspect(raw, name, platform):
    endian = 'big' if platform == 'wii' else 'little'
    data = xor_view(raw, name) if platform == 'wii' else raw
    need(data[:4] == b'rev\0', 'REV signature')
    u = lambda at: num(data, at, endian=endian)
    header = u(8)
    need(header == (0x58 if platform == 'wii' else 0x84), 'Unexpected REV header')
    recs, stop = records(data, header, endian)
    result = {'name': name, 'platform': platform, 'bytes': len(raw),
              'header_words': {f'{i:02x}': u(i) for i in range(4, header, 4)},
              'metadata_end': stop, 'records': recs, 'record_counts': dict(Counter(r['kind'] for r in recs)),
              'captions': [], 'red': [], 'cut_resources': [], 'errors': []}
    for slot in range((1 if platform == 'wii' else 6) if u(0x54) else 0):
        at = 0x50+8*slot
        start, size = u(at), u(at+4)
        c = {'slot': slot, 'offset': start, 'size': size}
        try:
            rows = captions(data, start, size, 'cp932' if platform == 'wii' else 'utf-8')
            c['records'] = rows
        except Exception as exc:
            c['error'] = str(exc)
            result['errors'].append({'caption_slot': slot, 'error': str(exc)})
        result['captions'].append(c)
    for rec in recs:
        at, kind = rec['offset'], rec['kind']
        rec['words'] = [u(i) for i in range(at+8, at+rec['size'], 4)]
        if kind == 6 and rec['size'] == 20:
            off, declared = u(at+12), u(at+16)
            try:
                red = red_items(raw, off, endian)
                red.update(record_offset=at, index=u(at+8), descriptor_size=declared)
                result['red'].append(red)
            except Exception as exc:
                result['errors'].append({'red_record': at, 'target': off, 'error': str(exc)})
        elif kind == 2 and rec['size'] == 64:
            off, length = u(at+20), u(at+16)
            label = span(data, at+24, 32).split(b'\0')[0].decode('ascii', errors='backslashreplace')
            cut = {'index': u(at+8), 'record_offset': at, 'name': label,
                   'offset': off, 'size_field': length}
            try:
                # Wii offsets may address the current decoded LH block, even
                # beyond the physical file. Only cut_payloads can resolve them.
                if platform == 'wii':
                    cut['address_space'] = 'physical_or_lh_virtual'
                    result['cut_resources'].append(cut)
                    continue
                head = span(raw, off, 0x48)
                cut['signature_hex'] = head[:8].hex()
                if head[:4] == b'res\0':
                    cut['res_header'] = {f'{i:02x}': num(head, i, endian=endian) for i in range(4, 0x48, 4)}
            except Exception as exc:
                result['errors'].append({'cut_record': at, 'error': str(exc)})
            result['cut_resources'].append(cut)
    return result
