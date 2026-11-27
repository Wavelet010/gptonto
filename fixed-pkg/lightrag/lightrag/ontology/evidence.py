"""Persist original parser outputs and locate evidence without inventing offsets."""
import io
import os
import zipfile
from pathlib import Path


def locate_chunks(text,chunks):
    for chunk in chunks:
        content=chunk.get("content","")
        span=chunk.get("_source_span")
        if isinstance(span,dict) and isinstance(span.get("start"),int) and isinstance(span.get("end"),int) and 0<=span["start"]<=span["end"]<=len(text) and text[span["start"]:span["end"]]==content:
            pass
        else:
            # Only claim an absolute position if the content occurs uniquely.
            pos=text.find(content) if content else -1
            if pos>=0 and text.find(content,pos+1)<0:
                chunk["_source_span"]={"start":pos,"end":pos+len(content)}
            else:
                chunk.pop("_source_span",None)
        # Do not call normalized/reconstructed content verbatim source text.
        chunk["evidence_kind"]="parsed_text" if content and content in text else "normalized_chunk"
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
            if isinstance(span,dict) and span.get("end",0)-span.get("start",0)==len(text) and chunk.get("evidence_kind","parsed_text")=="parsed_text":
                hit.update(parsed_start=span["start"]+pos,parsed_end=span["start"]+pos+len(quote))
            offsets.append(hit)
            pos=text.find(quote,pos+1)
    return {"chunk_id":row["id"],"document_id":row["doc_id"],"filename":row["filename"],"chunk_order_index":row["ord"],
            "content":text,"quote":quote,"matches":offsets,"source_span":span,"heading":chunk.get("heading_path") or chunk.get("heading"),
            "evidence_kind":chunk.get("evidence_kind","parsed_text"),"sidecar":chunk.get("sidecar"),
            "parse_engine":chunk.get("parse_engine"),"source_blocks":chunk.get("source_blocks",[]),
            "source_assets":chunk.get("source_assets",[]),"provenance_warning":chunk.get("provenance_warning")}



def attach_source_blocks(chunks,blocks_path,root):
    """Resolve the original LightRAG block/modality IDs into parser positions.

    Positions come only from the original Sidecar, never the language model.
    They locate covering blocks, not an exact quote bounding box.
    """
    import json
    path=Path(blocks_path)
    if not path.is_file(): return
    root=root.resolve()
    def relative(p):
        try: return p.resolve().relative_to(root).as_posix()
        except ValueError: return None
    artifact=relative(path)
    if not artifact: return
    blocks={}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row=json.loads(line)
            if row.get("type")=="content" and row.get("blockid"):
                blocks[row["blockid"]]={"block_id":row["blockid"],"artifact":artifact,
                                         "positions":row.get("positions",[]),"heading":row.get("heading")}
    base=str(path).removesuffix(".blocks.jsonl")
    modalities={}
    for kind,plural in (("drawing","drawings"),("table","tables"),("equation","equations")):
        side=Path(base+"."+plural+".json")
        if not side.is_file() or not relative(side): continue
        raw=json.loads(side.read_text(encoding="utf-8"))
        for itemid,item in raw.get(plural,{}).items():
            if not isinstance(item,dict): continue
            info={"kind":kind,"id":itemid,"artifact":relative(side),"block_id":item.get("blockid")}
            for name in ("path","img_path","image_path"):
                if isinstance(item.get(name),str):
                    candidate=path.parent/item[name]
                    rel=relative(candidate)
                    if rel and candidate.is_file(): info["asset"]=rel
                    break
            modalities[kind,itemid]=info
    for chunk in chunks:
        sidecar=chunk.get("sidecar") or {}
        refs=sidecar.get("refs") or ([{"type":sidecar.get("type"),"id":sidecar.get("id")}] if sidecar.get("id") else [])
        ids=set()
        assets=[]
        for ref in refs:
            kind,rid=ref.get("type"),ref.get("id")
            if kind=="block" and rid in blocks: ids.add(rid)
            elif (kind,rid) in modalities:
                info=modalities[kind,rid]
                assets.append(info)
                if info.get("block_id") in blocks: ids.add(info["block_id"])
        if ids: chunk["source_blocks"]=[blocks[i] for i in blocks if i in ids]
        if assets: chunk["source_assets"]=assets
