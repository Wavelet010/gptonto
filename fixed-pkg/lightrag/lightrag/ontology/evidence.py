"""Persist original parser outputs and locate evidence without inventing offsets."""
import io
import os
import zipfile
from pathlib import Path


def locate_chunks(text,chunks):
    cursor=0
    for chunk in chunks:
        content=chunk.get("content","")
        span=chunk.get("_source_span")
        if isinstance(span,dict) and isinstance(span.get("start"),int) and isinstance(span.get("end"),int) and 0<=span["start"]<=span["end"]<=len(text) and text[span["start"]:span["end"]]==content:
            cursor=span["start"]
        else:
            # Only claim an absolute position if the content occurs uniquely.
            pos=text.find(content) if content else -1
            if pos>=0 and text.find(content,pos+1)<0:
                chunk["_source_span"]={"start":pos,"end":pos+len(content)}
                cursor=pos
            else:
                chunk.pop("_source_span",None)
        chunk["evidence_kind"]="parsed_text"
    return chunks


def pack_artifacts(directory,text):
    """Use original sidecar files/asset layout, archive only files inside temp root."""
    cap=int(os.getenv("GPTONTO_ARTIFACT_MAX_BYTES",str(128*1024*1024)))
    buf=io.BytesIO()
    total=len(text.encode())
    if total>cap: raise ValueError("Parsed text exceeds artifact limit")
    root=directory.resolve()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("parsed-text.txt",text)
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root): continue
            rel=path.relative_to(directory)
            if len(rel.parts)==1: continue  # original source already lives in documents
            size=path.stat().st_size
            total+=size
            if total>cap: raise ValueError("Parser assets exceed configured limit")
            z.write(path,rel.as_posix())
    return buf.getvalue()


def artifact_manifest(bundle):
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        return [{"path":i.filename,"size":i.file_size} for i in z.infolist() if not i.is_dir()]


def read_artifact(bundle,path):
    if not path or path.startswith("/") or ".." in Path(path).parts or "\\" in path:
        raise KeyError(path)
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        return z.read(path)


def evidence_view(row,quote):
    chunk=row["data"]
    text=chunk["content"]
    span=chunk.get("_source_span")
    offsets=[]
    if quote:
        pos=text.find(quote)
        while pos>=0:
            hit={"chunk_start":pos,"chunk_end":pos+len(quote)}
            if isinstance(span,dict) and span.get("end",0)-span.get("start",0)==len(text) and chunk.get("evidence_kind")!="multimodal_analysis":
                hit.update(parsed_start=span["start"]+pos,parsed_end=span["start"]+pos+len(quote))
            offsets.append(hit)
            pos=text.find(quote,pos+1)
    return {"chunk_id":row["id"],"document_id":row["doc_id"],"filename":row["filename"],"chunk_order_index":row["ord"],
            "content":text,"quote":quote,"matches":offsets,"source_span":span,"heading":chunk.get("heading_path") or chunk.get("heading"),
            "evidence_kind":chunk.get("evidence_kind","parsed_text"),"sidecar":chunk.get("sidecar"),
            "parse_engine":chunk.get("parse_engine")}
