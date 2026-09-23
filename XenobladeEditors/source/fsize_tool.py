"""Audit/update the observed N3DS fsize.dat file-size index."""
import argparse
import json
from pathlib import Path, PurePosixPath
from xeno_formats import need, num, put, digest
from xeno_tool import destination


def path_hash(path):
    value=0
    for byte in path.encode('ascii'):
        if byte not in (ord('/'),ord('\\')):
            value=(value*31+(byte|0x20))&0xffffffff
    return value


def read_index(data):
    need(data[:4]==b'zisf','Expected N3DS zisf index')
    count=num(data,4)
    need(len(data)==16+count*8,'fsize record count/length mismatch')
    entries={}
    previous=-1
    for i in range(count):
        at=16+i*8
        key,size=num(data,at),num(data,at+4)
        need(key>previous,'fsize keys must be unique and sorted')
        entries[key]={'index':i,'size':size,'size_field':at+4}
        previous=key
    return entries


def audit(root):
    data=(root/'fsize.dat').read_bytes();entries=read_index(data)
    matched=[];unindexed=[];mismatches=[];seen={}
    for p in sorted(root.rglob('*')):
        if not p.is_file():
            continue
        relative=p.relative_to(root).as_posix();key=path_hash(relative)
        need(key not in seen,f'Path hash collision: {relative} and {seen.get(key)}')
        seen[key]=relative
        if key not in entries:
            unindexed.append(relative)
        elif p.stat().st_size!=entries[key]['size']:
            mismatches.append({'path':relative,'stored':entries[key]['size'],'actual':p.stat().st_size})
        else:
            matched.append(relative)
    return {'source_sha256':digest(data),'records':len(entries),'matched':len(matched),
            'unindexed_files':unindexed,'size_mismatches':mismatches,
            'unresolved_keys':[f'{key:08x}' for key in entries if key not in seen]}


def replace_sizes(data, sizes):
    entries=read_index(data);out=bytearray(data);seen=set()
    for path,size in sizes.items():
        key=path_hash(path)
        need(key in entries,f'Path not in original fsize: {path}')
        need(key not in seen,'Duplicate path hash in size changes')
        seen.add(key)
        put(out,entries[key]['size_field'],size)
    after=read_index(out)
    for path,size in sizes.items():
        need(after[path_hash(path)]['size']==size,'fsize write verification failed')
    return bytes(out)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['audit','update'])
    parser.add_argument('romfs',type=Path)
    parser.add_argument('--changes',type=Path,help='JSON list of romfs_path/replacement objects')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.action=='audit':
            result=audit(args.romfs)
            destination(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=True))
            raise SystemExit(bool(result['size_mismatches'] or result['unresolved_keys']))
        need(args.changes is not None,'--changes JSON required')
        changes=json.loads(args.changes.read_text(encoding='utf-8'))
        need(isinstance(changes,list),'Changes must be a JSON array')
        data=(args.romfs/'fsize.dat').read_bytes();entries=read_index(data);sizes={};records=[]
        for change in changes:
            relative=change['romfs_path'].replace('\\','/')
            path=PurePosixPath(relative)
            need(not path.is_absolute() and '..' not in path.parts and ':' not in relative,'Use a relative RomFS path')
            need(relative not in sizes,'Duplicate replacement path')
            original=(args.romfs/relative).resolve()
            need(original.is_relative_to(args.romfs.resolve()),'Original escapes RomFS')
            key=path_hash(relative)
            need(key in entries and original.stat().st_size==entries[key]['size'],'Original file/index size mismatch')
            replacement=Path(change['replacement'])
            if not replacement.is_absolute():
                replacement=args.changes.parent/replacement
            blob=replacement.read_bytes();sizes[relative]=len(blob)
            records.append({'path':relative,'old_size':entries[key]['size'],'new_size':len(blob),'replacement_sha256':digest(blob)})
        output=replace_sizes(data,sizes)
        destination(args.output).write_bytes(output)
        print(json.dumps({'sha256':digest(output),'changes':records},ensure_ascii=True))
    except (ValueError,OSError,KeyError,TypeError) as exc:
        parser.exit(2,f'Error: {exc}\n')
