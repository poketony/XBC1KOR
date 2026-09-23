"""3DS BRLYT txt1 properties; no rendering or guessed BDAT-to-pane mapping.

Field layout cross-checked with doldecomp/ogws nw4r/lyt/lyt_textBox.h and
lyt_pane.h. Game-specific clipping behavior still requires game validation.
"""
import math
import struct
from xeno_formats import brlyt_strings,layout_sections,num,put,span,need
from wii_repack import align

FIELDS={'width':68,'height':72,'font_width':100,'font_height':104,'char_space':108,'line_space':112}
LABELS={'width':'상자 너비','height':'상자 높이','font_width':'글자 너비',
        'font_height':'글자 높이','char_space':'글자 간격','line_space':'줄 간격',
        'capacity_units':'버퍼 용량 (UTF-16 단위, 종료 문자 포함)'}


def properties(data,index):
    meta,rows=brlyt_strings(data);need(meta['endian']=='little','Expected 3DS BRLYT')
    need(0<=index<len(rows),'텍스트 상자를 선택해 주세요.')
    row=rows[index];start=row['section_offset']
    return dict(row,capacity_units=row['capacity_bytes']//2,
                **{key:struct.unpack_from('<f',data,start+offset)[0] for key,offset in FIELDS.items()})


def update(data,index,changes):
    before=properties(data,index);need(set(changes)<=set(LABELS),'Unknown text box property')
    start=before['section_offset'];endian,sections=layout_sections(data)
    values={}
    for key,value in changes.items():
        if key=='capacity_units':
            need(isinstance(value,int) and not isinstance(value,bool),'버퍼 용량은 정수여야 합니다.')
            need(0<=value<=32767 and value*2>=before['used_bytes'],'버퍼 용량이 저장 문자열보다 작거나 필드 범위를 넘습니다.')
        else:
            value=float(value);need(math.isfinite(value),'유한한 숫자를 입력해 주세요.')
            if key in ('width','height','font_width','font_height'):need(value>=0,'크기는 음수일 수 없습니다.')
            value=struct.unpack('<f',struct.pack('<f',value))[0]
        values[key]=value
    if all(before[key]==value for key,value in values.items()):return data
    out=bytearray(data[:num(data,12,2,'little')])
    for section in sections:
        part=bytearray(span(data,section['offset'],section['size']))
        if section['offset']==start:
            for key,offset in FIELDS.items():
                if key in values:struct.pack_into('<f',part,offset,values[key])
            if 'capacity_units' in values and values['capacity_units']!=before['capacity_units']:
                at=before['offset']-start;stop=at+before['capacity_bytes'];capacity=values['capacity_units']*2
                tail=part[stop:]
                if not any(tail):tail=b''
                part=part[:at]+part[at:at+before['used_bytes']]+bytes(capacity-before['used_bytes'])+tail
                part.extend(bytes(align(len(part))-len(part)))
                put(part,76,capacity,2,'little');put(part,4,len(part),endian='little')
        out.extend(part)
    put(out,8,len(out),endian='little')
    after=properties(out,index)
    need(all(after[key]==value for key,value in values.items()),'Text box property verification failed')
    need([r['raw_hex'] for r in brlyt_strings(data)[1]]==[r['raw_hex'] for r in brlyt_strings(out)[1]],'Layout edit changed text')
    return bytes(out)
