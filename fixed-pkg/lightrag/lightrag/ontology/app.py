"""Standalone GPTOnto extraction service. No RAG, vector DB or reranker initialization."""
import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from .storage import Store
from .schema import compile_ttl
from .documents import split_document
from .engine import extract_chunks
from .cross_chunk import complete_relationships

load_dotenv()

class Options(BaseModel):
    strategy: Literal["F","R","P","V","S","H"] = "H"
    size: int = Field(2000, ge=64, le=16000)
    overlap: int = Field(100, ge=0)
    heading_depth: int = Field(1,ge=1,le=6)
    heading_formats: list[Literal["chapter","chinese","bracket_cn","bracket_num","number","markdown"]] | None = None
    number_base: int | None = Field(None,ge=1,le=6)
    profile_ids: list[str] | None = None
    cross_chunk: bool = True
    parser: Literal["auto","native","legacy","mineru","docling"] = "auto"

    @model_validator(mode="after")
    def check(self):
        if self.overlap >= self.size or self.heading_formats == []:
            raise ValueError("Invalid overlap or heading formats")
        return self

class ProfileInput(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    ontology_id: str
    prompt_id: str

class Enabled(BaseModel):
    enabled: bool

def create_app():
    store = Store(os.getenv("GPTONTO_DB","data/gptonto.sqlite3"))
    tasks = set()
    from .llm_runtime import create_llm_runtime
    llm, fresh_llm, close_llm, llm_stats = create_llm_runtime(store)
    document_semaphore = asyncio.Semaphore(max(1,int(os.getenv("GPTONTO_DOCUMENT_CONCURRENCY","2"))))
    key = os.getenv("GPTONTO_API_KEY")
    async def auth(authorization: str | None = Header(None)):
        if key and not hmac.compare_digest(authorization or "", "Bearer "+key):
            raise HTTPException(401,"Invalid API key")

    async def process(did, retry_mode=None):
        async with document_semaphore:
            try:
                doc = store.document(did)
                store.status(did,"PROCESSING")
                if retry_mode:
                    previous, failed = store.retry_input(did)
                    if previous:
                        indices = list(range(len(previous))) if retry_mode == "all" else failed
                        if not indices:
                            store.status(did,"COMPLETED")
                            return
                        records = await extract_chunks([previous[i]["chunk"] for i in indices],
                            doc["snapshot"],fresh_llm,concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
                        merged, diagnostics = store.prepare_retry(previous,dict(zip(indices,records)))
                        if doc["options"].get("cross_chunk",True):
                            merged = await complete_relationships(did,merged,doc["snapshot"],fresh_llm,
                                concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
                            # Do not replace an existing graph with an incomplete second pass.
                            if any(r.get("cross_errors") for r in merged):
                                diagnostics.update({i:r for i,r in enumerate(merged) if r.get("cross_errors")})
                                for i in indices:
                                    diagnostics.setdefault(i, records[indices.index(i)])
                                merged = previous
                        store.commit(did,merged,doc["snapshot"],diagnostics=diagnostics)
                        return
                chunks = await split_document(doc["filename"],doc["source"],did,doc["options"])
                if not chunks: raise ValueError("No usable chunks")
                records = await extract_chunks(chunks,doc["snapshot"],llm,
                    concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
                if doc["options"].get("cross_chunk",True):
                    records = await complete_relationships(did,records,doc["snapshot"],llm,
                        concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
                store.commit(did,records,doc["snapshot"])
            except asyncio.CancelledError:
                store.status(did,"FAILED","Interrupted; retry document")
                raise
            except Exception as exc:
                store.status(did,"FAILED",type(exc).__name__+": "+str(exc)[:300])

    def enqueue(did, retry_mode=None):
        task = asyncio.create_task(process(did,retry_mode))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield
        active = list(tasks)
        for task in active: task.cancel()
        await asyncio.gather(*active,return_exceptions=True)
        await close_llm()

    app = FastAPI(title="GPTOnto", lifespan=lifespan)
    from fastapi import APIRouter
    api = APIRouter(prefix="/ontology-api",dependencies=[Depends(auth)])

    async def read(file, limit):
        data = await file.read(limit+1)
        if not data or len(data)>limit:
            raise HTTPException(413,"Empty or oversized upload")
        return data

    @api.post("/ontologies")
    async def upload_ontology(file: UploadFile=File(...), name: str=Form(...),
                              dependency_ids: str=Form("[]")):
        try:
            ttl = (await read(file,2*1024*1024)).decode("utf-8-sig")
            import json
            ids = json.loads(dependency_ids)
            if not isinstance(ids,list) or not all(isinstance(i,str) for i in ids):
                raise ValueError("dependency_ids must be a JSON list of uploaded ontology IDs")
            ids = sorted(set(ids))
            deps = store.ontology_dependencies(ids)
            schema = await asyncio.to_thread(compile_ttl,ttl,deps)
            schema["dependency_ids"] = ids
            return {"id":store.resource("ontology",name,ttl,schema),"schema":schema}
        except HTTPException: raise
        except Exception as exc: raise HTTPException(422,str(exc)[:500]) from exc

    @api.get("/ontologies")
    async def ontologies(): return store.resources("ontology")

    @api.post("/prompts")
    async def upload_prompt(file: UploadFile=File(...), name: str=Form(...)):
        try:
            raw = (await read(file,256*1024)).decode("utf-8-sig")
            p = yaml.safe_load(raw)
            if not isinstance(p,dict) or not isinstance(p.get("description"),str) or not isinstance(p.get("extraction"),dict) or not isinstance(p["extraction"].get("instructions"),str):
                raise ValueError("Expected description and extraction.instructions")
            identities = p.get("identity_properties",{})
            if not isinstance(identities,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in identities.items()):
                raise ValueError("identity_properties must map class URI to property URI")
            # Serialize immediately; reject YAML objects that cannot be JSON encoded.
            data = {"description":p["description"],"extraction":{"instructions":p["extraction"]["instructions"]},
                    "routing":p.get("routing",{}),"identity_properties":identities}
            return {"id":store.resource("prompt",name,raw,data)}
        except HTTPException: raise
        except Exception as exc: raise HTTPException(422,str(exc)[:500]) from exc

    @api.get("/prompts")
    async def prompts(): return store.resources("prompt")

    @api.post("/profiles")
    async def create_profile(p:ProfileInput):
        try:
            return {"id":store.profile(p.name,p.ontology_id,p.prompt_id)}
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc

    @api.get("/profiles")
    async def profiles(): return store.profiles()

    @api.patch("/profiles/{id}")
    async def enable_profile(id:str,body:Enabled):
        try: store.set_enabled("profiles",id,body.enabled)
        except KeyError: raise HTTPException(404,"Unknown profile")
        return {"enabled":body.enabled}

    @api.patch("/resources/{id}")
    async def enable_resource(id:str,body:Enabled):
        try: store.set_enabled("resources",id,body.enabled)
        except KeyError: raise HTTPException(404,"Unknown resource")
        return {"enabled":body.enabled}

    @api.get("/runtime")
    async def runtime():
        return {"llm_queue":await llm_stats(),"active_documents":len(tasks)}

    @api.get("/parsers")
    async def parsers():
        from lightrag.parser.registry import suffix_capabilities, engine_endpoint_configured, engine_endpoint_requirement
        return [{"id":name,"suffixes":sorted(suffix_capabilities(name)),
                 "available":engine_endpoint_configured(name),"requirement":engine_endpoint_requirement(name)}
                for name in ("native","legacy","mineru","docling")]

    @api.post("/documents",status_code=202)
    async def upload_document(file:UploadFile=File(...), options:str=Form("{}")):
        try: opts = Options.model_validate_json(options)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        profiles = store.profiles(enabled_only=True)
        if opts.profile_ids is not None:
            requested = set(opts.profile_ids)
            profiles = [p for p in profiles if p["id"] in requested]
            if requested != {p["id"] for p in profiles}:
                raise HTTPException(422,"Unknown or disabled profile")
        if not profiles: raise HTTPException(422,"No enabled extraction profiles")
        if not os.getenv("LLM_MODEL"): raise HTTPException(503,"LLM_MODEL not configured")
        data = await read(file,32*1024*1024)
        did = store.new_document(Path(file.filename or "document.txt").name,data,opts.model_dump(),profiles)
        enqueue(did)
        return {"id":did,"status":"QUEUED"}

    @api.post("/documents/preview")
    async def preview(file:UploadFile=File(...), options:str=Form("{}")):
        import uuid
        try:
            opts = Options.model_validate_json(options)
            data = await read(file,32*1024*1024)
            chunks = await split_document(Path(file.filename or "document.txt").name,data,uuid.uuid4().hex,opts.model_dump())
            return {"count":len(chunks),"chunks":chunks}
        except HTTPException: raise
        except Exception as exc: raise HTTPException(422,type(exc).__name__+": "+str(exc)[:300]) from exc

    @api.get("/documents")
    async def documents(): return store.documents()

    @api.get("/documents/{id}/chunks")
    async def chunks(id:str):
        try: store.document(id)
        except KeyError: raise HTTPException(404,"Unknown document")
        return store.chunks(id)

    @api.post("/documents/{id}/retry",status_code=202)
    async def retry(id:str, mode:Literal["failed","all"]="failed"):
        try: store.retry(id)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        enqueue(id,mode)
        return {"id":id,"status":"QUEUED"}

    @api.delete("/documents/{id}")
    async def delete(id:str):
        try: store.delete_document(id)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return {"deleted":id}

    @api.get("/graph/labels")
    async def graph_labels(q:str=Query("",max_length=300),limit:int=Query(100,ge=1,le=1000)):
        from .graph_adapter import graph_labels
        return await asyncio.to_thread(graph_labels,store,q,limit)

    @api.get("/graph")
    async def graph(label:str=Query("*",max_length=500),max_depth:int=Query(2,ge=1,le=8),
                    max_nodes:int=Query(500,ge=1,le=1000),document_id:str | None=None):
        from .graph_adapter import graph_data
        return await asyncio.to_thread(graph_data,store,label,max_depth,max_nodes,document_id)

    @api.get("/instances")
    async def instances(document_id:str | None=None,q:str=Query("",max_length=300),
                        class_uri:str | None=None,offset:int=Query(0,ge=0),
                        limit:int=Query(50,ge=1,le=100),focus:str | None=None):
        from .browse import browse_instances
        if document_id:
            try: store.document(document_id)
            except KeyError: raise HTTPException(404,"Unknown document")
        return await asyncio.to_thread(browse_instances,store,document_id,q,class_uri,offset,limit,focus)

    @api.get("/export")
    async def export(document_id:str | None=None, format:Literal["json","ttl"]="json"):
        if document_id:
            try: store.document(document_id)
            except KeyError: raise HTTPException(404,"Unknown document")
        data = store.export(document_id)
        if format == "ttl":
            from fastapi.responses import Response
            from .exporter import rdf_turtle
            return Response(rdf_turtle(data),media_type="text/turtle",
                            headers={"Content-Disposition":"attachment; filename=gptonto.ttl"})
        return JSONResponse(data,headers={"Content-Disposition":"attachment; filename=gptonto.json"})

    @app.get("/",response_class=HTMLResponse)
    async def index():
        return (Path(__file__).parent/"web.html").read_text(encoding="utf-8")

    app.include_router(api)
    graph_dir = Path(__file__).parent/"graph_webui"
    if (graph_dir/"index.html").is_file():
        from fastapi.staticfiles import StaticFiles
        app.mount("/graph-ui",StaticFiles(directory=graph_dir,html=True),name="graph-ui")
    else:
        @app.get("/graph-ui/",response_class=HTMLResponse)
        async def graph_unbuilt():
            return HTMLResponse("<h2>原版图谱前端尚未构建</h2><p>请参考 GPTOnto.md 构建图谱资源后重启。<a href='../'>返回</a></p>",status_code=503)
    return app


def main():
    import uvicorn
    if not os.getenv("GPTONTO_API_KEY") and os.getenv("GPTONTO_HOST","127.0.0.1") not in {"127.0.0.1","localhost","::1"}:
        raise RuntimeError("Set GPTONTO_API_KEY before exposing service")
    uvicorn.run(create_app(),host=os.getenv("GPTONTO_HOST","127.0.0.1"),
                port=int(os.getenv("GPTONTO_PORT","9621")),workers=1)

if __name__ == "__main__":
    main()
