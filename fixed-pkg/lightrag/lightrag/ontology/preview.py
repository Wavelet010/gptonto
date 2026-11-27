"""Bounded, one-use preview cache; source bytes and exact parser options must match."""
import hashlib
import json
import time
import uuid
from collections import OrderedDict


class PreviewCache:
    def __init__(self,max_entries=8,max_bytes=64*1024*1024,ttl=900):
        self.max_entries,self.max_bytes,self.ttl=max_entries,max_bytes,ttl
        self.entries=OrderedDict()
        self.bytes=0

    @staticmethod
    def fingerprint(filename,source,options):
        # Profile and second-pass selection do not affect the parsed chunks.
        relevant={k:v for k,v in options.items() if k not in {"profile_ids","cross_chunk"}}
        h=hashlib.sha256(source)
        h.update(json.dumps([filename,relevant],sort_keys=True,ensure_ascii=False).encode())
        return h.hexdigest()

    def _drop(self,key):
        row=self.entries.pop(key,None)
        if row: self.bytes-=row[3]
        return row

    def _expire(self):
        now=time.monotonic()
        for key,row in list(self.entries.items()):
            if row[0]<=now: self._drop(key)

    def put(self,doc_id,filename,source,options,chunks,artifacts=None):
        self._expire()
        # Store serialized bytes to keep the memory bound meaningful.
        data=json.dumps(chunks,ensure_ascii=False,allow_nan=False).encode()
        size=len(data)+len(artifacts or b"")
        if size>self.max_bytes: return None
        while self.entries and (len(self.entries)>=self.max_entries or self.bytes+size>self.max_bytes):
            self._drop(next(iter(self.entries)))
        key=uuid.uuid4().hex
        self.entries[key]=(time.monotonic()+self.ttl,self.fingerprint(filename,source,options),doc_id,size,data,artifacts)
        self.bytes+=size
        return key

    def take(self,key,filename,source,options):
        self._expire()
        row=self.entries.get(key)
        if not row or row[1]!=self.fingerprint(filename,source,options):
            return None
        self._drop(key)
        return (row[2],json.loads(row[4]),row[5]) if row[5] is not None else (row[2],json.loads(row[4]))
