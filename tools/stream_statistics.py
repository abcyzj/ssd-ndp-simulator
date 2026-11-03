"""Bounded JSON resource-array reader for strict offline comparisons.

Uses the standard JSON decoder per value/row, retaining only a chunk and one
row. All fields, resource kinds and row order survive; duplicate keys, malformed
structure and trailing data fail closed. No CSV substitution loses nested data.
"""
import json
from pathlib import Path


class Reader:
    def __init__(self, file):
        self.file=file;self.buffer='';self.offset=0;self.eof=False
        self.decoder=json.JSONDecoder(object_pairs_hook=self.object)
    @staticmethod
    def object(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError('duplicate JSON key: '+key)
            out[key]=value
        return out
    def fill(self):
        self.buffer=self.buffer[self.offset:]+self.file.read(65536);self.offset=0
        if len(self.buffer)==0:self.eof=True
    def peek(self):
        while True:
            while self.offset<len(self.buffer) and self.buffer[self.offset].isspace():self.offset+=1
            if self.offset<len(self.buffer):return self.buffer[self.offset]
            if self.eof:return ''
            self.fill()
    def take(self, char):
        if self.peek()!=char:raise ValueError('expected JSON '+char)
        self.offset+=1
    def value(self):
        self.peek()
        while True:
            try:
                value,end=self.decoder.raw_decode(self.buffer,self.offset)
                # A number may continue across the next chunk.
                if end==len(self.buffer) and not self.eof:
                    old=len(self.buffer)-self.offset;self.fill()
                    if len(self.buffer)==old:self.eof=True
                    continue
                self.offset=end;return value
            except json.JSONDecodeError:
                if self.eof:raise
                old=len(self.buffer)-self.offset;self.fill()
                if len(self.buffer)==old:self.eof=True
    def members(self):
        self.take('{');seen=set()
        if self.peek()=='}':self.offset+=1;return
        while True:
            key=self.value()
            if not isinstance(key,str) or key in seen:raise ValueError('invalid/duplicate object key')
            seen.add(key);self.take(':');yield key
            if self.peek()=='}':self.offset+=1;return
            self.take(',')
    def array(self):
        self.take('[')
        if self.peek()==']':self.offset+=1;return
        while True:
            yield self.value()
            if self.peek()==']':self.offset+=1;return
            self.take(',')


class Rows:
    def __init__(self,db,table,kind):self.db,self.table,self.kind=db,table,kind
    def __iter__(self):
        for (text,) in self.db.execute(f'select row from {self.table} where kind=? order by seq',(self.kind,)):
            yield json.loads(text)


def read_statistics(path,db,table):
    db.execute(f'create table {table}(seq integer primary key,kind text,row text)')
    db.execute(f'create index {table}_kind on {table}(kind,seq)')
    out={}
    with Path(path).open() as f:
        reader=Reader(f)
        for key in reader.members():
            if key!='resources':out[key]=reader.value();continue
            resources={};out[key]=resources
            for kind in reader.members():
                resources[kind]=Rows(db,table,kind)
                for row in reader.array():
                    if not isinstance(row,dict):raise ValueError('resource row must be an object')
                    db.execute(f'insert into {table}(kind,row) values(?,?)',(kind,json.dumps(row)))
            # This one bounded seven-operator summary is used by validation.
            if 'ir_summary' in resources:resources['ir_summary']=list(resources['ir_summary'])
        if reader.peek():raise ValueError('trailing JSON data')
    if 'resources' not in out:raise ValueError('missing statistics resources')
    db.commit();return out
