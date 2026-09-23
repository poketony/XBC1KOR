"""Bounds-checked BRRES/MGBR dictionary inspection (animation semantics excluded)."""
from xeno_formats import num, span, need


def resource_names(data):
    sig = bytes(data[:4])
    need(sig in (b'bres', b'mgbr'), 'Expected BRRES or MGBR')
    endian = 'big' if sig == b'bres' else 'little'
    u = lambda at: num(data, at, endian=endian)
    size = u(8)
    data = span(data, 0, size)
    if sig == b'bres':
        root = num(data, 12, 2, endian)
        need(bytes(data[root:root+4]) == b'root', 'BRRES root signature')
        root += 8
    else:
        root = u(16)

    def dictionary(at):
        count = u(at+4)
        need(count <= 100000, 'Resource dictionary count limit')
        span(data, at, 24+16*count)
        rows = []
        for i in range(count):
            entry = at+24+16*i
            name_at, target = at+u(entry+8), at+u(entry+12)
            need(0 <= name_at < size and 0 <= target < size, 'Dictionary pointer bounds')
            raw = bytes(data[name_at:min(name_at+4096, size)])
            end = raw.find(b'\0')
            need(end >= 0, 'Unterminated resource name')
            rows.append({'name': raw[:end].decode('ascii'), 'offset': target})
        return rows

    groups = dictionary(root)
    for group in groups:
        group['resources'] = dictionary(group['offset'])
        for item in group['resources']:
            at = item['offset']
            item['signature'] = bytes(span(data, at, 4)).decode('ascii')
            item['size_field'] = u(at+4)
            item['version'] = u(at+8)
            item['declared_end_within_container'] = item['size_field'] <= size-at
            # Converted TXTR fields can retain a larger, padded size. Do not
            # use an unverified field as the physical slice length.
            if sig == b'bres' or item['signature'] == 'CHR0':
                span(data, at, item['size_field'])
    return {'signature': sig.decode('ascii'), 'endian': endian, 'size': size, 'groups': groups}
