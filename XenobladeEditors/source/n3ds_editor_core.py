"""Read-only RomFS inputs, rebuilt edited files and matching fsize output."""
from pathlib import Path
import uuid
from xeno_formats import need,num,cbytes,arc_entries,bundle_entries,sb_strings,bdat_strings,brlyt_strings
from bdat_scramble import transform_table
from n3ds_repack import container_grow,sb_grow,bdat_grow,brlyt_grow,rev_grow
from fsize_tool import replace_sizes,read_index,path_hash
from rev_structure import inspect

LIMIT=64*1024*1024
PARSERS={'arc':arc_entries,'bundle':bundle_entries}


def kind(data,name=''):
    known={b'SB  ':'sb',b'TADB':'bdat',b'TYLR':'brlyt',b'NALR':'brlan',b'ANFR':'brfna',
           b'cram':'arc',b'mgbr':'mgbr',b'rev\0':'rev',bytes.fromhex('30af2000'):'tpl'}
    if data[:4] in known:return known[data[:4]]
    try:bundle_entries(data);return 'bundle'
    except ValueError:return 'binary'


def documents(data,name,route=(),depth=0):
    need(depth<12,'Container depth limit');typ=kind(data,name)
    if typ not in PARSERS:return [{'route':route,'name':name,'kind':typ,'bytes':len(data)}]
    result=[]
    for entry in PARSERS[typ](data):
        payload=data[entry['offset']:entry['offset']+entry['size']];child=entry['name']
        if typ=='bundle':
            plain=transform_table(payload);child=cbytes(plain,num(plain,6,2)).decode('ascii')
        result.extend(documents(payload,name+'/'+child,route+((typ,entry['index']),),depth+1))
        need(len(result)<=10000,'Too many internal files')
    return result


def resolve(data,route):
    for typ,index in route:
        entry=next(e for e in PARSERS[typ](data) if e['index']==index)
        data=data[entry['offset']:entry['offset']+entry['size']]
    return data


def replace_nested(data,route,edited):
    if not route:return edited
    typ,index=route[0];entries=PARSERS[typ](data);entry=next(e for e in entries if e['index']==index)
    child=replace_nested(data[entry['offset']:entry['offset']+entry['size']],route[1:],edited)
    result=container_grow(data,typ,entries,{index:child})
    actual=resolve(result,route)
    need(actual[:len(edited)]==edited and not any(actual[len(edited):]),'Nested rebuild mismatch')
    return result


def text_rows(data,typ,name):
    if typ=='sb':return sb_strings(data,'utf-8')[2]
    if typ=='bdat':return bdat_strings(transform_table(data),'utf-8')[1]
    if typ=='brlyt':return brlyt_strings(data)[1]
    if typ=='rev':
        meta=inspect(data,Path(name).name,'n3ds');need(not meta['errors'],str(meta['errors']))
        return [dict(row,index=f"{slot['slot']}:{i}",slot=slot['slot'],row=i)
                for slot in meta['captions'] for i,row in enumerate(slot['records'])]
    return []


def edit_text(data,typ,name,changes):
    if typ=='rev':return rev_grow(data,Path(name).name,changes)
    if typ=='brlyt':return brlyt_grow(data,changes,preserve_capacity=True)
    return {'sb':sb_grow,'bdat':bdat_grow}[typ](data,changes)


class Project:
    def __init__(self,root):
        root=Path(root).resolve()
        for suffix in ('','ExtractedRomFS','RomFS','romfs'):
            candidate=root/suffix
            if (candidate/'fsize.dat').is_file() and (candidate/'script').is_dir():self.root=candidate;break
        else:raise ValueError('fsize.dat와 script 폴더가 있는 ExtractedRomFS 루트를 선택해 주세요.')
        self.index=(self.root/'fsize.dat').read_bytes();read_index(self.index)
        self.files={p.relative_to(self.root).as_posix():p for p in sorted(self.root.rglob('*')) if p.is_file() and p.name!='fsize.dat'}
        self.pending={};self.originals={};self.dirty=False

    def read(self,key):
        if key in self.pending:return self.pending[key]
        need(key in self.files,'Unknown RomFS file');path=self.files[key]
        need(path.resolve().is_relative_to(self.root),'Path escapes RomFS')
        need(path.stat().st_size<=LIMIT,'선택 파일이 64 MiB를 넘습니다.')
        return path.read_bytes()

    def stage(self,key,route,edited):
        data=self.read(key);replacement=replace_nested(data,route,edited)
        if replacement==data:return
        need(sum(len(v) for k,v in self.pending.items() if k!=key)+len(replacement)<=128*1024*1024,'수정 캐시 128 MiB 초과. 먼저 저장해 주세요.')
        if key not in self.originals:
            import hashlib
            self.originals[key]=hashlib.sha256(self.files[key].read_bytes()).digest()
        self.pending[key]=replacement;self.dirty=True

    def save(self,output):
        import hashlib
        output=Path(output).resolve()
        need(not output.exists() and not output.is_relative_to(self.root),'원본 밖의 새 출력 폴더를 지정해 주세요.')
        need((self.root/'fsize.dat').read_bytes()==self.index,'원본 fsize.dat가 변경되었습니다. 프로젝트를 다시 여세요.')
        for key in self.pending:
            need(hashlib.sha256(self.files[key].read_bytes()).digest()==self.originals[key],f'원본이 변경되었습니다: {key}')
        index=replace_sizes(self.index,{key:len(data) for key,data in self.pending.items()})
        staging=output.with_name(output.name+'.incomplete-'+uuid.uuid4().hex);staging.mkdir(parents=True)
        try:
            for key,data in self.pending.items():
                target=(staging/key).resolve();need(target.is_relative_to(staging),'Output path escape')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
                need(target.read_bytes()==data,'Output verification failed')
            (staging/'fsize.dat').write_bytes(index);need((staging/'fsize.dat').read_bytes()==index,'fsize output mismatch')
            staging.rename(output)
        except Exception as exc:raise ValueError(f'저장 실패. 사용하지 마세요: {staging}\n{exc}') from exc
        self.dirty=False;return output
