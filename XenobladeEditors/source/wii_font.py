"""Wii RFNA sheet replacement with Huffman-8 encoding and pointer relocation."""
from collections import Counter
from compare_fonts import read_font_data,huffman8
from xeno_formats import num,put,need


def compress_huffman8(raw):
    """Frequency-ordered groups keep every Nintendo tree displacement <= 63.

    This is a valid prefix tree, not an optimal Huffman tree. Each group has
    sixteen balanced leaves, avoiding an overflowing 6-bit tree displacement.
    """
    need(0<len(raw)<1<<24,'Huffman input length outside u24')
    counts=Counter(raw)
    symbols=sorted(range(256),key=lambda s:(-counts[s],s))
    def balanced(values):
        if len(values)==1:return values[0]
        half=len(values)//2
        return balanced(values[:half]),balanced(values[half:])
    def groups(values):
        if len(values)==16:return balanced(values)
        return balanced(values[:16]),groups(values[16:])
    tree=bytearray(2);codes={}
    def emit(node,at,code=0,bits=0):
        if isinstance(node,int):
            tree[at]=node;codes[node]=(code,bits);return
        pair=len(tree);tree.extend(b'\0\0')
        distance=pair//2-at//2-1
        need(0<=distance<=63,'Huffman tree displacement overflow')
        tree[at]=distance|(0x80 if isinstance(node[0],int) else 0)|(0x40 if isinstance(node[1],int) else 0)
        emit(node[0],pair,code<<1,bits+1)
        emit(node[1],pair+1,(code<<1)|1,bits+1)
    emit(groups(symbols),1)
    tree[0]=len(tree)//2-1
    out=bytearray(b'\x28'+len(raw).to_bytes(3,'little')+tree)
    word=0;used=0
    for value in raw:
        code,bits=codes[value]
        for shift in range(bits-1,-1,-1):
            word=(word<<1)|((code>>shift)&1);used+=1
            if used==32:out.extend(word.to_bytes(4,'little'));word=0;used=0
    if used:out.extend((word<<(32-used)).to_bytes(4,'little'))
    need(huffman8(out)==raw,'Huffman verification failed')
    return bytes(out)


def tile_i4(image):
    need(image.mode=='L','Wii font sheet must be grayscale L')
    w,h=image.size;need(w%8==0 and h%8==0,'I4 dimensions must be multiples of eight')
    values=image.tobytes();need(all(v%17==0 for v in values),'I4 requires values 0,17,...,255; enable lossy conversion to quantize')
    out=bytearray(w*h//2)
    for y in range(h):
        for x in range(w):
            at=((y//8)*(w//8)+x//8)*64+(y%8)*8+x%8
            out[at//2]|=(values[y*w+x]//17)<<(4 if at%2==0 else 0)
    return bytes(out)


def replace_sheets(data,edits):
    need(data[:4]==b'RFNA','Expected Wii RFNA')
    logical=num(data,8,endian='big');font=data[:logical]
    need(not any(data[logical:]),'Nonzero data after RFNA logical end')
    meta,mapping,glyphs,widths,sheets=read_font_data(font)
    need(set(edits)<=set(range(len(sheets))),'Unknown font sheet')
    replacements={}
    for index,image in edits.items():
        need(image.size==sheets[index].size,'Retain font sheet dimensions and glyph grid')
        raw=tile_i4(image)
        if image.tobytes()!=sheets[index].tobytes():replacements[index]=compress_huffman8(raw)
    if not replacements:return data
    sections=meta['blocks'];t,tlen=next((p,n) for tag,p,n in sections if tag=='TGLP')
    need(sections[0][0]=='GLGR','Missing GLGR')
    g=sections[0][1];groups=num(font,g+14,2,'big');count=num(font,g+16,2,'big')
    size_at=(41+groups*2)&~3
    need(count==len(sheets) and size_at+count*4<=g+sections[0][2],'Invalid GLGR sizes')
    payload=bytearray(font[t:meta['chunks'][0]['offset']]);sizes=[]
    for i,chunk in enumerate(meta['chunks']):
        old_size=chunk['stored_length'];at=chunk['offset']
        need(num(font,size_at+4*i,endian='big')==old_size,'GLGR allocation mismatch')
        compressed=replacements.get(i,font[at+4:at+4+old_size])
        sizes.append(len(compressed));payload.extend(len(compressed).to_bytes(4,'big'));payload.extend(compressed)
    put(payload,4,len(payload),endian='big')
    out=bytearray(font[:t]+payload+font[t+tlen:]);delta=len(payload)-tlen
    def moved(at):return at+delta if at>=t+tlen else at
    for tag,at,n in sections:
        new=moved(at)
        fields=(16,20,24) if tag=='FINF' else (12,) if tag=='CWDH' else (16,) if tag=='CMAP' else ()
        for offset in fields:
            address=num(font,at+offset,endian='big')
            if address:put(out,new+offset,moved(address),endian='big')
    for i,size in enumerate(sizes):put(out,size_at+i*4,size,endian='big')
    put(out,8,len(out),endian='big')
    checked,new_mapping,_,new_widths,new_sheets=read_font_data(out)
    need(new_mapping==mapping and new_widths==widths,'Font metadata changed')
    for i,sheet in enumerate(new_sheets):
        need(sheet.tobytes()==edits.get(i,sheets[i]).tobytes(),'Font sheet verification failed')
    return bytes(out)


def edit_metadata(data,remap=None,width_edits=None):
    """Rename existing code points and edit signed-left/width/advance records.

    CMAP block membership (and therefore GLGR load groups) is preserved.
    A changed map is rebuilt as a sorted sparse table, with relocated links.
    """
    remap=remap or {};width_edits=width_edits or {}
    length=num(data,8,endian='big');font=data[:length]
    need(data[:4]==b'RFNA' and not any(data[length:]),'Expected Wii RFNA with zero allocation padding')
    meta,mapping,_,widths,_=read_font_data(font)
    need(set(remap)<=set(mapping),'Unknown source code point')
    need(all(type(c) is int and 0<=c<0xffff and not 0xd800<=c<=0xdfff for c in remap.values()),'Invalid BMP code point')
    expected={remap.get(c,c):g for c,g in mapping.items()}
    need(len(expected)==len(mapping),'Code point collision')
    need(set(width_edits)<=set(range(meta['glyph_count'])),'Unknown width glyph')
    for values in width_edits.values():
        need(len(values)==3 and all(type(v) is int for v in values),'Expected left, width, advance integers')
        need(-128<=values[0]<=127 and 0<=values[1]<=255 and 0<=values[2]<=255,'Width record outside byte range')
    replacements={};maps=[]
    for tag,at,size in meta['blocks']:
        if tag=='CWDH':
            block=bytearray(font[at:at+size]);first=num(block,8,2,'big');last=num(block,10,2,'big')
            for glyph,values in width_edits.items():
                if first<=glyph<=last:block[16+3*(glyph-first):19+3*(glyph-first)]=bytes(v&255 for v in values)
            replacements[at]=block
        elif tag=='CMAP':
            lo=num(font,at+8,2,'big');hi=num(font,at+10,2,'big');method=num(font,at+12,2,'big')
            if method==0:pairs=[(c,num(font,at+20,2,'big')+c-lo) for c in range(lo,hi+1)]
            elif method==1:pairs=[(c,num(font,at+20+2*(c-lo),2,'big')) for c in range(lo,hi+1)]
            else:pairs=[(num(font,at+22+4*i,2,'big'),num(font,at+24+4*i,2,'big')) for i in range(num(font,at+20,2,'big'))]
            pairs=[(c,g) for c,g in pairs if g!=65535]
            if not any(c in remap and remap[c]!=c for c,g in pairs):
                maps.append((lo,hi,dict(pairs)));continue
            pairs=sorted((remap.get(c,c),g) for c,g in pairs)
            maps.append((pairs[0][0],pairs[-1][0],dict(pairs)))
            block=bytearray(font[at:at+20]);put(block,8,pairs[0][0],2,'big');put(block,10,pairs[-1][0],2,'big');put(block,12,2,2,'big')
            block.extend(len(pairs).to_bytes(2,'big'))
            for c,g in pairs:block.extend(c.to_bytes(2,'big')+g.to_bytes(2,'big'))
            block.extend(bytes((-len(block))%4));put(block,4,len(block),endian='big');replacements[at]=block
    # NW4R stops at the FIRST matching interval, even if its sparse map misses.
    # A dictionary union alone would hide this runtime lookup regression.
    for code,glyph in expected.items():
        actual=next((entries.get(code) for lo,hi,entries in maps if lo<=code<=hi),None)
        need(actual==glyph,'CMAP range would shadow another character; choose a code within this map or use the final sparse map')
    out=bytearray(font[:16]);locations={}
    for tag,at,size in meta['blocks']:
        locations[at]=len(out);out.extend(replacements.get(at,font[at:at+size]))
    def relocate(address):
        if not address:return 0
        for tag,at,size in meta['blocks']:
            if at<=address<at+size:return locations[at]+address-at
        raise ValueError('Font pointer outside sections')
    for tag,at,size in meta['blocks']:
        fields=(16,20,24) if tag=='FINF' else (28,) if tag=='TGLP' else (12,) if tag=='CWDH' else (16,) if tag=='CMAP' else ()
        for field in fields:put(out,locations[at]+field,relocate(num(font,at+field,endian='big')),endian='big')
    g=meta['blocks'][0][1];need(meta['blocks'][0][0]=='GLGR','Missing GLGR')
    cursor=((41+num(font,g+14,2,'big')*2)&~3)+num(font,g+16,2,'big')*4
    for count_at,tag in ((18,'CWDH'),(20,'CMAP')):
        blocks=[(at,size) for name,at,size in meta['blocks'] if name==tag]
        need(len(blocks)==num(font,g+count_at,2,'big'),'GLGR block count mismatch')
        for at,size in blocks:
            put(out,cursor,len(replacements[at]) if at in replacements else size,endian='big');cursor+=4
    put(out,8,len(out),endian='big')
    _,actual,_,actual_widths,_=read_font_data(out)
    need(actual==expected,'CMAP verification failed')
    expected_widths=bytearray(widths)
    for glyph,values in width_edits.items():expected_widths[glyph*3:glyph*3+3]=bytes(v&255 for v in values)
    need(actual_widths==expected_widths,'CWDH verification failed')
    return data if bytes(out)==font else bytes(out)
