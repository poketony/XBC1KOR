"""Bounds checks derived from the analyzed REV/TPL loader paths.

This models address arithmetic, not ARM execution or GPU rendering. See
N3DS_IMAGE_CODE_NOTES.md for code.bin offsets and the limits of the evidence.
"""
from xeno_formats import num,need,span,tpl_info


def tpl_contract(data):
    from texture_tool import texture
    images=tpl_info(data)['images'];result=[]
    for entry in images:
        head=num(data,num(data,8)+entry['index']*8)
        base=num(data,head+8)
        need(base%128==0,'TPL payload is not 128-byte aligned')
        need(data[base:base+4]==b'!xtt','Expected !xtt texture')
        flags=num(data,base+18,2)
        need(not flags&3,'Serialized texture contains initialized runtime pointers/state')
        # 0x31f6c..0x31f78: xtrd-relative pixel pointer, not TPL-relative.
        pixel=base+12+num(data,base+24)
        size=num(data,base+28)
        span(data,pixel,size)
        need(pixel%128==0,'Texture pixels are not 128-byte aligned')
        info,raw=texture(data,entry['index'])
        need(pixel==info['pixel_offset'] and size>=len(raw),'Texture loader/parser disagreement')
        # 0x31fe8..0x31ff4: ror16 of width | height<<16.
        dimensions=num(data,base+4)
        gpu_word=((dimensions>>16)|(dimensions<<16))&0xffffffff
        result.append({'index':entry['index'],'image_header':head,'payload':base,
            'pixels':pixel,'pixel_bytes':size,'logical_width':num(data,head+2,2),
            'logical_height':num(data,head,2),'physical_width':info['width'],
            'physical_height':info['height'],'gpu_dimensions_word':gpu_word})
    return result


def rev_contract(data,name):
    from rev_structure import inspect
    meta=inspect(data,name,'n3ds');need(not meta['errors'],str(meta['errors']))
    resident=num(data,24);span(data,0,resident)
    need(meta['metadata_end']<=resident,'Metadata outside resident read')
    resources=[]
    for red in meta['red']:
        # 0x21e650..0x21e664: descriptor +12 is relative to REV base.
        base=num(data,red['record_offset']+12)
        need(base==red['offset'],'RED descriptor address mismatch')
        at=base+32;items=[]
        for item in red['items']:
            need(at==item['offset'],'RED item walk disagrees with parser')
            size=num(data,at);kind=num(data,at+4,2)
            need(size>=32 and at+size<=resident,'RED item outside resident read')
            if kind in (1,4):
                # 0x11499c/0x1149c4: (item + 0x9f) & ~0x7f.
                payload=(at+0x9f)&~0x7f
                need(payload==item['resource_offset'],'RED resource alignment mismatch')
                if kind==1:tpl_contract(data[payload:at+size])
                items.append({'kind':kind,'offset':at,'payload':payload,'size':size})
            at+=size
        need(at+32<=resident and num(data,at+4,2)==0,'RED terminator outside resident read')
        resources.append({'offset':base,'items':items})
    for cap in meta['captions']:
        if cap['size']:need(cap['offset']+cap['size']<=resident,'Caption outside resident read')
    tail_reads=[]
    for cut in meta['cut_resources']:
        need(cut['offset']>=resident,'Cut overlaps resident read')
        span(data,cut['offset'],0x48)
        # Original converted files have a 0..127 byte final read discrepancy.
        # Record it; the OS short-read behavior is not modeled here.
        end=cut['offset']+cut['size_field']
        need(end<=len(data)+127,'Cut read exceeds observed original tail discrepancy')
        if end>len(data):tail_reads.append(end-len(data))
    return {'resident_bytes':resident,'resources':resources,'cut_count':len(meta['cut_resources']),
            'tail_read_padding':tail_reads}
