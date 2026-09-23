"""Wii editor backend. Seek PKB entries; preserve all unedited containers."""
from pathlib import Path
import hashlib
import uuid
from xeno_formats import (need, num, cbytes, pkh_entries, u8_entries, bundle_entries,
    arc_entries, dap_entries, dap_decode,
    sb_strings, bdat_strings, brlyt_strings)
from rev_structure import inspect
from wii_repack import container_grow, sb_grow, bdat_grow, brlyt_grow, rev_grow, align
from xeno_formats import put

LIMIT = 64*1024*1024
PARSERS = {'u8':u8_entries,'bundle':bundle_entries,'arc':arc_entries,'dap':dap_entries}


def kind(data, name=''):
    if name.lower().endswith('.rev'): return 'rev'
    magic = data[:4]
    known = {b'SB  ':'sb',b'BDAT':'bdat',b'RLYT':'brlyt',b'RLAN':'brlan',
             b'RFNA':'brfna',
             b'\x55\xaa\x38\x2d':'u8',b'DAP1':'dap',b'marc':'arc',b'bres':'bres',
             b'\x00\x20\xaf\x30':'tpl'}
    if magic in known:return known[magic]
    try:
        bundle_entries(data)
        return 'bundle'
    except ValueError:return 'binary'


def label(data, fallback):
    try:
        if data[:4] == b'BDAT':return cbytes(data,num(data,6,2,'big')).decode('ascii')
        if kind(data)=='bundle':
            entries=bundle_entries(data)
            names=[label(data[e['offset']:e['offset']+e['size']],e['name']) for e in entries[:4]]
            return ', '.join(names)+(' …' if len(entries)>4 else '')
        if kind(data)=='u8':
            entries=u8_entries(data)
            return ', '.join(Path(e['name']).name for e in entries[:3])
    except (ValueError,UnicodeError):pass
    return fallback


def documents(data, name, route=(), depth=0):
    need(depth<12,'Container nesting limit')
    typ=kind(data,name)
    if typ not in PARSERS:return [{'route':route,'name':name,'kind':typ,'bytes':len(data)}]
    result=[]
    for e in PARSERS[typ](data):
        payload=dap_decode(data,e) if typ=='dap' else data[e['offset']:e['offset']+e['size']]
        child=label(payload,e['name']) if typ=='bundle' else e['name']
        result.extend(documents(payload,name+'/'+child,route+((typ,e['index']),),depth+1))
        need(len(result)<=10000,'Too many inner files')
    return result


def resolve(data, route):
    for typ,index in route:
        e=next(e for e in PARSERS[typ](data) if e['index']==index)
        data=dap_decode(data,e) if typ=='dap' else data[e['offset']:e['offset']+e['size']]
    return data


def replace_nested(data, route, edited):
    if not route:return edited
    typ,index=route[0]; entries=PARSERS[typ](data)
    e=next(e for e in entries if e['index']==index)
    old=dap_decode(data,e) if typ=='dap' else data[e['offset']:e['offset']+e['size']]
    child=replace_nested(old,route[1:],edited)
    result=container_grow(data,typ,entries,{index:child})
    need(resolve(result,route)==edited or resolve(result,route).startswith(edited), 'Nested write verification')
    return result


def text_rows(data, typ, filename):
    if typ=='sb':return sb_strings(data,'cp932')[2]
    if typ=='bdat':return bdat_strings(data,'cp932')[1]
    if typ=='brlyt':return brlyt_strings(data)[1]
    if typ=='rev':
        m=inspect(data,filename,'wii');need(not m['errors'],str(m['errors']))
        return [dict(r,index=f"{c['slot']}:{i}",slot=c['slot'],row=i)
                for c in m['captions'] for i,r in enumerate(c['records'])]
    return []


def edit_text(data,typ,filename,changes):
    if typ=='rev':
        edits={}
        for key,value in changes.items():
            slot,row=map(int,str(key).split(':'));edits.setdefault(slot,{})[row]=value
        need(set(edits)<= {0},'Wii supports one caption slot')
        return rev_grow(data,filename,edits.get(0,{}))
    if typ=='brlyt':return brlyt_grow(data,changes,preserve_capacity=True)
    return {'sb':sb_grow,'bdat':bdat_grow}[typ](data,changes)


class Project:
    def __init__(self,root,static=None):
        root=Path(root).resolve()
        for suffix in ('','files','XenoEX/files','Xenoblade Chronicles Root/XenoEX/files'):
            candidate=root/suffix
            if list(candidate.glob('*.pkb')):
                self.root=candidate;break
        else:raise ValueError('PKB 파일이 있는 Wii files 폴더를 선택해 주세요.')
        headers=[];self.static_files={};self.static_archive=None;self.static_entries=[]
        self.static_directory=Path(static).resolve() if static else self.root/'static.arc_OUT'
        for directory in [self.static_directory,self.root/'static',self.root/'static.arc_files',self.root/'static.arc']:
            if directory.is_dir():
                headers.extend(directory.rglob('*.pkh'))
                for path in directory.rglob('*'):
                    if path.is_file() and path.suffix.lower()!='.pkh':self.static_files.setdefault(path.relative_to(directory).as_posix(),path)
        headers.extend(self.root.glob('*.pkh'))
        if (self.root/'static.arc').is_file():
            self.static_archive=(self.root/'static.arc').read_bytes()
            self.static_entries=u8_entries(self.static_archive)
        self.areas={};self.pending={}
        for body in sorted(self.root.glob('*.pkb')):
            matches=[h for h in headers if h.stem.lower()==body.stem.lower()]
            header=matches[0].read_bytes() if matches else None
            if header is None and self.static_archive:
                match=next((e for e in self.static_entries if Path(e['name']).name.lower()==body.stem.lower()+'.pkh'),None)
                if match:header=self.static_archive[match['offset']:match['offset']+match['size']]
            self.areas[body.stem]={'body':body,'header':header,'header_name':body.stem+'.pkh'}
        self.dirty=False
        self.loose_files={}
        static_dirs={self.static_directory,self.root/'static',self.root/'static.arc_files'}
        for path in sorted(self.root.rglob('*')):
            if not path.is_file() or any(path.is_relative_to(p) for p in static_dirs):continue
            if path.is_relative_to(self.root/'ev') or path.suffix.lower() in ('.pkb','.pkh','.sfd'):continue
            if path==self.root/'static.arc':continue
            if path==self.root/'hbm.arc' and (self.root/'hbm.arc_OUT').is_dir():continue
            self.loose_files[path.relative_to(self.root).as_posix()]=path

    def entries(self,area):
        a=self.areas[area];need(a['header'] is not None,'PKH가 없습니다. static.arc를 푼 폴더를 지정해 주세요.')
        if 'entries' not in a:
            header=a['header'];count=num(header,16,endian='big');directory=None
            if num(header,8,endian='big')+count*10==len(header):
                with a['body'].open('rb') as f:directory=f.read(8+count*8)
            a['entries']=pkh_entries(header,range(a['body'].stat().st_size),directory)
        return a['entries']

    def read(self,key):
        if key in self.pending:return self.pending[key]
        area,index=key
        if area=='static':
            if index in self.static_files:return self.static_files[index].read_bytes()
            e=next(e for e in self.static_entries if e['name']==index)
            return self.static_archive[e['offset']:e['offset']+e['size']]
        if area in ('ev','loose'):
            path=(self.root/index).resolve();need(path.is_relative_to(self.root),'Invalid REV path')
            need(path.stat().st_size<=LIMIT,'선택 파일이 64 MiB 제한을 넘습니다.')
            return path.read_bytes()
        e=self.entries(area)[index];need(e['size']<=LIMIT,'선택 항목이 64 MiB 제한을 넘습니다.')
        with self.areas[area]['body'].open('rb') as f:
            f.seek(e['offset']);return f.read(e['size'])

    def stage(self,key,route,edited):
        if key[0] not in ('ev','static','loose'):
            need(self.entries(key[0])[key[1]].get('container')!='afs','AFS 오디오는 현재 목록 조회만 지원합니다.')
        data=self.read(key)
        if not hasattr(self,'original_hashes'):self.original_hashes={}
        if key not in self.pending:self.original_hashes[key]=hashlib.sha256(data).hexdigest()
        replacement=replace_nested(data,route,edited)
        if replacement==data:return
        need(sum(len(v) for k,v in self.pending.items() if k!=key)+len(replacement)<=128*1024*1024,
             '수정 캐시가 128 MiB를 넘습니다. 먼저 저장해 주세요.')
        self.pending[key]=replacement
        self.dirty=True

    def save(self,output):
        output=Path(output).resolve()
        need(not output.exists(),'새 출력 폴더를 지정해 주세요. 기존 폴더는 덮어쓰지 않습니다.')
        need(not output.is_relative_to(self.root),'원본 게임 폴더 밖에 저장해 주세요.')
        staging=output.with_name(output.name+'.incomplete-'+uuid.uuid4().hex)
        try:
            self._save(staging)
            staging.rename(output)
        except Exception as exc:
            raise ValueError(f'저장 실패. 미완성 출력은 사용하지 마세요: {staging}\n{exc}') from exc
        self.dirty=False
        return output

    def _save(self,output):
        output=Path(output).resolve()
        need(not output.exists(),'새 출력 폴더를 지정해 주세요. 기존 폴더는 덮어쓰지 않습니다.')
        need(not output.is_relative_to(self.root),'원본 게임 폴더 밖에 저장해 주세요.')
        # Preflight all selected allocations before creating output.
        for (area,index),data in self.pending.items():
            if area not in ('ev','static','loose'):need(align(len(data),0x800)//0x800<=65535,'PKH sector count exceeds u16')
        output.mkdir(parents=True)
        areas=set()
        for (area,index),data in self.pending.items():
            if area in ('ev','static','loose'):
                target=(output/('static' if area=='static' else '')/index).resolve()
                need(target.is_relative_to(output),'Output path escapes project')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            else:areas.add(area)
        for area in sorted(areas):
            a=self.areas[area];target=output/a['body'].name
            header=bytearray(a['header']);entries=self.entries(area)
            need(not any(e.get('container')=='afs' for e in entries),'AFS editing is not supported')
            count=num(header,16,endian='big');sizes=num(header,8,endian='big')+count*8
            offsets=sizes+count*2
            with a['body'].open('rb') as source,target.open('wb') as dest:
                for e in sorted(entries,key=lambda e:e['offset']):
                    index=e['index'];edited=self.pending.get((area,index));at=dest.tell()
                    size=len(edited) if edited is not None else e['size']
                    allocation=align(size,0x800)
                    put(header,sizes+index*2,allocation//0x800,2,'big')
                    put(header,offsets+index*4,at//0x800,endian='big')
                    if edited is not None:dest.write(edited)
                    else:
                        source.seek(e['offset']);remaining=size
                        while remaining:
                            block=source.read(min(1024*1024,remaining));need(bool(block),'Truncated source PKB')
                            dest.write(block);remaining-=len(block)
                    dest.write(bytes(allocation-size))
            dest=output/'static'/a['header_name'];dest.parent.mkdir(exist_ok=True);dest.write_bytes(header)
            checked=pkh_entries(header,range(target.stat().st_size))
            with target.open('rb') as saved,a['body'].open('rb') as source:
                for e,c in zip(entries,checked):
                    saved.seek(c['offset']);edited=self.pending.get((area,e['index']))
                    if edited is not None:
                        need(saved.read(len(edited))==edited,'Saved PKB mismatch')
                    else:
                        source.seek(e['offset']);remaining=e['size']
                        while remaining:
                            size=min(1024*1024,remaining)
                            need(saved.read(size)==source.read(size),'Unedited PKB entry changed')
                            remaining-=size
        return output
