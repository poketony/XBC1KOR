"""Observed N3DS TPL/!xtt textures: PNG export and lossless packed-format import."""
import argparse
import json
from pathlib import Path
from PIL import Image
from xeno_formats import need, num, span, tpl_info, digest
from xeno_tool import destination

FORMATS = {0x23: ('RGBA4', 2), 0x24: ('RGB565', 2), 0x25: ('LA8', 2),
           0x28: ('ETC1', 0), 0x2a: ('LA4', 1)}
MORTON = [sum((((x >> b) & 1) << (2*b)) | (((y >> b) & 1) << (2*b+1))
              for b in range(3)) for y in range(8) for x in range(8)]
MODIFIERS = [(2,8), (5,17), (9,29), (13,42), (18,60), (24,80), (33,106), (47,183)]


def texture(data, index=0):
    info = tpl_info(data)
    need(info['endian'] == 'little', 'This tool supports N3DS TPL only')
    need(0 <= index < len(info['images']), 'Texture index outside TPL')
    entry = info['images'][index]
    base = entry['payload_offset']
    raw = span(data, base, len(data) - base)
    need(raw[:4] == b'!xtt' and raw[12:16] == b'xtrd', 'Unsupported texture wrapper')
    width, height = num(raw, 4, 2), num(raw, 6, 2)
    fmt = raw[9]
    need(fmt in FORMATS and width > 0 and height > 0 and width % 8 == height % 8 == 0,
         'Unsupported texture dimensions or format')
    offset, length = 12 + num(raw, 24), num(raw, 28)
    expected = width * height // 2 if fmt == 0x28 else width * height * FORMATS[fmt][1]
    need(length == expected, 'Texture byte count mismatch (mipmap/variant unsupported)')
    pixels = span(raw, offset, length)
    return {'index': index, 'width': width, 'height': height, 'format': fmt,
            'format_name': FORMATS[fmt][0], 'pixel_offset': base + offset,
            'pixel_bytes': length, 'outer_width': entry['width'], 'outer_height': entry['height']}, pixels


def expand5(v):
    return (v << 3) | (v >> 2)


def etc_block(raw):
    word = int.from_bytes(raw, 'little')
    high, low = word >> 32, word & 0xffffffff
    if high & 2:
        a = [(high >> shift) & 31 for shift in (27,19,11)]
        delta = [(high >> shift) & 7 for shift in (24,16,8)]
        b = [v + (d if d < 4 else d - 8) for v,d in zip(a,delta)]
        need(all(0 <= v < 32 for v in b), 'Invalid ETC1 differential block')
        colors = [[expand5(v) for v in c] for c in (a,b)]
    else:
        colors = [[((high >> shift) & 15) * 17 for shift in shifts]
                  for shifts in ((28,20,12),(24,16,8))]
    tables = [(high >> 5) & 7, (high >> 2) & 7]
    output = []
    for y in range(4):
        for x in range(4):
            half = int(y >= 2) if high & 1 else int(x >= 2)
            bit = x * 4 + y
            delta = MODIFIERS[tables[half]][(low >> bit) & 1]
            if (low >> (bit + 16)) & 1:
                delta = -delta
            output.append(tuple(max(0,min(255,c + delta)) for c in colors[half]) + (255,))
    return output


def decode(info, raw):
    w,h,fmt = info['width'],info['height'],info['format']
    rgba = bytearray(w*h*4)
    if fmt == 0x28:
        pos = 0
        for ty in range(0,h,8):
            for tx in range(0,w,8):
                for sy,sx in ((0,0),(0,4),(4,0),(4,4)):
                    block = etc_block(raw[pos:pos+8]); pos += 8
                    for y in range(4):
                        for x in range(4):
                            at = ((ty+sy+y)*w+tx+sx+x)*4
                            rgba[at:at+4] = bytes(block[y*4+x])
    else:
        bpp = FORMATS[fmt][1]
        for y in range(h):
            for x in range(w):
                at = (((y//8)*(w//8)+x//8)*64 + MORTON[(y%8)*8+x%8])*bpp
                v = int.from_bytes(raw[at:at+bpp], 'little')
                if fmt == 0x23:
                    pixel = tuple(((v >> shift)&15)*17 for shift in (12,8,4,0))
                elif fmt == 0x24:
                    green = (v>>5)&63
                    pixel = (expand5(v>>11), (green<<2)|(green>>4), expand5(v&31), 255)
                elif fmt == 0x25:
                    pixel = (v>>8,)*3 + (v&255,)
                else:
                    pixel = ((v>>4)*17,)*3 + ((v&15)*17,)
                dest = (y*w+x)*4
                rgba[dest:dest+4] = bytes(pixel)
    return Image.frombytes('RGBA',(w,h),bytes(rgba)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)


def encode(info, image):
    need(image.mode == 'RGBA' and image.size == (info['width'],info['height']), 'Expected same-size RGBA PNG')
    fmt = info['format']
    need(fmt != 0x28, 'ETC1 changed-image encoding is not implemented; export and unchanged import only')
    w,h = image.size
    linear = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
    bpp = FORMATS[fmt][1]
    out = bytearray(w*h*bpp)
    for y in range(h):
        for x in range(w):
            p = (y*w+x)*4
            r,g,b,a = linear[p:p+4]
            if fmt == 0x23:
                need(all(v%17==0 for v in (r,g,b,a)), 'RGBA4 requires 4-bit channel values; quantize explicitly')
                v = (r//17<<12)|(g//17<<8)|(b//17<<4)|(a//17)
            elif fmt == 0x24:
                need(a==255, 'RGB565 cannot represent transparency')
                v = (r>>3<<11)|(round(g*63/255)<<5)|(b>>3)
            elif fmt == 0x25:
                need(r==g==b, 'LA8 requires grayscale RGB')
                v = (r<<8)|a
            else:
                need(r==g==b and r%17==0 and a%17==0, 'LA4 requires 4-bit grayscale and alpha')
                v = (r//17<<4)|(a//17)
            at = (((y//8)*(w//8)+x//8)*64 + MORTON[(y%8)*8+x%8])*bpp
            out[at:at+bpp] = v.to_bytes(bpp,'little')
    need(decode(info,out).tobytes()==image.tobytes(), 'Pixels not exactly representable in original texture format')
    return bytes(out)


def encode_etc_block(pixels):
    """Simple lossy ETC1 individual-mode encoder; no differential search."""
    best = None
    for flip in (0,1):
        colors, tables, selectors, total = [], [], {}, 0
        for half in (0,1):
            positions = [(x,y) for y in range(4) for x in range(4)
                         if (int(y>=2) if flip else int(x>=2))==half]
            color = [max(0,min(15,round(sum(pixels[y*4+x][c] for x,y in positions)/len(positions)/17)))
                     for c in range(3)]
            best_table = None
            for table, mods in enumerate(MODIFIERS):
                error, indices = 0, {}
                variants = [tuple(max(0,min(255,v*17+d)) for v in color)
                            for d in (mods[0],mods[1],-mods[0],-mods[1])]
                for x,y in positions:
                    target = pixels[y*4+x]
                    costs = [sum((v[c]-target[c])**2 for c in range(3)) for v in variants]
                    choice = min(range(4),key=costs.__getitem__)
                    error += costs[choice]
                    indices[(x,y)] = choice
                if best_table is None or error < best_table[0]:
                    best_table = error, table, indices
            total += best_table[0]
            colors.append(color); tables.append(best_table[1]); selectors.update(best_table[2])
        high = flip | (tables[0]<<5) | (tables[1]<<2)
        for color,shifts in zip(colors,((28,20,12),(24,16,8))):
            for value,shift in zip(color,shifts):
                high |= value<<shift
        low = 0
        for (x,y),choice in selectors.items():
            bit = x*4+y
            low |= (choice&1)<<bit
            low |= (choice>>1)<<(bit+16)
        if best is None or total < best[0]:
            best = total, ((high<<32)|low).to_bytes(8,'little')
    return best[1]


def replace_etc(info, original, image):
    w,h = image.size
    linear = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
    need(all(linear[i]==255 for i in range(3,len(linear),4)), 'ETC1 cannot store transparency')
    out = bytearray(original)
    pos = 0
    for ty in range(0,h,8):
        for tx in range(0,w,8):
            for sy,sx in ((0,0),(0,4),(4,0),(4,4)):
                pixels = []
                for y in range(4):
                    for x in range(4):
                        at = ((ty+sy+y)*w+tx+sx+x)*4
                        pixels.append(tuple(linear[at:at+4]))
                if pixels != etc_block(original[pos:pos+8]):
                    out[pos:pos+8] = encode_etc_block(pixels)
                pos += 8
    return bytes(out)


def replace(data, index, image, allow_lossy=False):
    info, raw = texture(data,index)
    old = decode(info,raw)
    need(image.mode == 'RGBA' and image.size == old.size, 'Expected original-size RGBA image')
    if old.tobytes()==image.tobytes():
        return data
    if info['format']==0x28:
        need(allow_lossy, 'ETC1 edits require --allow-lossy; unchanged 4x4 blocks are preserved')
        pixels = replace_etc(info,raw,image)
    else:
        pixels = encode(info,image)
    out = bytearray(data)
    at = info['pixel_offset']
    out[at:at+len(pixels)] = pixels
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['export','import'])
    parser.add_argument('source',type=Path)
    parser.add_argument('--index',type=int,default=0)
    parser.add_argument('--edits',type=Path)
    parser.add_argument('--allow-lossy',action='store_true',help='Allow ETC1 recompression of changed 4x4 blocks')
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    try:
        data = args.source.read_bytes()
        info, raw = texture(data,args.index)
        if args.action=='export':
            decode(info,raw).save(destination(args.output),format='PNG')
        else:
            need(args.edits is not None,'--edits PNG required')
            with Image.open(args.edits) as im:
                output = replace(data,args.index,im,args.allow_lossy)
                decoded = decode(info,texture(output,args.index)[1]).tobytes()
                expected = im.tobytes()
                info['max_channel_error'] = max(abs(a-b) for a,b in zip(decoded,expected))
            destination(args.output).write_bytes(output)
            info['output_sha256']=digest(output)
        print(json.dumps(info))
    except (ValueError,OSError,IndexError) as exc:
        parser.exit(2,f'Error: {exc}\n')


if __name__=='__main__':
    main()
