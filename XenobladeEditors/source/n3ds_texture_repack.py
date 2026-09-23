"""3DS texture encoders and TPL/REV resource rebuilding."""
from PIL import Image
from xeno_formats import need,num,put,span,tpl_info
from texture_tool import texture,decode,encode,encode_etc_block,etc_block,MORTON
from rev_rem_texture import decode_level
from wii_repack import align


def encode_pixels(info,image,old=None,allow_lossy=False):
    w,h=image.size;fmt=info['format'];image=image.convert('RGBA')
    need(0<w<=4096 and 0<h<=4096 and w%8==h%8==0,'3DS image dimensions must be multiples of 8, up to 4096')
    spec=dict(info,width=w,height=h,offset=0,platform='n3ds',bytes=len(old) if old is not None else 0)
    if old is not None and decode_level(old,spec).tobytes()==image.tobytes():return old
    if fmt==0x28:
        need(allow_lossy,'ETC1 변경은 손실 변환 허용이 필요합니다.')
    if fmt not in (0x20,0x28,0x29):
        if allow_lossy and fmt in (0x23,0x2a):image=image.point(lambda v:round(v/17)*17)
        return encode(spec,image)
    linear=image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
    if fmt==0x20:
        out=bytearray(w*h*4)
        for y in range(h):
            for x in range(w):
                at=(((y//8)*(w//8)+x//8)*64+MORTON[(y%8)*8+x%8])*4
                out[at:at+4]=linear[(y*w+x)*4:(y*w+x)*4+4][::-1]
        return bytes(out)
    if fmt==0x28:need(all(linear[i]==255 for i in range(3,len(linear),4)),'ETC1 cannot represent transparency')
    block_bytes=8 if fmt==0x28 else 16
    out=bytearray(old if old is not None else bytes(w*h//2 if fmt==0x28 else w*h));pos=0;cache={}
    for ty in range(0,h,8):
        for tx in range(0,w,8):
            for sy,sx in ((0,0),(0,4),(4,0),(4,4)):
                pixels=[tuple(linear[((ty+sy+y)*w+tx+sx+x)*4:((ty+sy+y)*w+tx+sx+x)*4+4]) for y in range(4) for x in range(4)]
                color_at=pos if fmt==0x28 else pos+8
                colors=etc_block(out[color_at:color_at+8])
                if old is None or any(a[:3]!=b[:3] for a,b in zip(pixels,colors)):
                    need(allow_lossy,'ETC1A4 색상 변경은 손실 변환 허용이 필요합니다.')
                    key=tuple(p[:3] for p in pixels)
                    if key not in cache:
                        if len(cache)>=4096:cache.clear()
                        cache[key]=encode_etc_block(pixels)
                    out[color_at:color_at+8]=cache[key]
                if fmt==0x28:pos+=block_bytes;continue
                alpha=0
                for y in range(4):
                    for x in range(4):
                        a=pixels[y*4+x][3]
                        need(allow_lossy or a%17==0,'ETC1A4 알파는 0,17,...,255여야 합니다.')
                        alpha|=round(a/17)<<(4*(x*4+y))
                out[pos:pos+8]=alpha.to_bytes(8,'little');pos+=block_bytes
    return bytes(out)


def rebuild_tpl(data,index,image,allow_lossy=False):
    from n3ds_image_contract import tpl_contract
    tpl_contract(data)
    entries=tpl_info(data)['images'];need(0<=index<len(entries),'Unknown TPL image')
    old_info,old_pixels=texture(data,index);image=image.convert('RGBA')
    if image.size==(old_info['width'],old_info['height']) and decode(old_info,old_pixels).tobytes()==image.tobytes():return data
    pixels=encode_pixels(old_info,image,old_pixels if image.size==(old_info['width'],old_info['height']) else None,allow_lossy)
    table=num(data,8);out=bytearray(data[:12]);put(out,8,12);out.extend(bytes(8*len(entries)))
    headers={};boundaries={len(data)}
    for entry in entries:
        i=entry['index'];head=num(data,table+8*i)
        need(not entry['palette_header_offset'],'Paletted 3DS TPL variant is unsupported')
        boundaries.update((head,entry['payload_offset']))
        headers[i]=len(out);put(out,12+8*i,len(out));out.extend(span(data,head,36))
    ordered=sorted(entries,key=lambda e:e['payload_offset'])
    for entry in ordered:
        i=entry['index'];spec,raw=texture(data,i);base=entry['payload_offset']
        head=headers[i]
        stop=min(at for at in boundaries if at>=spec['pixel_offset']+len(raw))
        tail=data[spec['pixel_offset']+len(raw):stop]
        blob=bytearray(data[base:spec['pixel_offset']]);need(blob[8]==1,'TPL resize requires one mip level')
        if i==index:
            put(blob,4,image.width,2);put(blob,6,image.height,2);put(blob,28,len(pixels));blob.extend(pixels)
        else:blob.extend(raw)
        # Converted REV TPLs can retain non-padding trailing bytes. Preserve
        # these verbatim rather than treating the declared pixels as the file end.
        blob.extend(tail)
        out.extend(bytes([0xe3])*(align(len(out),128)-len(out)));put(out,head+8,len(out));out.extend(blob)
    for entry in entries:
        i=entry['index'];spec,raw=texture(out,i)
        if i==index:
            need((spec['width'],spec['height'])==image.size and raw==pixels,'TPL rebuild verification failed')
        else:need(raw==texture(data,i)[1],'Unedited TPL pixels changed')
    tpl_contract(out)
    return bytes(out)


def replace_rev_resource(data,name,red_index,item_index,payload):
    from rev_structure import inspect
    from n3ds_image_contract import rev_contract
    before_contract=rev_contract(data,name)
    meta=inspect(data,name,'n3ds');need(not meta['errors'],str(meta['errors']))
    red=meta['red'][red_index];item=red['items'][item_index]
    need(item['kind'] in (1,4),'Expected TPL or MGBR resource')
    start=item['offset'];base=item['resource_offset'];end=start+item['size']
    if data[base:end]==payload:return data
    # Preserve every later absolute alignment, including the cut stream boundary.
    delta=align(len(payload)-(end-base),1024);new_size=item['size']+delta
    replacement=bytearray(data[start:base]);replacement.extend(payload)
    replacement.extend(bytes([0xe3])*(new_size-len(replacement)));put(replacement,0,new_size)
    out=bytearray(data[:start])+replacement+data[end:]
    # The observed loader resolves descriptor +12, then walks individual item
    # sizes to kind=0. It does not use descriptor +16 or RED header +0 here.
    # Preserve those converted metadata values even if one coincidentally equals
    # the physical size. See the code.bin evidence in N3DS_IMAGE_CODE_NOTES.md.
    for other in meta['red']:
        if other['offset']>=end:put(out,other['record_offset']+12,other['offset']+delta)
    for cap in meta['captions']:
        if cap['size'] and cap['offset']>=end:put(out,0x50+8*cap['slot'],cap['offset']+delta)
    old_cut=num(data,24);need(old_cut>=end,'Resource overlaps cut boundary');put(out,24,old_cut+delta)
    for cut in meta['cut_resources']:
        need(cut['offset']>=old_cut,'Unknown cut address space');put(out,cut['record_offset']+20,cut['offset']+delta)
    after=inspect(out,name,'n3ds');need(not after['errors'],str(after['errors']))
    after_contract=rev_contract(out,name)
    need(after_contract['tail_read_padding']==before_contract['tail_read_padding'],'Cut tail read behavior changed')
    need(out[old_cut+delta:]==data[old_cut:],'Cut stream changed')
    need([[r['raw_hex'] for r in c['records']] for c in meta['captions']]==[[r['raw_hex'] for r in c['records']] for c in after['captions']],'Captions changed')
    for ri,(a,b) in enumerate(zip(meta['red'],after['red'])):
        for ii,(x,y) in enumerate(zip(a['items'],b['items'])):
            if (ri,ii)==(red_index,item_index):continue
            need(data[x['offset']:x['offset']+x['size']]==out[y['offset']:y['offset']+y['size']],'Unedited RED item changed')
    return bytes(out)
