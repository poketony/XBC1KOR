"""3DS editor rebuild paths; never append stale text pools."""
from xeno_formats import (need,num,put,span,sb_strings,bdat_strings,brlyt_strings,
                          arc_entries,bundle_entries,layout_sections)
from wii_repack import align,verify_text


def bdat_grow(data,changes):
    from bdat_scramble import transform_table
    scrambled=bool(data[4]&2);plain=transform_table(data)
    meta,rows=bdat_strings(plain,'utf-8')
    need(meta['endian']=='little' and meta['flags']==1,'Expected 3DS BDAT')
    need(set(changes)<={r['index'] for r in rows},'Unknown BDAT row')
    if all(rows[i]['text']==text for i,text in changes.items()):return data
    out=bytearray(plain[:meta['string_start']]);pool={}
    for row in rows:
        text=changes.get(row['index'],row['text']);raw=bytes.fromhex(row['raw_hex'])
        if text!=row['text']:
            need(isinstance(text,str) and '\0' not in text,'Invalid text');raw=text.encode('utf-8')
        key=(row['offset'],raw)
        if key not in pool:pool[key]=len(out);out.extend(raw+b'\0')
        put(out,row['pointer_field'],pool[key])
    size=len(out)-meta['string_start'];put(out,28,size)
    tail=plain[meta['string_end']:]
    need(len(tail)<4 and all(v in (0,0xe3) for v in tail),'Unknown BDAT suffix')
    out.extend(bytes([tail[0] if tail else 0])*((-len(out))%4))
    verify_text(rows,bdat_strings(out,'utf-8')[1],changes)
    return transform_table(out,encrypt=True) if scrambled else bytes(out)


def rev_grow(data,name,changes):
    from rev_structure import inspect
    from n3ds_image_contract import rev_contract
    meta=inspect(data,name,'n3ds');need(not meta['errors'],str(meta['errors']))
    slots=[c for c in meta['captions'] if c['size']]
    known={f"{c['slot']}:{i}":r for c in slots for i,r in enumerate(c['records'])}
    need(set(changes)<=set(known),'Unknown caption row')
    if all(known[key]['text']==text for key,text in changes.items()):return data
    before_contract=rev_contract(data,name)
    need(slots,'No caption slots')
    slots=sorted(slots,key=lambda c:c['offset']);start=slots[0]['offset'];end=num(data,24)
    need(meta['metadata_end']<=start<end<=len(data),'Unknown caption region')
    need(all(r['offset']+r['physical_size']<=start for r in meta['red']),'RED overlaps captions')
    need(all(c['offset']>=end for c in meta['cut_resources']),'Cut precedes caption end')
    out=bytearray(data[:start]);cursor=start
    for slot in slots:
        need(slot['offset']>=cursor and all(v in (0,0xe3) for v in data[cursor:slot['offset']]),'Unknown bytes between caption slots')
        at=len(out)
        for i,row in enumerate(slot['records']):
            key=f"{slot['slot']}:{i}";text=changes.get(key,row['text'])
            need(isinstance(text,str) and '\0' not in text,'Invalid caption')
            raw=text.encode('utf-8') if text!=row['text'] else bytes.fromhex(row['raw_hex'])
            out.extend(row['time_raw'].to_bytes(2,'big')+raw+b'\0')
        out.extend(b'\xb8\x18');put(out,0x50+slot['slot']*8,at);put(out,0x54+slot['slot']*8,len(out)-at)
        cursor=slot['offset']+slot['size']
    need(cursor<=end and all(v in (0,0xe3) for v in data[cursor:end]),'Unknown caption padding')
    out.extend(bytes([0xe3])*(align(len(out),1024)-len(out)));new_end=len(out);delta=new_end-end
    put(out,24,new_end)
    for cut in meta['cut_resources']:put(out,cut['record_offset']+20,cut['offset']+delta)
    out.extend(data[end:]);after=inspect(out,name,'n3ds');need(not after['errors'],str(after['errors']))
    for c in after['captions']:
        for i,row in enumerate(c['records']):
            key=f"{c['slot']}:{i}";need(row['text']==changes.get(key,known[key]['text']),'Caption verification failed')
    need(out[new_end:]==data[end:],'REV resources changed')
    after_contract=rev_contract(out,name)
    need(after_contract['tail_read_padding']==before_contract['tail_read_padding'],'Cut tail read behavior changed')
    return bytes(out)


def sb_grow(data,changes):
    plain,endian,rows=sb_strings(data,'utf-8');need(endian=='little','Expected 3DS SB')
    need(set(changes)<={r['index'] for r in rows},'Unknown SB row')
    changes={i:t for i,t in changes.items() if t!=rows[i]['text']}
    if not changes:return data
    for text in changes.values():need(isinstance(text,str) and '\0' not in text,'Invalid text')
    start,end=num(plain,24,endian='little'),num(plain,28,endian='little')
    base=start+12;old_width=num(plain,start+8,endian='little');n=len(rows)
    old_pool=base+n*old_width
    need(old_pool<=end<=len(plain),'Invalid SB text section')
    for width in (2,4):
        section=bytearray(plain[start:start+12]);put(section,8,width,endian='little')
        section.extend(bytes(n*width));pool={};pointers=[]
        for row in rows:
            raw=changes[row['index']].encode('utf-8') if row['index'] in changes else bytes.fromhex(row['raw_hex'])
            key=(row['offset'],raw)
            if key not in pool:
                pool[key]=len(section)-12;section.extend(raw+b'\0')
            pointers.append(pool[key])
        section.extend(bytes(align(len(section))-len(section)))
        delta=len(section)-(end-start)
        if max(pointers,default=0)<1<<(width*8):break
    for i,p in enumerate(pointers):put(section,12+i*width,p,width,'little')
    out=bytearray(plain[:start])+section+plain[end:]
    for field in range(12,60,4):
        value=num(plain,field,endian='little')
        if value>=end:put(out,field,value+delta,endian='little')
    if data[6]&2:
        for field,endfield in ((12,16),(24,28)):
            at,stop=num(out,field,endian='little'),num(out,endfield,endian='little')
            h,count,size=(num(out,at+k,endian='little') for k in (0,4,8))
            for p in range(at+h+count*size,stop-3,4):
                v=num(out,p,endian='little');put(out,p,((v<<2)|(v>>30))&0xffffffff,endian='little')
        out[6]=data[6]
    decoded,_,after=sb_strings(out,'utf-8');verify_text(rows,after,changes)
    need(decoded[end+delta:]==plain[end:],'Non-text SB suffix changed')
    return bytes(out)


def container_grow(data,typ,entries,replacements):
    need(typ in ('bundle','arc'),'Unsupported 3DS container')
    need(set(replacements)<={e['index'] for e in entries},'Unknown container entry')
    if not entries:return data
    original=lambda e:span(data,e['offset'],e['size'])
    if all(payload==original(next(e for e in entries if e['index']==index)) for index,payload in replacements.items()):return data
    boundary=num(data,8) if typ=='arc' else 4
    need(boundary>0 and boundary&(boundary-1)==0,'Invalid archive alignment')
    out=bytearray(data[:min(e['offset'] for e in entries)])
    for entry in sorted(entries,key=lambda e:e['offset']):
        out.extend(bytes(align(len(out),boundary)-len(out)))
        payload=replacements.get(entry['index'],original(entry))
        if typ=='bundle':put(out,8+4*entry['index'],len(out))
        else:
            put(out,entry['offset_field'],len(out));put(out,entry['size_field'],len(payload))
        out.extend(payload)
    if typ=='bundle':put(out,4,len(out))
    checked={'bundle':bundle_entries,'arc':arc_entries}[typ](out)
    need(len(checked)==len(entries),'Container count changed')
    for entry,updated in zip(entries,checked):
        expected=replacements.get(entry['index'],original(entry));actual=span(out,updated['offset'],updated['size'])
        need(actual[:len(expected)]==expected and not any(actual[len(expected):]),'Container rebuild mismatch')
    return bytes(out)


def brlyt_grow(data,changes,preserve_capacity=False):
    meta,rows=brlyt_strings(data);endian,sections=layout_sections(data)
    need(endian=='little','Expected 3DS BRLYT')
    need(set(changes)<={r['index'] for r in rows},'Unknown BRLYT string index')
    for text in changes.values():need(isinstance(text,str) and '\0' not in text,'Invalid BRLYT text')
    by_section={r['section_offset']:r for r in rows}
    changed={r['section_offset']:changes[r['index']].encode('utf-16-le')+b'\0\0'
             for r in rows if r['index'] in changes and changes[r['index']]!=r['text']}
    if not changed:return data
    out=bytearray(data[:num(data,12,2,'little')])
    for s in sections:
        part=bytearray(span(data,s['offset'],s['size']))
        if s['offset'] in changed:
            row=by_section[s['offset']];text=changed[s['offset']]
            at=row['offset']-s['offset'];stop=at+row['capacity_bytes']
            capacity=max(len(text),row['capacity_bytes']) if preserve_capacity else len(text)
            # txt1's only variable-length field is its text buffer. Preserve tail bytes.
            tail=part[stop:]
            if not any(tail):tail=b''
            part=part[:at]+text+bytes(capacity-len(text))+tail
            part.extend(bytes(align(len(part))-len(part)))
            put(part,4,len(part),endian='little');put(part,76,capacity,2,'little');put(part,78,len(text),2,'little');put(part,88,at,endian='little')
        out.extend(part)
    put(out,8,len(out),endian='little');verify_text(rows,brlyt_strings(out)[1],changes)
    return bytes(out)
