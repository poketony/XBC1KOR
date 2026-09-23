"""REM TEX0/TXTR texture levels; container sizes are not trusted as pixel lengths.

RGBA8 and ETC1A4 channel order cross-checked against azahar-emu/azahar:
src/common/color.h and src/video_core/texture/texture_decode.cpp.
"""
from PIL import Image
from xeno_formats import num, span, need
from rev_resource_names import resource_names
from rev_wii_texture import decode_gx
from texture_tool import decode, etc_block, MORTON


def textures(data):
    container = resource_names(data)
    little = container['endian'] == 'little'
    u = lambda at, size=4: num(data, at, size, container['endian'])
    result = []
    for group in container['groups']:
        if group['name'] != 'Textures(NW4R)':
            continue
        for entry in group['resources']:
            at = entry['offset']
            target = at+u(at+0x10)
            row = {'name': entry['name'], 'resource_offset': at, 'levels': []}
            if little:
                need(bytes(data[target:target+4]) == b'!xtt', 'TXTR wrapper')
                need(bytes(data[target+12:target+16]) == b'xtrd', 'TXTR data wrapper')
                width, height = u(target+4,2), u(target+6,2)
                count, fmt = data[target+8], data[target+9]
                need(fmt in (0x20,0x23,0x24,0x25,0x28,0x29), 'Unknown TXTR pixel format')
                pixels = target+12+u(target+24)
                declared = u(target+28)
                span(data, pixels, declared)
            else:
                width, height = u(at+0x1c,2), u(at+0x1e,2)
                fmt, count = u(at+0x20), u(at+0x24)
                need(fmt in (1,3,4,5,6,14), 'Unknown TEX0 pixel format')
                pixels, declared = target, None
            need(0 < count <= 16 and 0 < width <= 4096 and 0 < height <= 4096, 'REM texture dimensions/levels')
            pos = pixels
            for level in range(count):
                w, h = max(1,width >> level), max(1,height >> level)
                if little:
                    need(w%8 == 0 and h%8 == 0, 'TXTR sub-tile mip level unsupported')
                    length = w*h//2 if fmt==0x28 else w*h*(4 if fmt==0x20 else 1 if fmt==0x29 else 2)
                else:
                    tw, th = (8,4) if fmt==1 else (8,8) if fmt==14 else (4,4)
                    length = ((w+tw-1)//tw)*((h+th-1)//th)*(64 if fmt==6 else 32)
                span(data, pos, length)
                row['levels'].append({'level': level, 'width': w, 'height': h,
                                      'format': fmt, 'offset': pos, 'bytes': length,
                                      'platform': 'n3ds' if little else 'wii'})
                pos += length
            if little:
                need(pos-pixels == declared, 'TXTR mip payload length mismatch')
            result.append(row)
    return result


def decode_level(data, info):
    w,h,fmt = info['width'],info['height'],info['format']
    if info['platform'] == 'wii':
        return decode_gx(data, info['offset'], w, h, fmt)
    raw = span(data, info['offset'], info['bytes'])
    if fmt not in (0x20,0x29):
        return decode(info, raw)
    rgba = bytearray(w*h*4)
    if fmt == 0x20:
        for y in range(h):
            for x in range(w):
                source = (((y//8)*(w//8)+x//8)*64+MORTON[(y%8)*8+x%8])*4
                target = (y*w+x)*4
                rgba[target:target+4] = bytes(raw[source:source+4])[::-1]
    else:
        pos = 0
        for ty in range(0,h,8):
            for tx in range(0,w,8):
                for sy,sx in ((0,0),(0,4),(4,0),(4,4)):
                    alpha = int.from_bytes(raw[pos:pos+8], 'little')
                    colors = etc_block(raw[pos+8:pos+16])
                    pos += 16
                    for y in range(4):
                        for x in range(4):
                            target = ((ty+sy+y)*w+tx+sx+x)*4
                            a = ((alpha >> (4*(x*4+y))) & 15)*17
                            rgba[target:target+4] = bytes(colors[y*4+x][:3]+(a,))
    return Image.frombytes('RGBA',(w,h),bytes(rgba)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
