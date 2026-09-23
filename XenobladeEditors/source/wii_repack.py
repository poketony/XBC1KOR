"""Wii relocation writers. Structural checks do not replace game testing."""
import zlib
from xeno_formats import (need,num,put,span,sb_strings,bdat_strings,bundle_entries,
                         brlyt_strings,layout_sections,dap_decode,arc_entries,u8_entries,dap_entries)


def align(n,a=4):return (n+a-1)//a*a


def bdat_grow(data,changes):
    meta,rows=bdat_strings(data,'cp932')
    need(meta['endian']=='big' and meta['flags']==0,'Expected Wii flag-0 BDAT')
    need(set(changes)<={r['index'] for r in rows},'Unknown BDAT row')
    if all(rows[i]['text']==text for i,text in changes.items()):return data
    out=bytearray(data[:meta['string_start']]);pool={}
    for row in rows:
        text=changes.get(row['index'],row['text'])
        raw=bytes.fromhex(row['raw_hex'])
        if text!=row['text']:
            need(isinstance(text,str) and '\0' not in text,'Invalid text')
            raw=text.encode('cp932')
        # Preserve existing sharing; different edits to a shared string split it.
        key=(row['offset'],raw)
        if key not in pool:
            need(len(out)<=65535,'Rebuilt BDAT string address exceeds u16')
            pool[key]=len(out);out.extend(raw+b'\0')
        put(out,row['pointer_field'],pool[key],2,'big')
    verify_text(rows,bdat_strings(out,'cp932')[1],changes)
    return bytes(out)


def verify_text(before,after,changes):
    need(len(before)==len(after),'String count changed')
    for a,b in zip(before,after):
        if a['index'] in changes:need(b['text']==changes[a['index']],'Edited string mismatch')
        else:need(a['raw_hex']==b['raw_hex'],'Unedited string bytes changed')


def sb_grow(data,changes):
    plain,endian,rows=sb_strings(data,'cp932');need(endian=='big','Expected Wii SB')
    need(set(changes)<={r['index'] for r in rows},'Unknown SB row')
    changes={i:t for i,t in changes.items() if t!=rows[i]['text']}
    if not changes:return data
    for text in changes.values():need(isinstance(text,str) and '\0' not in text,'Invalid text')
    start,end=num(plain,24,endian='big'),num(plain,28,endian='big')
    base=start+12;old_width=num(plain,start+8,endian='big');n=len(rows)
    old_pool=base+n*old_width
    need(old_pool<=end<=len(plain),'Invalid SB text section')
    for width in (2,4):
        section=bytearray(plain[start:start+12]);put(section,8,width,endian='big')
        section.extend(bytes(n*width));pool={};pointers=[]
        for row in rows:
            raw=changes[row['index']].encode('cp932') if row['index'] in changes else bytes.fromhex(row['raw_hex'])
            key=(row['offset'],raw)
            if key not in pool:
                pool[key]=len(section)-12;section.extend(raw+b'\0')
            pointers.append(pool[key])
        section.extend(bytes(align(len(section))-len(section)))
        delta=len(section)-(end-start)
        if max(pointers,default=0)<1<<(width*8):break
    for i,p in enumerate(pointers):put(section,12+i*width,p,width,'big')
    out=bytearray(plain[:start])+section+plain[end:]
    for field in range(12,60,4):
        value=num(plain,field,endian='big')
        if value>=end:put(out,field,value+delta,endian='big')
    if data[6]&2:
        for field,endfield in ((12,16),(24,28)):
            at,stop=num(out,field,endian='big'),num(out,endfield,endian='big')
            h,count,size=(num(out,at+k,endian='big') for k in (0,4,8))
            for p in range(at+h+count*size,stop-3,4):
                v=num(out,p,endian='big');put(out,p,((v<<2)|(v>>30))&0xffffffff,endian='big')
        out[6]=data[6]
    decoded,_,after=sb_strings(out,'cp932');verify_text(rows,after,changes)
    need(decoded[end+delta:]==plain[end:],'Non-text SB suffix changed')
    return bytes(out)


def container_grow(data,typ,entries,replacements):
    need(set(replacements)<={e['index'] for e in entries},'Unknown container entry')
    if typ=='bundle':
        if all(payload==span(data,entries[index]['offset'],entries[index]['size']) for index,payload in replacements.items()):return data
        out=bytearray(data[:entries[0]['offset']])
        for e in entries:
            out.extend(bytes(align(len(out))-len(out)))
            put(out,8+4*e['index'],len(out),endian='big')
            out.extend(replacements.get(e['index'],span(data,e['offset'],e['size'])))
        put(out,4,len(out),endian='big')
        checked=bundle_entries(out)
        for e,c in zip(entries,checked):
            p=replacements.get(e['index'],span(data,e['offset'],e['size']))
            observed=span(out,c['offset'],c['size'])
            need(observed[:len(p)]==p and not any(observed[len(p):]),'Bundle payload mismatch')
        return bytes(out)
    need(typ in ('u8','arc','dap'),'Unsupported container rebuild')
    need(set(replacements)<={e['index'] for e in entries},'Unknown container entry')
    if not entries:return data
    original=lambda e:dap_decode(data,e) if typ=='dap' else span(data,e['offset'],e['size'])
    if all(payload==original(next(e for e in entries if e['index']==index)) for index,payload in replacements.items()):return data
    boundary=num(data,8,endian='big') if typ=='arc' else 32
    need(boundary>0 and boundary&(boundary-1)==0,'Invalid archive alignment')
    out=bytearray(data[:min(e['offset'] for e in entries)])
    for e in sorted(entries,key=lambda e:e['offset']):
        out.extend(bytes(align(len(out),boundary)-len(out)));at=len(out)
        if typ=='dap':
            if e['index'] in replacements and replacements[e['index']]!=original(e):
                payload=replacements[e['index']];packed=zlib.compress(payload,9)
                block=data[e['offset']:e['offset']+4]+len(payload).to_bytes(4,'big')+packed
                put(out,e['record']+12,len(block)-4,endian='big')
                put(out,e['record']+16,len(payload),endian='big')
            else:block=span(data,e['offset'],e['size'])
            put(out,e['record']+8,at,endian='big')
        else:
            block=replacements.get(e['index'],span(data,e['offset'],e['size']))
            put(out,e['offset_field'],at,endian='big');put(out,e['size_field'],len(block),endian='big')
        out.extend(block)
    parser={'u8':u8_entries,'arc':arc_entries,'dap':dap_entries}[typ]
    checked=parser(out);need(len(checked)==len(entries),'Container entry count changed')
    by_id={e['index']:e for e in checked}
    for e in entries:
        updated=by_id[e['index']]
        need(updated['name']==e['name'],'Container entry name changed')
        if typ=='dap' and e['index'] not in replacements:
            need(span(out,updated['offset'],updated['size'])==span(data,e['offset'],e['size']),'Unedited compressed stream changed')
        else:
            actual=dap_decode(out,updated) if typ=='dap' else span(out,updated['offset'],updated['size'])
            need(actual==replacements.get(e['index'],original(e)),'Rebuilt container payload mismatch')
    return bytes(out)


def brlyt_grow(data,changes,preserve_capacity=False):
    meta,rows=brlyt_strings(data);endian,sections=layout_sections(data)
    need(endian=='big','Expected Wii BRLYT')
    need(set(changes)<={r['index'] for r in rows},'Unknown BRLYT string index')
    for text in changes.values():need(isinstance(text,str) and '\0' not in text,'Invalid BRLYT text')
    by_section={r['section_offset']:r for r in rows}
    changed={r['section_offset']:changes[r['index']].encode('utf-16-be')+b'\0\0'
             for r in rows if r['index'] in changes and changes[r['index']]!=r['text']}
    if not changed:return data
    out=bytearray(data[:num(data,12,2,'big')])
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
            put(part,4,len(part),endian='big');put(part,76,capacity,2,'big');put(part,78,len(text),2,'big');put(part,88,at,endian='big')
        out.extend(part)
    put(out,8,len(out),endian='big');verify_text(rows,brlyt_strings(out)[1],changes)
    return bytes(out)


def rev_grow(data,filename,changes):
    from rev_structure import inspect,xor_view
    m=inspect(data,filename,'wii');need(not m['errors'],str(m['errors']))
    need(len(m['captions'])==1,'No Wii caption slot')
    rows=m['captions'][0]['records'];need(set(changes)<=set(range(len(rows))),'Unknown caption row')
    if all(rows[i]['text']==t for i,t in changes.items()):return data
    payload=bytearray()
    for i,row in enumerate(rows):
        text=changes.get(i,row['text']);need(isinstance(text,str) and '\0' not in text,'Invalid caption')
        raw=bytes.fromhex(row['raw_hex']) if text==row['text'] else text.encode('cp932')
        payload.extend(row['time_raw'].to_bytes(2,'big')+raw+b'\0')
    payload.extend(b'\xb8\x18')
    plain=xor_view(data,filename)
    boundary=num(plain,0x10,endian='big')+0x800
    loaded=num(plain,0x18,endian='big');slot=m['captions'][0]
    need(m['metadata_end']==slot['offset'],'Caption is not adjacent to REV metadata; migration needs review')
    stop=slot['offset']+slot['size']
    need(stop<=boundary<=loaded<=len(data),'Unexpected Wii REV read boundary')
    need(not any(data[stop:boundary]) or all(v in (0,0xe3) for v in plain[stop:boundary]),'Unknown data after captions')
    need(num(plain,12,endian='big')==len(data),'Unexpected Wii REV file-size field')
    need(all(r['offset']>=boundary for r in m['red']),'RED overlaps encrypted prefix')
    need(all(c['offset']>=boundary for c in m['cut_resources']),'Cut overlaps encrypted prefix')
    prefix=bytearray(plain[:slot['offset']])+payload
    new_boundary=align(len(prefix),0x800);delta=new_boundary-boundary
    put(prefix,12,len(data)+delta,endian='big')
    put(prefix,0x10,new_boundary-0x800,endian='big')
    put(prefix,0x18,loaded+delta,endian='big')
    put(prefix,0x54,len(payload),endian='big')
    for r in m['red']:put(prefix,r['record_offset']+12,r['offset']+delta,endian='big')
    for c in m['cut_resources']:put(prefix,c['record_offset']+20,c['offset']+delta,endian='big')
    result=xor_view(prefix,filename)+bytes(new_boundary-len(prefix))+data[boundary:]
    after=inspect(result,filename,'wii');need(not after['errors'],str(after['errors']))
    updated=after['captions'][0]['records'];need(len(updated)==len(rows),'Caption count changed')
    for i,(a,b) in enumerate(zip(rows,updated)):
        need(b['text']==changes.get(i,a['text']) and b['time_raw']==a['time_raw'],'Caption verification failed')
    need(result[boundary+delta:]==data[boundary:],'Opaque REV resources changed')
    return result
