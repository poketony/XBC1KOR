"""Wii image catalog and exact packed-pixel replacement (no container relocation)."""
from xeno_formats import tpl_info,need,span,num
from PIL import Image
from rev_structure import inspect
from rev_rem_texture import textures
from rev_wii_texture import decode_gx,rgb565


def catalog(data,typ,name):
    result=[]
    def add_tpl(payload,base,label,**owner):
        for e in tpl_info(payload)['images']:
            info={'name':label+f" / image {e['index']}",'offset':base+e['payload_offset'],
                  'width':e['width'],'height':e['height'],'format':e['format_raw'],
                  'tpl_index':e['index'],'tpl_base':base,'tpl_size':len(payload),**owner}
            if e['format_raw'] in (8,9):
                p=e['palette_header_offset'];need(p>0,'Missing TPL palette')
                count=num(payload,p,2,'big');fmt=num(payload,p+4,4,'big');at=num(payload,p+8,4,'big')
                need(fmt in (0,1,2),'Unsupported palette format')
                info['palette']=[palette_color(int.from_bytes(span(payload,at+2*i,2),'big'),fmt) for i in range(count)]
            result.append(info)
    def add_rem(payload,base,label):
        for texture in textures(payload):
            for level in texture['levels']:
                result.append(dict(level,offset=base+level['offset'],name=label+'/'+texture['name']+f" / mip {level['level']}"))
    if typ=='tpl':add_tpl(data,0,name)
    elif typ=='brfna':
        from compare_fonts import read_font_data
        font=span(data,0,num(data,8,endian='big'))
        metadata,_,_,_,sheets=read_font_data(font)
        for i,sheet in enumerate(sheets):
            result.append({'name':f"{name} / sheet {i} · {metadata['glyph_count']} glyphs",
                           'width':sheet.width,'height':sheet.height,'format':metadata['format'],
                           'font_sheet':i,'font_preview':sheet})
    elif typ=='bres':add_rem(data,0,name)
    elif typ=='rev':
        metadata=inspect(data,name,'wii');need(not metadata['errors'],str(metadata['errors']))
        for red in metadata['red']:
            for item_index,item in enumerate(red['items']):
                if item['kind'] not in (1,4):continue
                at=item['resource_offset'];payload=data[at:item['offset']+item['size']]
                label=f"RED {red['index']} / {item['name']}"
                if item['kind']==1:add_tpl(payload,at,label,rev_name=name,red_index=metadata['red'].index(red),item_index=item_index)
                else:add_rem(payload,at,label)
    return result


def palette_color(v,fmt):
    if fmt==0:return (v&255,)*3+(v>>8,)
    if fmt==1:return rgb565(v)+(255,)
    if v&0x8000:
        return tuple((c<<3)|(c>>2) for c in ((v>>10)&31,(v>>5)&31,v&31))+(255,)
    a=(v>>12)&7
    return tuple(((v>>s)&15)*17 for s in (8,4,0))+((a<<5)|(a<<2)|(a>>1),)


def preview(data,info):
    if 'font_sheet' in info:
        sheet=info['font_preview']
        return Image.merge('RGBA',(sheet,sheet,sheet,sheet))
    fmt=info['format']
    if fmt in (0,2,8,9):
        w,h=info['width'],info['height'];need(0<w<=4096 and 0<h<=4096,'Texture dimensions limit')
        tw,th=(8,8) if fmt in (0,8) else (8,4)
        raw=span(data,info['offset'],((w+7)//8)*((h+th-1)//th)*32)
        out=Image.new('RGBA',(w,h));pix=out.load();at=0
        for ty in range(0,h,th):
            for tx in range(0,w,tw):
                for i in range(tw*th):
                    v=(raw[at+i//2]>>(4 if i%2==0 else 0))&15 if fmt in (0,8) else raw[at+i]
                    if fmt==0:color=(v*17,)*4
                    elif fmt==2:color=((v&15)*17,)*3+((v>>4)*17,)
                    else:
                        need(v<len(info['palette']),'Palette index out of range');color=info['palette'][v]
                    x,y=tx+i%tw,ty+i//tw
                    if x<w and y<h:pix[x,y]=color
                at+=32
        return out
    return decode_gx(data,info['offset'],info['width'],info['height'],info['format'])


def cmpr_block(pixels):
    opaque=[p for p in pixels if p[3]>=128]
    if not opaque:return bytes.fromhex('00000000ffffffff')
    first,second=max(((a,b) for a in opaque for b in opaque),key=lambda pair:sum((a-b)**2 for a,b in zip(pair[0][:3],pair[1][:3])))
    pack=lambda p:(round(p[0]*31/255)<<11)|(round(p[1]*63/255)<<5)|round(p[2]*31/255)
    a,b=pack(first),pack(second);transparent=len(opaque)!=16
    a,b=(min(a,b),max(a,b)) if transparent else (max(a,b),min(a,b))
    ca,cb=rgb565(a),rgb565(b);colors=[ca,cb]
    if a>b:colors.extend([tuple((5*x+3*y)//8 for x,y in zip(ca,cb)),tuple((3*x+5*y)//8 for x,y in zip(ca,cb))])
    else:colors.append(tuple((x+y)//2 for x,y in zip(ca,cb)))
    indices=[3 if p[3]<128 else min(range(len(colors)),key=lambda i:sum((v-c)**2 for v,c in zip(p[:3],colors[i]))) for p in pixels]
    return a.to_bytes(2,'big')+b.to_bytes(2,'big')+bytes(sum(indices[y*4+x]<<(6-2*x) for x in range(4)) for y in range(4))


def replace(data,info,image,allow_lossy=False,regenerate_palette=False):
    if 'font_sheet' in info:
        from wii_font import replace_sheets
        if image.mode=='RGBA':
            r,g,b,a=image.split()
            need(r.tobytes()==g.tobytes()==b.tobytes()==a.tobytes(),'Font RGBA must have equal channels; use an L grayscale sheet')
            image=a
        need(image.mode=='L','Font image must be L grayscale or equal-channel RGBA')
        if allow_lossy:image=image.point(lambda v:round(v/17)*17)
        return replace_sheets(data,{info['font_sheet']:image})
    original=preview(data,info);image=image.convert('RGBA')
    if image.size!=original.size or regenerate_palette:
        need('tpl_index' in info,'BRRES 텍스처 크기 변경은 아직 지원하지 않습니다.')
        from wii_texture_repack import rebuild_tpl,replace_rev_resource
        tpl=span(data,info['tpl_base'],info['tpl_size'])
        rebuilt=rebuild_tpl(tpl,info['tpl_index'],image,allow_lossy,regenerate_palette)
        if 'rev_name' in info:return replace_rev_resource(data,info['rev_name'],info['red_index'],info['item_index'],rebuilt)
        need(info['tpl_base']==0,'Unknown TPL owner')
        return rebuilt
    if image.tobytes()==original.tobytes():return data
    fmt=info['format']
    if fmt==14:
        need(allow_lossy,'CMPR 변경은 손실 압축을 사용합니다. 손실 압축 허용을 선택해 주세요.')
        out=bytearray(data);at=info['offset'];w,h=image.size;pix=image.load();old=original.load()
        for ty in range(0,h,8):
            for tx in range(0,w,8):
                for sy,sx in ((0,0),(0,4),(4,0),(4,4)):
                    coords=[(tx+sx+x,ty+sy+y) for y in range(4) for x in range(4)]
                    if any(x<w and y<h and pix[x,y]!=old[x,y] for x,y in coords):
                        pixels=[pix[x,y] if x<w and y<h else (0,0,0,0) for x,y in coords]
                        out[at:at+8]=cmpr_block(pixels)
                    at+=8
        return bytes(out)
    need(fmt in (0,1,2,3,4,5,6,8,9),'Unsupported GX format')
    w,h=image.size;tw,th=(8,8) if fmt in (0,8) else (8,4) if fmt in (1,2,9) else (4,4);out=bytearray(data);at=info['offset']
    palette=info.get('palette',[]);indices={c:i for i,c in reversed(list(enumerate(palette)))};nearest={}
    pix=image.load();old=original.load()
    for ty in range(0,h,th):
        for tx in range(0,w,tw):
            for y in range(th):
                for x in range(tw):
                    i=y*tw+x
                    if tx+x>=w or ty+y>=h or pix[tx+x,ty+y]==old[tx+x,ty+y]:continue
                    r,g,b,a=pix[tx+x,ty+y]
                    if fmt in (0,2,8,9):
                        if fmt in (8,9):
                            color=(r,g,b,a);value=indices.get(color)
                            if value is None:
                                need(allow_lossy,'원본 팔레트에 없는 색입니다. 손실 변환을 허용하면 가장 가까운 색을 사용합니다.')
                                if color not in nearest:nearest[color]=min(range(len(palette)),key=lambda j:sum((a-b)**2 for a,b in zip(color,palette[j])))
                                value=nearest[color]
                        else:
                            need(r==g==b and (fmt!=0 or a==r),'I4는 R=G=B=A, IA4는 회색조여야 합니다.')
                            value=round(r/17) if fmt==0 else (round(a/17)<<4)|round(r/17)
                        if fmt in (0,8):
                            shift=4 if i%2==0 else 0;out[at+i//2]=(out[at+i//2]&~(15<<shift))|(value<<shift)
                        else:out[at+i]=value
                        continue
                    if fmt==6:
                        out[at+2*i:at+2*i+2]=bytes((a,r));out[at+32+2*i:at+34+2*i]=bytes((g,b));continue
                    if fmt==1:
                        need(r==g==b==a,'I8 이미지는 R=G=B=A 값이어야 합니다.');out[at+i]=r;continue
                    if fmt==3:
                        need(r==g==b,'IA8 이미지는 회색조여야 합니다.');value=a<<8|r
                    elif fmt==4:
                        need(a==255,'RGB565는 투명도를 지원하지 않습니다.');value=(round(r*31/255)<<11)|(round(g*63/255)<<5)|round(b*31/255)
                    elif a==255:value=0x8000|(round(r*31/255)<<10)|(round(g*31/255)<<5)|round(b*31/255)
                    else:value=(round(a*7/255)<<12)|(round(r/17)<<8)|(round(g/17)<<4)|round(b/17)
                    out[at+2*i:at+2*i+2]=value.to_bytes(2,'big')
            at+=64 if fmt==6 else 32
    return bytes(out)
