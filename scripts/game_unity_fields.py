"""Bounded Unity-v22/Qts field inspection and fixed-width project-only patching.

The parser reports original byte spans; a patch starts with the original bytes
and writes only an explicitly selected scalar. Variable-size edits are rejected.
No installed-file writing, Unity attachment, or resource-ID guessing is provided.
"""
from pathlib import Path
import argparse, collections, hashlib, json, struct

DEFAULT_SCHEMAS = Path(__file__).resolve().parents[1] / 'source/game/descriptors/unity-type-schema-index.json'
PRIMITIVES = {'UInt8':'B','SInt8':'b','char':'B','UInt16':'H','SInt16':'h','short':'h','unsigned short':'H','int':'i','SInt32':'i','UInt32':'I','unsigned int':'I','float':'f','double':'d','SInt64':'q','long long':'q','UInt64':'Q','unsigned long long':'Q','bool':'?'}

def sha256(raw):
    return hashlib.sha256(raw).hexdigest()

def read_index(raw, total_size=None):
    """Inspect a full file or bounded metadata prefix, never executing its data."""
    total = len(raw) if total_size is None else total_size
    if len(raw)<80 or struct.unpack_from('>I',raw,8)[0]!=22:
        raise ValueError('Expected Unity SerializedFile v22')
    if raw[16]!=0:
        raise ValueError('Big-endian object data is unsupported')
    metadata_size, file_size, data_offset = struct.unpack_from('>IQQ',raw,20)
    if file_size!=total:
        raise ValueError('File length differs from v22 header')
    end=raw.find(b'\0',48)
    if end<0 or end+10>len(raw):
        raise ValueError('Metadata prefix is incomplete')
    engine=raw[48:end].decode('ascii'); flag=raw[end+5]
    count=struct.unpack_from('<i',raw,end+6)[0]; pos=end+10
    if not 0<=count<=1000:
        raise ValueError('Type count outside bound')
    types=[]
    for _ in range(count):
        cid,stripped,script_index=struct.unpack_from('<i?h',raw,pos);pos+=7
        script_id=None
        if cid==114:
            script_id=raw[pos:pos+16].hex();pos+=16
        old_hash=raw[pos:pos+16].hex();pos+=16
        trailer=None
        if flag:
            # Actual Qts headers use a four-byte custom type trailer here.
            # This is not the general Unity inline type-tree format.
            trailer=raw[pos:pos+4].hex();pos+=4
        types.append({'class_id':cid,'script_index':script_index,'stripped':stripped,'script_id':script_id,'old_type_hash':old_hash,'custom_type_trailer':trailer})
    count=struct.unpack_from('<i',raw,pos)[0];pos+=4
    if not 0<=count<=100000:
        raise ValueError('Object count outside bound')
    objects=[]
    for _ in range(count):
        pos=(pos+3)&~3; table_offset=pos
        path_id,start,size,type_id=struct.unpack_from('<qqIi',raw,pos);pos+=24
        if not 0<=type_id<len(types) or not 0<=data_offset+start<=total-size:
            raise ValueError('Object index outside file')
        objects.append({'path_id':path_id,'byte_offset':data_offset+start,'byte_size':size,'type_id':type_id,'index_record_offset':table_offset,**types[type_id]})
    return {'engine':engine,'version':22,'file_size':file_size,'metadata_size':metadata_size,'data_offset':data_offset,'custom_QTS_type_trailer':bool(flag),'types':types,'objects':objects,'object_table_end':pos}

def load_schema(schema_id, schema_path=DEFAULT_SCHEMAS):
    raw=Path(schema_path).read_bytes(); catalog=json.loads(raw.decode('utf-8-sig'))
    candidates=[s for s in catalog['schemas'] if s['id_hex']==schema_id]
    if len(candidates)!=1:
        raise ValueError('Schema ID must select exactly one type tree')
    result=dict(candidates[0]);result['catalog_sha256']=sha256(raw)
    return result

def schema_id_from_old_hash(old_type_hash):
    return f"{int.from_bytes(bytes.fromhex(old_type_hash)[:8],'little'):016x}"

def make_tree(schema):
    stack=[];roots=[]
    for original in schema['nodes']:
        node=dict(original);node['children']=[]
        while stack and stack[-1]['level']>=node['level']:
            stack.pop()
        if stack:stack[-1]['children'].append(node)
        else:roots.append(node)
        stack.append(node)
    if len(roots)!=1:
        raise ValueError('Expected one root type-tree node')
    return roots[0]

def read_fields(raw, schema, *, max_array=100000):
    root=make_tree(schema);pos=0;spans=[]
    def take(fmt):
        nonlocal pos
        size=struct.calcsize('<'+fmt)
        if pos+size>len(raw):raise ValueError('Field outside object')
        value=struct.unpack_from('<'+fmt,raw,pos)[0];pos+=size;return value
    def read(node, path='', array_element=False):
        nonlocal pos
        start=pos;typ=node['type'];children=node['children'];align=bool(node['meta_flag']&0x4000)
        fmt=None;value_end=None
        if typ in PRIMITIVES:
            fmt=PRIMITIVES[typ];value=take(fmt);value_end=pos
        elif typ=='string':
            length=take('i')
            if not 0<=length<=len(raw)-pos:raise ValueError('String outside object')
            value=raw[pos:pos+length].decode('utf8');pos+=length;value_end=pos;pos=(pos+3)&~3
        elif children and children[0]['type']=='Array':
            arr=children[0];align=align or bool(arr['meta_flag']&0x4000)
            count=take('i')
            if not 0<=count<=max_array:raise ValueError('Array outside bound')
            if len(arr['children'])!=2:raise ValueError('Unexpected array schema')
            element=arr['children'][1]
            value=[read(element,f'{path}[{i}]',True) for i in range(count)]
            align=align or bool(element['meta_flag']&0x4000)
        elif children:
            value={}
            for child in children:
                name=child['name']
                if name in value:raise ValueError('Duplicate field name requires explicit schema handling')
                child_path=f'{path}.{name}' if path else name
                value[name]=read(child,child_path)
        elif node['byte_size']==0:value={}
        else:raise ValueError(f'Unsupported type {typ}')
        if align and not array_element:pos=(pos+3)&~3
        if pos>len(raw):raise ValueError('Alignment outside object')
        if fmt or typ=='string':
            spans.append({'path':path,'type':typ,'format':fmt,'offset':start,'value_end':value_end,'span_end':pos,'value':value})
        return value
    fields=read(root)
    if pos!=len(raw):raise ValueError(f'Type tree consumed {pos}/{len(raw)} bytes')
    return {'fields':fields,'spans':spans,'consumed':pos,'raw_sha256':sha256(raw),'schema_id':schema['id_hex'],'schema_catalog_sha256':schema.get('catalog_sha256'),'all_bytes_consumed':True}

def patch_fixed_fields(raw, schema, changes, *, expected_sha256):
    """Patch proven scalar byte spans, preserving every other original byte."""
    if sha256(raw)!=expected_sha256:raise ValueError('Origin SHA mismatch')
    parsed=read_fields(raw,schema);by_path={s['path']:s for s in parsed['spans']}
    out=bytearray(raw);evidence=[]
    for path,value in changes.items():
        if path not in by_path:raise ValueError(f'Unknown field {path}')
        span=by_path[path];fmt=span['format']
        if not fmt:raise ValueError('Only fixed-width primitive edits are supported')
        replacement=struct.pack('<'+fmt,value)
        if len(replacement)!=span['value_end']-span['offset']:raise ValueError('Field length mismatch')
        out[span['offset']:span['value_end']]=replacement
        evidence.append({'path':path,'offset':span['offset'],'bytes':len(replacement),'original':span['value'],'replacement':value})
    reparsed=read_fields(bytes(out),schema)
    for path,value in changes.items():
        check=next(s for s in reparsed['spans'] if s['path']==path)
        if check['value']!=struct.unpack('<'+check['format'],struct.pack('<'+check['format'],value))[0]:raise ValueError('Readback mismatch')
    allowed={i for e in evidence for i in range(e['offset'],e['offset']+e['bytes'])}
    if any(a!=b and i not in allowed for i,(a,b) in enumerate(zip(raw,out))):raise ValueError('Unexpected byte change')
    return bytes(out),evidence

def inspect_file(path, schema_path=DEFAULT_SCHEMAS):
    raw=Path(path).read_bytes();idx=read_index(raw);objects=[]
    for obj in idx['objects']:
        sid=schema_id_from_old_hash(obj['old_type_hash'])
        result=dict(obj)
        try:
            schema=load_schema(sid,schema_path)
            result['parsed']=read_fields(raw[obj['byte_offset']:obj['byte_offset']+obj['byte_size']],schema)
        except (ValueError,UnicodeError,struct.error) as error:result['parse_error']=str(error)
        objects.append(result)
    return {'source_path':str(Path(path).resolve()),'source_sha256':sha256(raw),'source_bytes':len(raw),'index':{k:v for k,v in idx.items() if k!='objects'},'objects':objects}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('asset');parser.add_argument('--output');parser.add_argument('--schemas',default=str(DEFAULT_SCHEMAS))
    args=parser.parse_args();result=inspect_file(args.asset,args.schemas)
    encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:Path(args.output).write_text(encoded,encoding='utf8')
    print(json.dumps({'source_sha256':result['source_sha256'],'source_bytes':result['source_bytes'],'objects':len(result['objects']),'parsed':sum('parsed' in o for o in result['objects'])},ensure_ascii=False))
