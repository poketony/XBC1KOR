"""Decode the six GX formats observed in original Wii REV TPL/REM to RGBA.

GX channel/CMPR rules cross-checked with Dolphin TextureDecoder_Generic.cpp
and TextureDecoder_Util.h (dolphin-emu/dolphin, consulted 2026-09-22).
"""
from PIL import Image
from xeno_formats import tpl_info, span, need


def rgb565(v):
    r, g, b = v >> 11, (v >> 5) & 63, v & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def decode_tpl(data, index=0):
    tpl = tpl_info(data)
    need(tpl['endian'] == 'big', 'Expected Wii TPL')
    need(0 <= index < len(tpl['images']), 'TPL image index')
    entry = tpl['images'][index]
    return decode_gx(data, entry['payload_offset'], entry['width'], entry['height'], entry['format_raw'])


def decode_gx(data, offset, w, h, fmt):
    need(0 < w <= 4096 and 0 < h <= 4096, 'Texture dimensions limit')
    need(fmt in (1, 3, 4, 5, 6, 14), 'Unsupported Wii REV texture format')
    tw, th = (8, 4) if fmt == 1 else (8, 8) if fmt == 14 else (4, 4)
    length = ((w+tw-1)//tw)*((h+th-1)//th)*(64 if fmt == 6 else 32)
    raw = span(data, offset, length)
    out = bytearray(w*h*4)

    def put(x, y, color):
        if x < w and y < h:
            at = (y*w+x)*4
            out[at:at+4] = bytes(color)

    pos = 0
    for ty in range(0, h, th):
        for tx in range(0, w, tw):
            if fmt == 14:
                for sy, sx in ((0, 0), (0, 4), (4, 0), (4, 4)):
                    block = raw[pos:pos+8]
                    pos += 8
                    a, b = int.from_bytes(block[:2], 'big'), int.from_bytes(block[2:4], 'big')
                    ca, cb = rgb565(a), rgb565(b)
                    colors = [ca+(255,), cb+(255,)]
                    if a > b:
                        colors += [tuple((5*x+3*y)//8 for x,y in zip(ca,cb))+(255,),
                                   tuple((3*x+5*y)//8 for x,y in zip(ca,cb))+(255,)]
                    else:
                        middle = tuple((x+y)//2 for x,y in zip(ca,cb))
                        colors += [middle+(255,), middle+(0,)]
                    for y in range(4):
                        for x in range(4):
                            put(tx+sx+x, ty+sy+y, colors[(block[4+y] >> (6-2*x)) & 3])
            elif fmt == 6:
                for y in range(4):
                    for x in range(4):
                        at = pos+2*(y*4+x)
                        put(tx+x, ty+y, (raw[at+1], raw[at+32], raw[at+33], raw[at]))
                pos += 64
            else:
                for y in range(th):
                    for x in range(tw):
                        if fmt == 1:
                            v = raw[pos]; pos += 1
                            color = (v, v, v, v)
                        else:
                            v = int.from_bytes(raw[pos:pos+2], 'big'); pos += 2
                            if fmt == 3:
                                color = (v & 255,)*3 + (v >> 8,)
                            elif fmt == 4:
                                color = rgb565(v)+(255,)
                            elif v & 0x8000:
                                channels = [(v >> shift) & 31 for shift in (10, 5, 0)]
                                color = tuple((c << 3) | (c >> 2) for c in channels)+(255,)
                            else:
                                alpha = (v >> 12) & 7
                                color = tuple(((v >> shift) & 15)*17 for shift in (8, 4, 0)) + ((alpha << 5)|(alpha << 2)|(alpha >> 1),)
                        put(tx+x, ty+y, color)
    return Image.frombytes('RGBA', (w,h), bytes(out))
