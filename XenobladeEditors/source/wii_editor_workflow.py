"""Font-table display and complete, backed-up Wii editor saves."""
from pathlib import Path
import hashlib
import json
import shutil
import uuid
from datetime import datetime
from xeno_formats import need, u8_entries
from wii_repack import container_grow

def font_mapping(path):
    path=Path(path)
    if path.suffix.lower()=='.json':
        mapping=json.loads(path.read_text('utf-8'))
        need(isinstance(mapping,dict) and all(isinstance(k,str) and len(k)==1 and isinstance(v,str) for k,v in mapping.items()),'문자 매핑 JSON 형식 오류')
        return mapping
    data=path.read_bytes();text=data.decode('utf-16' if data[:2] in (b'\xff\xfe',b'\xfe\xff') else 'utf-8-sig')
    mapping={}
    for line in text.splitlines():
        if '=' not in line:continue
        key,value=line.split('=',1);raw=bytes.fromhex(key)
        if raw==b'\0' or len(value)!=1:continue
        try:game=raw.decode('cp932')
        except UnicodeError:continue
        if len(game)==1 and game.encode('cp932')==raw:mapping.setdefault(value,game)
    need(bool(mapping),'사용할 수 있는 문자 매핑이 없습니다.')
    return mapping

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def save_complete(project, output):
    """Export changed PKBs plus the rebuilt static.arc; keep loose static output."""
    output=project.save(output)
    try:
        need(project.static_archive is not None or not (output/'static').exists(),'static.arc 원본이 필요합니다. static 폴더만 있는 프로젝트는 기존 내보내기를 사용하세요.')
        if project.static_archive:
            changes={}
            for e in project.static_entries:
                name=e['name'];exported=output/'static'/name
                if not exported.exists() and name.lower().endswith('.pkh'):exported=output/'static'/Path(name).name
                if exported.is_file():payload=exported.read_bytes()
                elif name in project.static_files:payload=project.static_files[name].read_bytes()
                elif name.lower().endswith('.pkh') and Path(name).stem in project.areas:payload=project.areas[Path(name).stem]['header']
                else:continue
                if payload!=project.static_archive[e['offset']:e['offset']+e['size']]:changes[e['index']]=payload
            if changes:
                arc=container_grow(project.static_archive,'u8',project.static_entries,changes)
                for a,b in zip(project.static_entries,u8_entries(arc)):
                    need(a['name']==b['name'],'static 파일 순서 변경')
                    expected=changes.get(a['index'],project.static_archive[a['offset']:a['offset']+a['size']])
                    need(arc[b['offset']:b['offset']+b['size']]==expected,'static 재포장 검증 실패')
                (output/'static.arc').write_bytes(arc)
        return output
    except Exception:
        project.dirty=True
        raise

def apply_to_game(project):
    """Back up every destination before replacing it; roll back on failure."""
    from wii_editor_core import Project
    need(all(p.is_relative_to(project.root/'static.arc_OUT') for p in project.static_files.values()),'직접 반영은 기본 static.arc_OUT 폴더에서 지원합니다. 다른 static 폴더를 사용 중이면 기본 폴더로 옮긴 뒤 다시 여세요.')
    fresh=Project(project.root,project.static_directory if project.static_directory.is_dir() else None)
    need(fresh.static_archive==project.static_archive,'static.arc가 외부에서 변경되었습니다. 루트를 다시 여세요.')
    for key,h in getattr(project,'original_hashes',{}).items():
        need(hashlib.sha256(fresh.read(key)).hexdigest()==h,'편집 중 원본 파일이 변경되었습니다. 루트를 다시 여세요.')
    backup=project.root.parent/'editor_backups'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    output=save_complete(project,backup/'output')
    project.dirty=True
    tasks=[]
    for src in output.rglob('*'):
        if not src.is_file() or src.is_relative_to(output/'static'):continue
        tasks.append((src,project.root/src.relative_to(output)))
    if (output/'static.arc').exists() and (project.root/'static.arc_OUT').is_dir():
        arc=(output/'static.arc').read_bytes()
        for e in u8_entries(arc):
            dest=(project.root/'static.arc_OUT'/e['name']).resolve()
            need(dest.is_relative_to((project.root/'static.arc_OUT').resolve()),'잘못된 static 내부 경로')
            payload=arc[e['offset']:e['offset']+e['size']]
            if dest.exists() and dest.read_bytes()==payload:continue
            src=backup/'mirror'/e['name'];src.parent.mkdir(parents=True,exist_ok=True);src.write_bytes(payload);tasks.append((src,dest))
    need(bool(tasks),'반영할 파일이 없습니다.')
    records=[]
    for src,dest in tasks:
        need(dest.resolve().is_relative_to(project.root),'잘못된 저장 경로')
        need(dest.exists(),'기존 파일이 없는 경로는 자동 반영하지 않습니다: '+str(dest))
        rel=dest.relative_to(project.root);old=backup/'original'/rel;old.parent.mkdir(parents=True,exist_ok=True)
        before=digest(dest);shutil.copy2(dest,old);need(digest(old)==before,'백업 검증 실패')
        records.append(dict(path=rel.as_posix(),before=before,after=digest(src)))
    (backup/'manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    written=[]
    try:
        for (src,dest),r in zip(tasks,records):
            need(digest(dest)==r['before'],'백업 후 파일이 외부에서 변경되었습니다.')
            temp=dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.tmp');shutil.copyfile(src,temp)
            need(digest(temp)==r['after'],'저장 검증 실패');temp.replace(dest);written.append(dest)
        for (_,dest),r in zip(tasks,records):need(digest(dest)==r['after'],'반영 결과 검증 실패')
    except Exception:
        for dest in written:shutil.copy2(backup/'original'/dest.relative_to(project.root),dest)
        project.dirty=True
        raise
    project.pending.clear();project.dirty=False
    return backup
