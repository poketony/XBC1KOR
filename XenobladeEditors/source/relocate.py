"""Explicit experimental growth paths; original inputs stay immutable.

Writers preserve unknown bytes whenever possible. Structural round trips do
not establish whether a game loader accepts appended data or changed sizes.
"""
from xeno_formats import (
    need, num, put, span, arc_entries, replace_entries, bundle_entries,
    sb_strings, bdat_strings, brlyt_strings, layout_sections,
)


def align(value, boundary):
    return (value + boundary - 1) // boundary * boundary


def arc_grow(data, entries, replacements):
    need(data[:4] == b'cram', 'ARC growth supports observed N3DS cram only')
    need(set(replacements) <= {e['index'] for e in entries}, 'Unknown ARC entry')
    if not entries:
        return data
    boundary = num(data, 8)
    need(0 < boundary <= 0x100000 and boundary & (boundary-1) == 0, 'Invalid ARC alignment')
    small = {e['index']: replacements[e['index']] for e in entries
             if e['index'] in replacements and len(replacements[e['index']]) <= e['size']}
    out = bytearray(replace_entries(data, entries, small))
    for entry in entries:
        payload = replacements.get(entry['index'])
        if payload is None or len(payload) <= entry['size']:
            continue
        at = align(len(out), boundary)
        out.extend(bytes(at-len(out)))
        out.extend(payload)
        put(out, entry['offset_field'], at)
        put(out, entry['size_field'], len(payload))
    check = arc_entries(out)
    for e in check:
        expected = replacements.get(e['index'], span(data, entries[e['index']]['offset'], entries[e['index']]['size']))
        need(span(out,e['offset'],e['size']) == expected, 'ARC relocation verification failed')
    return bytes(out)


def bundle_grow(data, entries, replacements):
    need(entries and entries[0]['endian']=='little', 'Bundle growth supports N3DS only')
    need(set(replacements) <= {e['index'] for e in entries}, 'Unknown bundle entry')
    payloads = [replacements.get(e['index'],span(data,e['offset'],e['size'])) for e in entries]
    if all(p == span(data,e['offset'],e['size']) for p,e in zip(payloads,entries)):
        return data
    out = bytearray(data[:entries[0]['offset']])
    for e,payload in zip(entries,payloads):
        need(payload[:4]==b'TADB', 'Replacement is not a N3DS table')
        out.extend(bytes(align(len(out),4)-len(out)))
        put(out,8+e['index']*4,len(out))
        out.extend(payload)
    put(out,4,len(out))
    check = bundle_entries(out)
    for e,payload in zip(check,payloads):
        observed=span(out,e['offset'],e['size'])
        need(observed[:len(payload)]==payload and not any(observed[len(payload):]), 'Bundle relocation verification failed')
    return bytes(out)


def sb_grow(data, translations, encoding='auto'):
    plain,endian,entries=sb_strings(data,encoding)
    need(endian=='little' and not data[6]&2, 'SB growth supports unscrambled N3DS only')
    need(not data[7] & 1, 'SB is already initialized in memory; use an on-disk source')
    need(set(translations) <= set(range(len(entries))), 'Unknown SB index')
    base=num(data,24)+12
    out=bytearray(data)
    for i,text in translations.items():
        e=entries[i]
        if text==e['text']:
            continue
        need(isinstance(text,str) and '\0' not in text,'Invalid SB translation')
        codec=e['encoding'] if encoding=='auto' else encoding
        need(codec is not None,'Unknown SB string encoding')
        raw=text.encode(codec)+b'\0'
        relative=len(out)-base
        if relative >= 1 << (8 * e['pointer_width']):
            return sb_widen(data, translations, encoding)
        put(out,e['pointer_field'],relative,e['pointer_width'])
        out.extend(raw)
    checked=sb_strings(out,encoding)[2]
    for e in entries:
        need(checked[e['index']]['text']==translations.get(e['index'],e['text']), 'SB relocation changed an unexpected reference')
    return bytes(out)


def sb_widen(data, translations, encoding='auto'):
    """Append a complete u32 text section; preserve code and all old pools.

    code.bin 0x5b3cc..0x5b3ec selects LDRH for width 2, LDR otherwise.
    The initializer at 0x373214 relocates sections independently and its two
    string-constant paths also support u32 (see LOADER_CLOSURE_REPORT.md).
    Only +0x18 changes in the original file; +0x1c remains the next section.
    """
    plain, endian, entries = sb_strings(data, encoding)
    need(endian == 'little' and not data[6] & 2, 'SB widening supports unscrambled N3DS only')
    need(not data[7] & 1, 'SB is already initialized in memory; use an on-disk source')
    need(set(translations) <= set(range(len(entries))), 'Unknown SB index')
    if all(entries[i]['text'] == text for i, text in translations.items()):
        return bytes(data)
    section = bytearray(12 + len(entries) * 4)
    put(section, 0, 12); put(section, 4, len(entries)); put(section, 8, 4)
    aliases = {}
    for e in entries:
        text = translations.get(e['index'], e['text'])
        raw = bytes.fromhex(e['raw_hex'])
        if text != e['text']:
            need(isinstance(text, str) and '\0' not in text, 'Invalid SB translation')
            codec = e['encoding'] if encoding == 'auto' else encoding
            need(codec is not None, 'Unknown SB string encoding')
            raw = text.encode(codec)
        key = (e['offset'], raw)
        if key not in aliases:
            aliases[key] = len(section) - 12
            section.extend(raw + b'\0')
        put(section, 12 + e['index'] * 4, aliases[key])
    out = bytearray(data)
    start = align(len(out), 4)
    out.extend(bytes(start - len(out)))
    out.extend(section)
    put(out, 24, start)
    check = sb_strings(out, encoding)[2]
    for e, found in zip(entries, check):
        if e['index'] in translations:
            need(found['text'] == translations[e['index']], 'Wide SB translated text mismatch')
        else:
            need(found['raw_hex'] == e['raw_hex'], 'Wide SB changed an untranslated byte string')
    return bytes(out)


def bdat_grow(data, translations, encoding='auto'):
    metadata,entries=bdat_strings(data,encoding)
    need(metadata['endian']=='little' and metadata['flags']==1,'BDAT growth supports N3DS flag 1 only')
    need(set(translations) <= set(range(len(entries))), 'Unknown BDAT index')
    out=bytearray(data)
    for i,text in translations.items():
        e=entries[i]
        if text==e['text']:
            continue
        need(isinstance(text,str) and '\0' not in text,'Invalid BDAT translation')
        codec=e['encoding'] if encoding=='auto' else encoding
        need(codec is not None,'Unknown BDAT encoding')
        raw=text.encode(codec)+b'\0'
        put(out,e['pointer_field'],len(out))
        out.extend(raw)
    if len(out)==len(data):
        return data
    put(out,28,len(out)-metadata['string_start'])
    # Preserve +0x16: recovered loader skips the seed for plaintext flag 1.
    checked=bdat_strings(out,encoding)[1]
    for e in entries:
        need(checked[e['index']]['text']==translations.get(e['index'],e['text']), 'BDAT relocation changed an unexpected reference')
    return bytes(out)


def brlyt_grow(data, translations):
    need(data[:4]==b'TYLR','BRLYT growth supports N3DS only')
    metadata,entries=brlyt_strings(data)
    need(set(translations) <= set(range(len(entries))), 'Unknown BRLYT index')
    changes={}
    for i,text in translations.items():
        e=entries[i]
        if text==e['text']:
            continue
        need(isinstance(text,str) and '\0' not in text,'Invalid BRLYT translation')
        raw=text.encode('utf-16-le')+b'\0\0'
        need(len(raw)<=65535,'BRLYT text exceeds u16 byte length')
        changes[e['section_offset']]=raw
    if not changes:
        return data
    _,sections=layout_sections(data)
    out=bytearray(data[:num(data,12,2)])
    for section in sections:
        chunk=bytearray(span(data,section['offset'],section['size']))
        raw=changes.get(section['offset'])
        if raw is not None:
            at=align(len(chunk),4)
            chunk.extend(bytes(at-len(chunk)))
            chunk.extend(raw)
            chunk.extend(bytes(align(len(chunk),4)-len(chunk)))
            put(chunk,4,len(chunk))
            put(chunk,76,len(raw),2)
            put(chunk,78,len(raw),2)
            put(chunk,88,at)
        out.extend(chunk)
    put(out,8,len(out))
    checked=brlyt_strings(out)[1]
    for e in entries:
        need(checked[e['index']]['text']==translations.get(e['index'],e['text']), 'BRLYT relocation verification failed')
    return bytes(out)
