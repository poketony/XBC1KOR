"""3DS TPL/font sheets and REV resource image previews."""
from pathlib import Path
from xeno_formats import tpl_info,need,span
from texture_tool import texture,decode,replace as replace_tpl
from font_writer import sheets,replace_sheets
from rev_structure import inspect
from rev_rem_texture import textures,decode_level


def catalog(data,typ,name):
    if typ=='tpl':return [dict(texture(data,i)[0],name=f'{name} / image {i}',kind='tpl') for i in range(len(tpl_info(data)['images']))]
    if typ=='brfna':return [dict(name=f"{name} / sheet {s['index']}",width=s['image'].width,height=s['image'].height,kind='font',index=s['index'],image=s['image']) for s in sheets(data)]
    result=[]
    def add_rem(payload,base,label):
        for tex in textures(payload):
            for level in tex['levels']:result.append(dict(level,name=f"{label}/{tex['name']} / mip {level['level']}",offset=base+level['offset'],kind='rem'))
    if typ=='mgbr':add_rem(data,0,name)
    elif typ=='rev':
        meta=inspect(data,Path(name).name,'n3ds');need(not meta['errors'],str(meta['errors']))
        for red_index,red in enumerate(meta['red']):
            for item_index,item in enumerate(red['items']):
                if item['kind'] not in (1,4):continue
                at=item['resource_offset'];payload=data[at:item['offset']+item['size']]
                if item['kind']==1:
                    for i in range(len(tpl_info(payload)['images'])):
                        result.append(dict(texture(payload,i)[0],name=item['name']+f' / image {i}',kind='rev_tpl',base=at,length=len(payload),rev_name=Path(name).name,red_index=red_index,item_index=item_index))
                elif item['kind']==4:add_rem(payload,at,item['name'])
    return result


def preview(data,info):
    if info['kind']=='font':return info['image'].copy()
    if info['kind']=='rem':return decode_level(data,info)
    payload=data[info['base']:info['base']+info['length']] if info['kind']=='rev_tpl' else data
    spec,raw=texture(payload,info['index']);return decode(spec,raw)


def replace(data,info,image,allow_lossy=False):
    if info['kind']=='font':
        old=info['image'];need(image.mode==old.mode,'폰트 PNG는 내보낸 색상 모드(L/RGBA)를 유지해 주세요.')
        return replace_sheets(data,{info['index']:image},grow=True)
    from n3ds_texture_repack import rebuild_tpl,replace_rev_resource,encode_pixels
    if info['kind']=='tpl':
        if image.size==(info['width'],info['height']):return replace_tpl(data,info['index'],image.convert('RGBA'),allow_lossy)
        return rebuild_tpl(data,info['index'],image,allow_lossy)
    if info['kind']=='rev_tpl':
        at=info['base'];end=at+info['length']
        if image.size==(info['width'],info['height']):
            edited=replace_tpl(data[at:end],info['index'],image.convert('RGBA'),allow_lossy)
            return data[:at]+edited+data[end:]
        edited=rebuild_tpl(data[at:end],info['index'],image,allow_lossy)
        return replace_rev_resource(data,info['rev_name'],info['red_index'],info['item_index'],edited)
    need(info['kind']=='rem','Unknown image owner')
    need(image.size==(info['width'],info['height']),'MGBR 이미지 크기 변경은 아직 지원하지 않습니다. 현재 mip와 같은 크기의 PNG를 사용해 주세요.')
    at=info['offset'];old=span(data,at,info['bytes']);pixels=encode_pixels(info,image,old,allow_lossy)
    need(len(pixels)==len(old),'MGBR mip length changed')
    return data[:at]+pixels+data[at+len(old):]
