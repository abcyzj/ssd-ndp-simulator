"""Static IR/2 capture sealing and bounded, disk-backed structural validation.

Business correctness is deliberately outside this module. Extensions register pure
metadata output-size rules; no operation names or business loops are built in.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile

from jsonschema import Draft202012Validator, validators
from referencing import Registry as SchemaRegistry, Resource

U64 = 2**64-1
META_MAX = 16*1024**2
FORMAT = 'ndpsim-static-ir/2'
FILES = {'manifest.json','operations.jsonl','index.jsonl','tail.json'}


class ValidationError(ValueError):
    pass


def require(ok, message):
    if not ok: raise ValidationError(message)


def _unique(pairs):
    result = {}
    for k,v in pairs:
        require(k not in result, 'duplicate JSON key: '+k)
        result[k] = v
    return result


def decode(raw):
    def invalid(value): raise ValidationError('nonfinite JSON number: '+value)
    try:
        def finite(text):
            value=float(text);require(math.isfinite(value),'nonfinite JSON exponent');return value
        value=json.loads(raw, object_pairs_hook=_unique, parse_constant=invalid, parse_float=finite)
        stack=[value]
        while stack:
            part=stack.pop()
            if isinstance(part,str):require(not any(0xd800<=ord(c)<=0xdfff for c in part),'invalid Unicode surrogate')
            elif isinstance(part,dict):stack.extend(part);stack.extend(part.values())
            elif isinstance(part,list):stack.extend(part)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError('invalid JSON: '+str(exc)) from exc


def canonical(value):
    try:
        return (json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False)+'\n').encode('ascii')
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError('invalid metadata: '+str(exc)) from exc


def digest_bytes(raw): return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024**2), b''): h.update(b)
    return h.hexdigest()


def _load(path, limit=META_MAX):
    require(not Path(path).is_symlink(), 'symlink metadata file')
    with Path(path).open('rb') as f:
        data=f.read(limit+1)
    require(len(data)<=limit, 'metadata byte limit exceeded')
    return decode(data)


# Bundled with the installable package. These are byte-for-byte the frozen schemas.
SCHEMA_DIR=Path(__file__).with_name('schemas')
_SCHEMAS=[_load(SCHEMA_DIR/n) for n in ('ndpsim-contract-v2.schema.json','ndpsim-static-ir-v2.schema.json','ndpsim-measurement-v2.schema.json')]
_SCHEMA_REGISTRY=SchemaRegistry().with_resources((s['$id'],Resource.from_contents(s)) for s in _SCHEMAS)
_STRICT=validators.extend(Draft202012Validator,type_checker=Draft202012Validator.TYPE_CHECKER.redefine('integer',lambda checker,v:type(v) is int))
_VALIDATORS={}


def check_schema(value, definition=None):
    key=definition or 'manifest'
    if key not in _VALIDATORS:
        schema=_SCHEMAS[2] if definition=='measurement' else ({'$ref':'urn:ndpsim:contract:2#/$defs/'+definition} if definition else _SCHEMAS[1])
        _VALIDATORS[key]=_STRICT(schema,registry=_SCHEMA_REGISTRY)
    try: _VALIDATORS[key].validate(value)
    except Exception as exc: raise ValidationError('schema '+key+': '+str(exc)) from exc


@dataclass(frozen=True)
class Limits:
    line_bytes: int = META_MAX
    query_index_bytes: int = META_MAX
    active_stages: int = 100000
    total_bytes: int = 2*1024**3
    scratch_bytes: int = 4*1024**3

    def __post_init__(self):
        require(all(type(v) is int and 0<v<=U64 for v in self.__dict__.values()), 'invalid deployment limits')
        require(self.line_bytes<=META_MAX and self.query_index_bytes<=META_MAX, 'metadata limit above supported 16MiB')

    def effective(self, declared):
        require(set(declared)=={'line_bytes','query_index_bytes','active_stages'}, 'invalid package limits')
        require(all(type(v) is int and 0<v<=getattr(self,k) for k,v in declared.items()), 'package exceeds deployment limits')
        return Limits(**declared,total_bytes=self.total_bytes,scratch_bytes=self.scratch_bytes)

    def metadata(self):
        return {k:getattr(self,k) for k in ('line_bytes','query_index_bytes','active_stages')}


class OperatorRegistry:
    """Trusted, externally supplied contracts. Callbacks never come from a package.

    Descriptor and callback identities are bound through rule descriptors. Custom
    adapters must include implementation_sha256 and supply their compiled callback.
    """
    def __init__(self, descriptor, custom_rules=None):
        require(set(descriptor)=={'format','units','operators','rules'}, 'registry fields')
        require(descriptor['format']=='ndpsim-operator-registry/1', 'registry version')
        self._descriptor=decode(canonical(descriptor));descriptor=self._descriptor
        require(len(canonical(self._descriptor))<=META_MAX, 'registry too large')
        require(type(descriptor['units']) is list and descriptor['units'] and all(type(u) is str and u for u in descriptor['units']), 'registry units')
        self.units=set(descriptor['units']);require(len(self.units)==len(descriptor['units']), 'duplicate unit registration')
        self._rules={}; self._specs={};custom_rules=custom_rules or {}
        for rule in descriptor['rules']:
            require(type(rule) is dict and type(rule.get('id')) is str, 'size rule declaration')
            require(rule['id'] not in self._rules,'duplicate size rule')
            if rule.get('kind')=='fixed-outputs/1':
                require(set(rule)=={'id','kind','bytes'} and type(rule['bytes']) is dict, 'fixed size rule fields')
                require(all(type(k) is str and type(v) is int and 0<=v<=U64 for k,v in rule['bytes'].items()), 'fixed size rule sizes')
                values=dict(rule['bytes']);fn=lambda stage,values=values:dict(values)
            else:
                require(set(rule)=={'id','kind','implementation_sha256'} and rule['kind']=='registered-metadata/1','unknown size rule kind')
                require(type(rule['implementation_sha256']) is str and len(rule['implementation_sha256'])==64 and all(c in '0123456789abcdef' for c in rule['implementation_sha256']), 'size rule identity')
                require(rule['id'] in custom_rules, 'unregistered metadata rule')
                fn=custom_rules[rule['id']]
            self._rules[rule['id']]=fn
        for spec in descriptor['operators']:
            check_schema(spec,'operator_spec');key=(spec['id'],spec['version'])
            require(key not in self._specs,'duplicate operator registration')
            require(len(set(spec['locations']))==len(spec['locations']),'duplicate location')
            kinds=set()
            for stage in spec['stages']:
                require(stage['kind'] not in kinds,'duplicate stage kind');kinds.add(stage['kind'])
                for direction in ('inputs','outputs'):
                    ports=stage[direction]
                    require(len({p['name'] for p in ports})==len(ports),'duplicate registered port')
                    require(all(p['unit'] in self.units for p in ports),'unknown registered port unit')
                    if direction=='outputs':require(all(p['cardinality']=='one' for p in ports),'output cardinality')
                require(all(u in self.units for f in ('work_units','feature_units') for u in stage[f].values()), 'unknown registered counter unit')
                require(len(set(stage['contexts']))==len(stage['contexts']) and len(set(stage['decomposition_rules']))==len(stage['decomposition_rules']), 'duplicate stage capability')
                require(stage['output_size_rule'] in self._rules,'unknown output size rule')
            self._specs[key]=spec
        require(self._specs,'empty operator registry')
        self.sha256=digest_bytes(canonical(self._descriptor))

    @property
    def descriptor(self):return decode(canonical(self._descriptor))

    @classmethod
    def load(cls,path): return cls(_load(path))

    def stage_spec(self,op,stage):
        key=(op['operator'],op['operator_version'])
        require(key in self._specs,'unknown operator/version: '+str(key))
        rows=[s for s in self._specs[key]['stages'] if s['kind']==stage['kind']]
        require(len(rows)==1,'unknown stage kind')
        return rows[0]

    def check_operation(self,op,objects,queries):
        check_schema(op,'operation');require(op['query'] in queries,'query outside declared query list')
        spec=self._specs.get((op['operator'],op['operator_version']))
        require(spec is not None,'unknown operator/version')
        semantic_units={}
        for st in spec['stages']:
            for name,unit in st['work_units'].items():
                require(name not in semantic_units or semantic_units[name]==unit,'conflicting Work units')
                semantic_units[name]=unit
        require(all(name in semantic_units and q['unit']==semantic_units[name] for name,q in op['semantic_work'].items()),'semantic Work unit')
        segments=[]
        for st in op['stages']:
            ss=self.stage_spec(op,st)
            require(st['context'] in ss['contexts'] and st['decomposition_rule'] in ss['decomposition_rules'],'unsupported context/decomposition')
            for field,decl in (('work','work_units'),('features','feature_units')):
                require(set(st[field])==set(ss[decl]),field+' coverage')
                require(all(q['unit']==ss[decl][name] for name,q in st[field].items()),field+' unit mismatch')
            outputs={o['port']:o for o in st['outputs']}
            require(len(outputs)==len(st['outputs']),'duplicate output port')
            declared={p['name']:p for p in ss['outputs']}
            require(set(outputs)<=set(declared) and all(not p['required'] or p['name'] in outputs for p in ss['outputs']), 'output port coverage')
            require(all(o['type']==declared[name]['type'] for name,o in outputs.items()), 'output port type')
            sizes=self._rules[ss['output_size_rule']](decode(canonical(st)))
            require(type(sizes) is dict and set(sizes)==set(outputs),'output-size rule port coverage')
            require(all(type(size) is int and 0<=size<=U64 and outputs[name]['logical_bytes']==size for name,size in sizes.items()),'output-size rule mismatch/overflow')
            for access in st['accesses']:
                obj=objects.get((access['object'],access['version']))
                require(obj is not None,'unknown object/version')
                start,length=access['offset_bytes'],access['length_bytes']
                require(start+length<=U64 and start<=obj['size_bytes'] and length<=obj['size_bytes']-start,'access range/overflow')
            segments.append(st['segment'])
        require(sorted(segments)==list(range(len(segments))),'operation segment coverage')


def _key(k): return (str(k['query']),k['op'],k['stage'])


def _json_key(k): return dict(query=int(k[0]),op=k[1],stage=k[2])


def _provenance(provenance,registry,allow_fixture):
    check_schema(provenance,'provenance')
    ids=provenance['query_ids']
    require(len(set(ids))==len(ids),'duplicate query ID')
    require(provenance['extension_sha256']==registry.sha256,'external registry identity mismatch')
    fixture=provenance['kind']=='synthetic-fixture'
    require(not fixture or allow_fixture,'synthetic fixture requires explicit permission')
    require(fixture==(provenance['split']=='fixture'),'fixture split/source mismatch')
    if not fixture:
        hashes=[provenance[k] for k in ('input_content_sha256','semantic_config_sha256','extension_sha256')]
        hashes += [provenance['capture_build'][k] for k in ('source_sha256','binary_sha256')]
        require(all(h!='0'*64 for h in hashes),'placeholder native provenance')
    return fixture


def _objects(rows):
    result={}
    for obj in rows:
        check_schema(obj,'object');key=(obj['id'],obj['version'])
        require(key not in result,'duplicate object/version');result[key]=obj
    return result


class _Scan:
    """Relational state lives on disk, not in a list proportional to total queries."""
    def __init__(self,folder,registry,objects,queries,limits):
        self.registry=registry;self.objects=objects;self.queries=queries;self.limits=limits
        self.db=sqlite3.connect(str(folder/'scan.sqlite'))
        self.db.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        PRAGMA temp_store=FILE; PRAGMA cache_size=-2048;
        CREATE TABLE ops(q TEXT,op TEXT, PRIMARY KEY(q,op));
        CREATE TABLE stages(q TEXT,op TEXT,st TEXT,rank INTEGER,body TEXT,operator TEXT,version TEXT,idx TEXT,
          PRIMARY KEY(q,op,st),UNIQUE(q,rank));
        CREATE TABLE edges(q TEXT,op TEXT,st TEXT,sop TEXT,sst TEXT,port TEXT,input TEXT,kind TEXT,
          UNIQUE(q,op,st,sop,sst,port,input));
        CREATE INDEX incoming ON edges(q,op,st);
        CREATE INDEX outgoing ON edges(q,sop,sst);
        CREATE TABLE versions(q TEXT,name TEXT,version TEXT,op TEXT,st TEXT,
          PRIMARY KEY(q,name,version));
        CREATE TABLE query_size(q TEXT PRIMARY KEY, stages INTEGER,bytes INTEGER);''')
        self.counts=dict(queries=len(queries),operations=0,stages=0,dependencies=0)
        self.peak_line=0;self.peak_query_stages=0;self.peak_query_index_bytes=0

    def close(self):self.db.close()

    def disk_check(self):
        pages=self.db.execute('PRAGMA page_count').fetchone()[0]
        size=self.db.execute('PRAGMA page_size').fetchone()[0]
        require(pages*size<=self.limits.scratch_bytes,'scratch database budget exceeded')

    def operation(self,op,offset,length):
        require(length<=self.limits.line_bytes,'operation line limit')
        self.peak_line=max(self.peak_line,length)
        self.registry.check_operation(op,self.objects,self.queries)
        q=str(op['query'])
        try:
            self.db.execute('INSERT INTO ops VALUES(?,?)',(q,op['id']))
            for st in op['stages']:
                require(st['topological_rank']<self.limits.active_stages,'stage rank exceeds query limit')
                key=dict(query=op['query'],op=op['id'],stage=st['id'])
                idx=dict(key=key,topological_rank=st['topological_rank'],file='operations.jsonl',offset_bytes=offset,length_bytes=length,stage_sha256=digest_bytes(canonical(st)),dependencies=st['dependencies'])
                line=canonical(idx);require(len(line)<=self.limits.line_bytes,'index line limit')
                previous=self.db.execute('SELECT stages,bytes FROM query_size WHERE q=?',(q,)).fetchone() or (0,0)
                n,b=previous[0]+1,previous[1]+len(line)
                require(n<=self.limits.active_stages and b<=self.limits.query_index_bytes,'query stage/index budget exceeded')
                self.peak_query_stages=max(self.peak_query_stages,n);self.peak_query_index_bytes=max(self.peak_query_index_bytes,b)
                self.db.execute('INSERT OR REPLACE INTO query_size VALUES(?,?,?)',(q,n,b))
                self.db.execute('INSERT INTO stages VALUES(?,?,?,?,?,?,?,?)',(q,op['id'],st['id'],st['topological_rank'],canonical(st).decode(),op['operator'],op['operator_version'],line.decode()))
                for dep in st['dependencies']:
                    require(dep['source']['key']['query']==op['query'],'cross-query dependency')
                    src=dep['source']['key']
                    self.db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?,?,?)',(q,op['id'],st['id'],src['op'],src['stage'],dep['source']['port'],dep['input'],dep['kind']))
                self.counts['dependencies']+=len(st['dependencies']);self.counts['stages']+=1
            self.counts['operations']+=1;self.disk_check()
        except sqlite3.IntegrityError as exc:raise ValidationError('duplicate operation/stage/rank/edge') from exc

    def finish(self,terminals):
        keys=[_key(k) for k in terminals]
        require(len(set(keys))==len(keys),'duplicate terminal')
        require({int(k[0]) for k in keys}==set(self.queries),'terminal query coverage')
        for q in self.queries:
            n,low,high=self.db.execute('SELECT count(*),min(rank),max(rank) FROM stages WHERE q=?',(str(q),)).fetchone()
            require(n>0 and low==0 and high==n-1,'query/rank coverage')
        # Each sink must be an explicit terminal; an acyclic graph then guarantees closure.
        sinks={tuple(r) for r in self.db.execute('SELECT q,op,st FROM stages s WHERE NOT EXISTS(SELECT 1 FROM edges e WHERE e.q=s.q AND e.sop=s.op AND e.sst=s.st)')}
        require(sinks==set(keys),'terminal closure/incomplete terminal')
        for q,op,st,rank,body,operator,version in self.db.execute('SELECT q,op,st,rank,body,operator,version FROM stages ORDER BY q,rank'):
            stage=decode(body);ss=self.registry.stage_spec(dict(operator=operator,operator_version=version),stage)
            inputs={p['name']:p for p in ss['inputs']};bound={}
            for dep in stage['dependencies']:
                sk=_key(dep['source']['key']);source=self.db.execute('SELECT rank,body,operator,version FROM stages WHERE q=? AND op=? AND st=?',sk).fetchone()
                require(source is not None,'missing dependency source')
                require(source[0]<rank,'cycle/invalid topological rank')
                src=decode(source[1]);src_spec=self.registry.stage_spec(dict(operator=source[2],operator_version=source[3]),src)
                outputs={o['port']:o for o in src['outputs']};port=dep['source']['port'];dest=dep['input']
                decl={p['name']:p for p in src_spec['outputs']}
                require(port in outputs and dest in inputs,'unknown dependency port')
                require((outputs[port]['type'],decl[port]['unit'])==(inputs[dest]['type'],inputs[dest]['unit']),'dependency type/unit mismatch')
                bound.setdefault(dest,[]).append((sk,port,src))
            require(all((not p['required'] or p['name'] in bound) and (p['cardinality']=='many' or len(bound.get(p['name'],[]))<=1) for p in inputs.values()),'required/cardinality input coverage')
            for v in stage['state_versions']:
                require(v['after']==v['before']+1 and v['after']<=U64,'state version increment')
                outs=[o for o in stage['outputs'] if o['port']==v['output']]
                require(len(outs)==1 and outs[0]['version']==v['after'],'state output version')
                if v['input'] is None:require(v['before']==0,'initial state version')
                else:
                    rows=bound.get(v['input'],[]);require(len(rows)==1,'state input coverage')
                    sk,port,src=rows[0]
                    prior=self.db.execute('SELECT op,st FROM versions WHERE q=? AND name=? AND version=?',(q,v['name'],str(v['before']))).fetchone()
                    require(prior==(sk[1],sk[2]) and any(o['port']==port and o['version']==v['before'] for o in src['outputs']),'state producer/version mismatch')
                try:self.db.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(q,v['name'],str(v['after']),op,st))
                except sqlite3.IntegrityError as exc:raise ValidationError('shared mutable/duplicate state version') from exc
            self.disk_check()

    def index(self):
        for q in self.queries:
            for (line,) in self.db.execute('SELECT idx FROM stages WHERE q=? ORDER BY rank',(str(q),)):
                yield line.encode('ascii')


def _lines(path,limit,total_limit):
    require(not Path(path).is_symlink(),'symlink record file')
    require(Path(path).stat().st_size<=total_limit,'record file budget exceeded')
    start=Path(path).stat();offset=0
    with Path(path).open('rb') as f:
        while True:
            raw=f.readline(limit+1)
            if not raw:break
            require(len(raw)<=limit and raw.endswith(b'\n'),'truncated/oversized JSONL record')
            yield offset,raw,decode(raw)
            offset+=len(raw)
            require(offset<=total_limit,'record byte budget exceeded')
    end=Path(path).stat()
    require((start.st_size,start.st_mtime_ns,start.st_ino)==(end.st_size,end.st_mtime_ns,end.st_ino),'input changed while scanning')


def _package_id(manifest,index,operations):
    return digest_bytes(FORMAT.encode()+b'\0'+bytes.fromhex(manifest)+bytes.fromhex(index)+bytes.fromhex(operations))


def _report(manifest,registry,package_id,scan):
    return dict(validator_source_sha256=file_hash(Path(__file__)),schema_bundle_sha256=digest_bytes(b''.join(canonical(s) for s in _SCHEMAS)),format='ndpsim-package-validation/1',structural_passed=True,semantic_validation='not-performed',independent_semantic_passed=False,package_sha256=package_id,registry_sha256=registry.sha256,source=manifest['provenance']['kind'],counts=scan.counts,
                metrics=dict(max_operation_line_bytes=scan.peak_line,max_query_stages=scan.peak_query_stages,max_query_index_bytes=scan.peak_query_index_bytes),
                limitations=['Metadata integrity/type/dependency validation only; no business payload was read or Work recomputed.','Production semantic certification belongs to the registered workload validator.'])


def validate_package(package,registry,*,allow_fixture=False,limits=Limits(),expected_package_sha256=None,expected_provenance=None):
    package=Path(package)
    require(package.is_dir() and not package.is_symlink(),'invalid package directory')
    require({p.name for p in package.iterdir()}==FILES and all(p.is_file() and not p.is_symlink() for p in package.iterdir()),'package file set/symlink')
    require(sum(p.stat().st_size for p in package.iterdir())<=limits.total_bytes,'package total byte budget')
    manifest=_load(package/'manifest.json');check_schema(manifest)
    effective=limits.effective(manifest['limits']);_provenance(manifest['provenance'],registry,allow_fixture)
    if expected_provenance is not None:require(manifest['provenance']==expected_provenance,'external provenance mismatch')
    objects=_objects(manifest['objects']);rows=manifest['files']
    require(len(rows)==2 and {r['path'] for r in rows}=={'operations.jsonl','index.jsonl'},'package paths/file coverage')
    for row in rows:
        path=package/row['path'];require(path.stat().st_size==row['bytes'] and file_hash(path)==row['sha256'],'file size/hash mismatch')
    manifest_hash=file_hash(package/'manifest.json')
    package_id=_package_id(manifest_hash,file_hash(package/'index.jsonl'),file_hash(package/'operations.jsonl'))
    if expected_package_sha256 is not None:require(package_id==expected_package_sha256,'external package identity mismatch')
    tail=_load(package/'tail.json');check_schema(tail,'tail')
    require(tail['package_sha256']==package_id and tail['counts']==manifest['counts'] and tail['terminals']==manifest['terminals'],'tail binding/count/terminal mismatch')
    with tempfile.TemporaryDirectory(prefix='ndpsim-check-') as temporary:
        scan=_Scan(Path(temporary),registry,objects,manifest['provenance']['query_ids'],effective)
        try:
            for offset,raw,op in _lines(package/'operations.jsonl',effective.line_bytes,effective.total_bytes):scan.operation(op,offset,len(raw))
            scan.finish(manifest['terminals']);require(scan.counts==manifest['counts'],'actual package counts mismatch')
            # Index order is physical storage, not an implicit ORDER dependency.
            scan.db.execute('CREATE TABLE checked_index(q TEXT,op TEXT,st TEXT,PRIMARY KEY(q,op,st))')
            for _,raw,index in _lines(package/'index.jsonl',effective.line_bytes,effective.total_bytes):
                check_schema(index,'index');key=_key(index['key'])
                wanted=scan.db.execute('SELECT idx FROM stages WHERE q=? AND op=? AND st=?',key).fetchone()
                require(wanted is not None and index==decode(wanted[0]),'index location/coverage/hash/dependency mismatch')
                try:scan.db.execute('INSERT INTO checked_index VALUES(?,?,?)',key)
                except sqlite3.IntegrityError as exc:raise ValidationError('duplicate index record') from exc
                scan.disk_check()
            require(scan.db.execute('SELECT count(*) FROM checked_index').fetchone()[0]==scan.counts['stages'],'truncated/missing index record')
            # Bind the bytes actually inspected, including after relational validation.
            require(manifest_hash==file_hash(package/'manifest.json'),'manifest changed')
            for row in rows:require(file_hash(package/row['path'])==row['sha256'],'file changed after validation')
            require(tail==_load(package/'tail.json'),'tail changed')
            report=_report(manifest,registry,package_id,scan)
            report['external_package_identity_bound']=expected_package_sha256 is not None
            report['external_provenance_bound']=expected_provenance is not None
            return report
        finally:scan.close()


class PackageWriter:
    """Incremental Python capture. Failed writers cannot be sealed or reused."""
    def __init__(self,output,provenance,registry,*,allow_fixture=False,limits=Limits()):
        self.output=Path(output).absolute();self.registry=registry;self.provenance=decode(canonical(provenance));self.limits=limits
        _provenance(self.provenance,registry,allow_fixture)
        self.allow_fixture=allow_fixture;self.failed=False;self.closed=False;self.bytes=0;self.objects={};self.recording=False
        self.output.parent.mkdir(parents=True,exist_ok=True)
        self.lock=Path(str(self.output)+'.lock');self.lock.mkdir()
        self.pending=Path(str(self.output)+'.pending')
        try:
            require(not self.output.exists() and not self.output.is_symlink(),'output must not exist')
            self.pending.mkdir();self.stream=(self.pending/'operations.jsonl').open('xb')
        except BaseException:
            self.lock.rmdir();raise

    def _fail(self):
        if self.failed or self.closed:return
        self.failed=True
        if hasattr(self,'stream'):self.stream.close()
        if self.pending.exists():
            failed=Path(str(self.output)+'.failed')
            if not failed.exists() and not failed.is_symlink():self.pending.rename(failed)
        if self.lock.exists():self.lock.rmdir()

    def __enter__(self):return self

    def __exit__(self,*error):
        if not self.closed:self._fail()

    def declare_object(self,obj):
        try:
            require(not self.closed and not self.failed and not self.recording,'object declaration after capture/close')
            check_schema(obj,'object');key=(obj['id'],obj['version'])
            require(key not in self.objects,'duplicate object/version')
            self.objects[key]=decode(canonical(obj))
            require(len(canonical(list(self.objects.values())))<=META_MAX,'object directory too large')
        except BaseException:self._fail();raise

    def record(self,operation):
        try:
            require(not self.closed and not self.failed,'writer failed/closed')
            self.registry.check_operation(operation,self.objects,self.provenance['query_ids'])
            raw=canonical(operation);require(len(raw)<=self.limits.line_bytes,'capture line budget')
            require(self.bytes+len(raw)<=self.limits.total_bytes,'capture output budget')
            self.recording=True;self.stream.write(raw);self.bytes+=len(raw)
        except BaseException:self._fail();raise

    def seal(self,terminals):
        try:
            require(not self.closed and not self.failed,'writer failed/closed');self.stream.close()
            for t in terminals:check_schema(t,'key')
            with tempfile.TemporaryDirectory(prefix='ndpsim-seal-') as temporary:
                scan=_Scan(Path(temporary),self.registry,self.objects,self.provenance['query_ids'],self.limits)
                try:
                    for offset,raw,op in _lines(self.pending/'operations.jsonl',self.limits.line_bytes,self.limits.total_bytes):scan.operation(op,offset,len(raw))
                    scan.finish(terminals)
                    with (self.pending/'index.jsonl').open('xb') as f:
                        index_bytes=0
                        for line in scan.index():
                            index_bytes+=len(line);require(self.bytes+index_bytes<=self.limits.total_bytes,'sealed output budget')
                            f.write(line)
                    manifest=dict(format=FORMAT,provenance=self.provenance,objects=list(self.objects.values()),files=[dict(path=name,bytes=(self.pending/name).stat().st_size,sha256=file_hash(self.pending/name)) for name in ('operations.jsonl','index.jsonl')],counts=scan.counts,terminals=terminals,limits=self.limits.metadata())
                    raw=canonical(manifest);require(len(raw)<=META_MAX,'manifest byte budget');(self.pending/'manifest.json').write_bytes(raw)
                    identity=_package_id(digest_bytes(raw),file_hash(self.pending/'index.jsonl'),file_hash(self.pending/'operations.jsonl'))
                    tail=dict(format='ndpsim-static-ir-tail/2',package_sha256=identity,counts=scan.counts,complete=True,lost_records=0,terminals=terminals)
                    raw=canonical(tail);require(len(raw)<=META_MAX,'tail byte budget');(self.pending/'tail.json').write_bytes(raw)
                finally:scan.close()
            report=validate_package(self.pending,self.registry,allow_fixture=self.allow_fixture,limits=self.limits)
            require(not self.output.exists() and not self.output.is_symlink(),'output appeared during capture')
            self.pending.rename(self.output);self.closed=True;self.lock.rmdir();return report
        except BaseException:self._fail();raise


def seal_capture(source,output,registry,*,allow_fixture=False,limits=Limits()):
    """Seal a completed C++ SDK capture; do not infer any business dependency."""
    source=Path(source)
    require(not Path(output).resolve().is_relative_to(source.resolve()),'output must be outside capture source')
    require(source.is_dir() and not source.is_symlink(),'invalid capture directory')
    require({p.name for p in source.iterdir()}=={'capture.json','operations.jsonl'} and all(p.is_file() and not p.is_symlink() for p in source.iterdir()),'capture file set/symlink')
    meta=_load(source/'capture.json')
    require(set(meta)=={'format','complete','lost_records','provenance','operators','objects','terminals','limits','operations_bytes','operations_sha256'},'capture metadata fields')
    require(meta['format']=='ndpsim-capture/1' and meta['complete'] is True and type(meta['lost_records']) is int and meta['lost_records']==0,'incomplete capture')
    require(meta['operators']==registry.descriptor['operators'],'capture/external operator declarations mismatch')
    limits=limits.effective(meta['limits'])
    require(type(meta['operations_bytes']) is int and 0<=meta['operations_bytes']<=limits.total_bytes,'capture byte budget')
    require((source/'operations.jsonl').stat().st_size==meta['operations_bytes'] and file_hash(source/'operations.jsonl')==meta['operations_sha256'],'capture operation size/hash mismatch')
    meta_hash=file_hash(source/'capture.json')
    with PackageWriter(output,meta['provenance'],registry,allow_fixture=allow_fixture,limits=limits) as writer:
        for obj in meta['objects']:writer.declare_object(obj)
        for _,raw,op in _lines(source/'operations.jsonl',limits.line_bytes,limits.total_bytes):writer.record(op)
        require(meta_hash==file_hash(source/'capture.json') and file_hash(source/'operations.jsonl')==meta['operations_sha256'],'capture changed while sealing')
        return writer.seal(meta['terminals'])
