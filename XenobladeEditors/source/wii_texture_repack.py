"""Rebuild Wii TPL buffers and their enclosing REV RED resource directory."""
from xeno_formats import num,put,span,need,tpl_info
from wii_repack import align
from PIL import Image


def pixel_bytes(width,height,fmt):
    need(0<width<=4096 and 0<height<=4096,'Texture dimensions outside supported GX range')
    need(fmt in (0,1,2,3,4,5,6,8,9,14),'Unsupported GX texture format')
    tw,th=(8,8) if fmt in (0,8,14) else (8,4) if fmt in (1,2,9) else (4,4)
    return ((width+tw-1)//tw)*((height+th-1)//th)*(64 if fmt==6 else 32)


def rebuild_tpl(data,index,image,allow_lossy=False,regenerate_palette=False):
    from wii_editor_images import catalog,replace,palette_color
    info=tpl_info(data);need(info['endian']=='big','Expected Wii TPL')
    entries=info['images'];need(0<=index<len(entries),'Unknown TPL image')
    new_palette=None
    if regenerate_palette:
        need(allow_lossy,'New palettes require explicit lossy conversion permission')
        fmt=entries[index]['format_raw'];need(fmt in (8,9),'Only C4/C8 TPL images use this palette editor')
        colors=16 if fmt==8 else 256
        quantized=image.convert('RGBA').quantize(colors=colors,method=Image.Quantize.FASTOCTREE).convert('RGBA')
        values=[]
        for r,g,b,a in sorted(set(quantized.getdata())):
            value=(0x8000|(round(r*31/255)<<10)|(round(g*31/255)<<5)|round(b*31/255)) if a==255 else ((round(a*7/255)<<12)|(round(r/17)<<8)|(round(g/17)<<4)|round(b/17))
            if value not in values:values.append(value)
        new_palette=b''.join(v.to_bytes(2,'big') for v in values)
    table=num(data,8,endian='big');out=bytearray(data[:12]);put(out,8,12,endian='big')
    out.extend(bytes(len(entries)*8));pending=[];specs=catalog(data,'tpl','TPL')
    for e in entries:
        i=e['index'];old_header=num(data,table+8*i,endian='big')
        head=bytearray(span(data,old_header,36));header_at=len(out)
        levels=head[34]+1;need(levels<=13,'Unsupported TPL mip count')
        put(out,12+i*8,header_at,endian='big');out.extend(head)
        if i==index:
            width,height=image.size;pixels=bytearray()
            for level in range(levels):
                w,h=max(1,width>>level),max(1,height>>level)
                length=pixel_bytes(w,h,e['format_raw'])
                spec=dict(specs[i],offset=0,width=w,height=h)
                if new_palette is not None:spec['palette']=[palette_color(v,2) for v in values]
                mip=image if level==0 else image.resize((w,h),Image.Resampling.NEAREST)
                pixels.extend(replace(bytes(length),spec,mip,allow_lossy))
            put(out,header_at,height,2,'big');put(out,header_at+2,width,2,'big')
        else:
            length=sum(pixel_bytes(max(1,e['width']>>level),max(1,e['height']>>level),e['format_raw']) for level in range(levels))
            pixels=span(data,e['payload_offset'],length)
        pending.append((header_at+8,pixels))
        palette=e['palette_header_offset']
        if palette:
            palette_at=len(out);put(out,12+i*8+4,palette_at,endian='big')
            out.extend(span(data,palette,12))
            count=num(data,palette,2,'big');address=num(data,palette+8,endian='big')
            if i==index and new_palette is not None:
                put(out,palette_at,len(values),2,'big');put(out,palette_at+4,2,endian='big')
                pending.append((palette_at+8,new_palette))
            else:pending.append((palette_at+8,span(data,address,count*2)))
    for field,payload in pending:
        out.extend(bytes(align(len(out),32)-len(out)));put(out,field,len(out),endian='big');out.extend(payload)
    checked=tpl_info(out)
    need((checked['images'][index]['width'],checked['images'][index]['height'])==image.size,'TPL dimension mismatch')
    return bytes(out)


def replace_rev_resource(data,name,red_index,item_index,payload):
    from rev_structure import inspect,xor_view,red_items
    meta=inspect(data,name,'wii');need(not meta['errors'],str(meta['errors']))
    red=meta['red'][red_index];need(0<=item_index<len(red['items']),'Unknown RED item')
    selected=red['items'][item_index]
    old=span(data,selected['resource_offset'],selected['offset']+selected['size']-selected['resource_offset'])
    if old==payload:return data
    block=bytearray(span(data,red['offset'],32))
    for i,item in enumerate(red['items']):
        if i!=item_index:block.extend(span(data,item['offset'],item['size']));continue
        at=len(block);block.extend(span(data,item['offset'],32));block.extend(payload)
        block.extend(bytes(align(len(block),32)-len(block)))
        put(block,at,len(block)-at,endian='big')
    block.extend(span(data,red['offset']+red['physical_size']-32,32))
    put(block,0,len(block)-32,endian='big')
    need(red_items(block,0,'big')['physical_size']==len(block),'Rebuilt RED structure mismatch')
    plain=xor_view(data,name);boundary=num(plain,16,endian='big')+2048;loaded=num(plain,24,endian='big')
    need(num(plain,20,endian='big')==loaded-boundary,'Unknown REV resource span')
    prefix=bytearray(plain[:boundary]);body=bytearray();cursor=boundary
    for r in sorted(meta['red'],key=lambda r:r['offset']):
        need(r['offset']==cursor,'Non-contiguous RED blocks need review')
        at=boundary+len(body)
        part=block if r is red else span(data,r['offset'],r['physical_size'])
        put(prefix,r['record_offset']+12,at,endian='big')
        put(prefix,r['record_offset']+16,len(part),endian='big')
        body.extend(part);cursor=r['offset']+r['physical_size']
    need(cursor<=loaded and not any(data[cursor:loaded]),'Unknown bytes after RED blocks')
    body.extend(bytes(align(len(body),2048)-len(body)));new_loaded=boundary+len(body);delta=new_loaded-loaded
    put(prefix,12,len(data)+delta,endian='big');put(prefix,20,len(body),endian='big');put(prefix,24,new_loaded,endian='big')
    for c in meta['cut_resources']:
        need(c['offset']>=loaded,'Cut overlaps RED region')
        put(prefix,c['record_offset']+20,c['offset']+delta,endian='big')
    result=xor_view(prefix,name)+body+data[loaded:]
    after=inspect(result,name,'wii');need(not after['errors'],str(after['errors']))
    need(result[new_loaded:]==data[loaded:],'Opaque cut data changed')
    for original,updated in zip(meta['red'],after['red']):
        if original is red:continue
        need(span(data,original['offset'],original['physical_size'])==span(result,updated['offset'],updated['physical_size']),'Unedited RED changed')
    return bytes(result)
