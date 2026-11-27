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
from .assertions import validate_document_assertions

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
    parser_params: dict = Field(default_factory=dict)
    chunk_params: dict = Field(default_factory=dict)
    modalities: list[Literal["images","tables","equations"]] = Field(default_factory=list)
    edp_profile: str | None = None

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
    active = {}
    cancelled = set()
    phases = {}
    history = []
    from datetime import datetime, timezone
    def now(): return datetime.now(timezone.utc).isoformat()
    class TaskCancelled(Exception): pass
    def progress(did,phase,done=0,total=0):
        if did in cancelled: raise TaskCancelled('Cancelled by user')
        phases[did]={'phase':phase,'done':done,'total':total,'updated_at':now()}
        if done==0 or done==total:
            history.append(f'{now()} {did[:8]} {phase} {done}/{total}')
            del history[:-200]
    from .preview import PreviewCache
    previews = PreviewCache()
    from .llm_runtime import create_llm_runtime, create_model_provider
    provider = create_model_provider()
    llm, fresh_llm, close_llm, llm_stats = create_llm_runtime(store,provider)
    from .multimodal import create_multimodal_runtime
    analyze_modalities, close_multimodal = create_multimodal_runtime(store,provider)
    from .config import resolve_options, profile_manager
    async def metadata_llm(prompt,system):
        return await provider(prompt,system_prompt=system)
    document_semaphore = asyncio.Semaphore(max(1,int(os.getenv("GPTONTO_DOCUMENT_CONCURRENCY","2"))))
    key = os.getenv("GPTONTO_API_KEY")
    async def auth(authorization: str | None = Header(None)):
        if key and not hmac.compare_digest(authorization or "", "Bearer "+key):
            raise HTTPException(401,"Invalid API key")

    async def process(did, retry_mode=None):
        async with document_semaphore:
            try:
                doc = store.document(did)
                progress(did,"PARSING")
                store.status(did,"PROCESSING")
                if retry_mode:
                    previous, failed = store.retry_input(did)
                    if previous:
                        indices = list(range(len(previous))) if retry_mode == "all" else failed
                        if not indices:
                            store.status(did,"COMPLETED")
                            return
                        records = await extract_chunks([previous[i]["chunk"] for i in indices],
                            doc["snapshot"],fresh_llm,concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")),
                            progress=lambda done,total:progress(did,"EXTRACTING",done,total))
                        merged, diagnostics = store.prepare_retry(previous,dict(zip(indices,records)))
                        if doc["options"].get("cross_chunk",True):
                            merged = await complete_relationships(did,merged,doc["snapshot"],fresh_llm,
                                concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")),
                                progress=lambda done,total:progress(did,"CROSS_CHUNK",done,total))
                            # Do not replace an existing graph with an incomplete second pass.
                            if any(r.get("cross_errors") for r in merged):
                                diagnostics.update({i:r for i,r in enumerate(merged) if r.get("cross_errors")})
                                for i in indices:
                                    diagnostics.setdefault(i, records[indices.index(i)])
                                merged = previous
                        checked=validate_document_assertions(did,merged,doc["snapshot"])
                        # A failed replacement must not remove facts previously accepted.
                        conflicts={i:r for i,r in enumerate(checked) if r.get("assertion_errors") and r.get("rejected_assertions")!=merged[i].get("rejected_assertions")}
                        if conflicts:
                            diagnostics.update(conflicts)
                            for i in indices: diagnostics.setdefault(i,records[indices.index(i)])
                            checked=previous
                        progress(did,"SAVING")
                        store.commit(did,checked,doc["snapshot"],diagnostics=diagnostics)
                        return
                chunks = store.prepared_chunks(did)
                if chunks is None:
                    chunks = await split_document(doc["filename"],doc["source"],did,doc["options"],
                        artifact_sink=lambda bundle:store.save_artifacts(did,bundle),multimodal=analyze_modalities,
                        stage=lambda name:progress(did,name),metadata_llm=metadata_llm)
                    store.save_prepared_chunks(did,chunks)
                if not chunks: raise ValueError("No usable chunks")
                progress(did,"EXTRACTING",0,len(chunks))
                records = await extract_chunks(chunks,doc["snapshot"],llm,
                    concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")),
                    progress=lambda done,total:progress(did,"EXTRACTING",done,total))
                if doc["options"].get("cross_chunk",True):
                    records = await complete_relationships(did,records,doc["snapshot"],llm,
                        concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")),
                        progress=lambda done,total:progress(did,"CROSS_CHUNK",done,total))
                records=validate_document_assertions(did,records,doc["snapshot"])
                progress(did,"SAVING")
                store.commit(did,records,doc["snapshot"])
            except TaskCancelled:
                store.status(did,"CANCELLED","Cancelled by user; previous valid knowledge retained")
            except asyncio.CancelledError:
                store.status(did,"FAILED","Interrupted; retry document")
                raise
            except Exception as exc:
                store.status(did,"FAILED",type(exc).__name__+": "+str(exc)[:300])

    def enqueue(did, retry_mode=None):
        task = asyncio.create_task(process(did,retry_mode))
        tasks.add(task)
        active[did]=task
        phases[did]={"phase":"QUEUED","done":0,"total":0,"updated_at":now()}
        def finished(t):
            tasks.discard(t)
            if t.cancelled():
                store.status(did,"CANCELLED" if did in cancelled else "FAILED","Cancelled by user" if did in cancelled else "Interrupted; retry document")
            if active.get(did) is not t: return
            active.pop(did,None)
            cancelled.discard(did)
            phases.pop(did,None)
        task.add_done_callback(finished)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield
        running = list(tasks)
        for task in running: task.cancel()
        await asyncio.gather(*running,return_exceptions=True)
        await close_llm()
        await close_multimodal()

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
    async def profiles(enabled_only:bool=False): return store.profiles(enabled_only=enabled_only)

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

    @api.get("/edp_profiles")
    async def edp_profiles(): return profile_manager().list_profiles()

    @api.get("/parsers")
    async def parsers():
        from lightrag.parser.registry import suffix_capabilities, engine_endpoint_configured, engine_endpoint_requirement
        return [{"id":name,"suffixes":sorted(suffix_capabilities(name)),
                 "available":engine_endpoint_configured(name),"requirement":engine_endpoint_requirement(name)}
                for name in ("native","legacy","mineru","docling")]

    @api.post("/documents",status_code=202)
    async def upload_document(file:UploadFile=File(...), options:str=Form("{}"), preview_id:str | None=Form(None)):
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
        filename=Path(file.filename or "document.txt").name
        try: options_dict=resolve_options(filename,opts.model_dump(),opts.model_fields_set)
        except (ValueError,TypeError,KeyError) as exc: raise HTTPException(422,str(exc)) from exc
        prepared=previews.take(preview_id,filename,data,options_dict) if preview_id else None
        did = store.new_document(filename,data,options_dict,profiles,
                                 did=prepared[0] if prepared else None,
                                 prepared=prepared[1] if prepared else None,
                                 artifacts=prepared[2] if prepared and len(prepared)>2 else None)
        enqueue(did)
        return {"id":did,"status":"QUEUED","preview_reused":bool(prepared)}

    @api.post("/documents/preview")
    async def preview(file:UploadFile=File(...), options:str=Form("{}")):
        import uuid
        try:
            opts = Options.model_validate_json(options)
            data = await read(file,32*1024*1024)
            filename=Path(file.filename or "document.txt").name
            did=uuid.uuid4().hex
            artifacts=[]
            options_dict=resolve_options(filename,opts.model_dump(),opts.model_fields_set)
            async with document_semaphore:
                chunks = await split_document(filename,data,did,options_dict,artifact_sink=artifacts.append,multimodal=analyze_modalities,metadata_llm=metadata_llm)
            token=previews.put(did,filename,data,options_dict,chunks,artifacts[0] if artifacts else None)
            return {"count":len(chunks),"chunks":chunks,"preview_id":token}
        except HTTPException: raise
        except Exception as exc: raise HTTPException(422,type(exc).__name__+": "+str(exc)[:300]) from exc

    @api.get("/documents")
    async def documents(): return store.documents()

    @api.post("/documents/paginated")
    async def paginated(body:dict):
        from .document_api import document_page
        try: return await asyncio.to_thread(document_page,store,body,dict(phases))
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc

    @api.get("/documents/status_counts")
    async def status_counts():
        from .document_api import document_page
        return {"status_counts":(await asyncio.to_thread(document_page,store,{},dict(phases)))["status_counts"]}

    @api.get("/documents/pipeline_status")
    async def pipeline_status():
        running=list(phases.values())
        return {"autoscanned":False,"busy":bool(active),"job_name":"GPTOnto",
                "docs":len(active),"batchs":sum(x["total"] for x in running),
                "cur_batch":sum(x["done"] for x in running),
                "request_pending":any(x["phase"]=="QUEUED" for x in running),
                "cancellation_requested":bool(cancelled),"latest_message":history[-1] if history else "",
                "history_messages":history[-100:],"update_status":phases}

    @api.post("/documents/cancel_pipeline")
    async def cancel_pipeline():
        cancelled.update(active)
        # Queued tasks have no parser or model resources to drain.
        for did,task in list(active.items()):
            if phases.get(did,{}).get("phase")=="QUEUED": task.cancel()
        return {"status":"cancellation_requested" if active else "not_busy",
                "message":"Cancellation takes effect at the next safe processing boundary"}

    @api.post("/documents/reprocess_failed")
    async def reprocess_failed():
        for doc in store.documents():
            if doc["status"] in {"FAILED","PARTIAL","CANCELLED"} and doc["id"] not in active:
                store.retry(doc["id"])
                enqueue(doc["id"],"failed")
        return {"status":"reprocessing_started","message":"Retry queued","track_id":""}

    @api.post("/documents/delete_batch")
    async def delete_batch(body:dict):
        ids=body.get("ids")
        if not isinstance(ids,list) or not ids or not all(isinstance(i,str) for i in ids):
            raise HTTPException(422,"Expected document IDs")
        try: n=store.delete_documents(ids)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return {"status":"deletion_started","message":f"Deleted {n} documents","doc_id":""}

    @api.delete("/documents")
    async def clear_documents():
        try: n=store.delete_documents()
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return {"status":"success","message":f"Deleted {n} documents"}

    @api.post("/documents/clear_cache")
    async def clear_cache():
        if active: raise HTTPException(409,"Stop processing before clearing cache")
        store.clear_cache()
        return {"status":"success","message":"Cache cleared"}

    @api.get("/documents/{id}/source")
    async def source_document(id:str):
        from fastapi.responses import Response
        from urllib.parse import quote
        try: doc=store.document(id)
        except KeyError: raise HTTPException(404,"Unknown document")
        return Response(doc["source"],media_type="application/octet-stream",
                        headers={"Content-Disposition":"attachment; filename*=UTF-8''"+quote(doc["filename"],safe=""),
                                 "X-Content-Type-Options":"nosniff"})

    @api.get("/documents/{id}/artifacts")
    async def artifacts(id:str):
        from .evidence import artifact_manifest
        bundle=store.artifacts(id)
        if bundle is None: raise HTTPException(404,"No persisted parse artifacts")
        return artifact_manifest(bundle)

    @api.get("/documents/{id}/artifacts/{path:path}")
    async def artifact(id:str,path:str):
        from .evidence import read_artifact
        from fastapi.responses import Response
        bundle=store.artifacts(id)
        if bundle is None: raise HTTPException(404,"No persisted parse artifacts")
        try: data=read_artifact(bundle,path)
        except KeyError: raise HTTPException(404,"Unknown artifact")
        return Response(data,media_type="application/octet-stream",headers={"Content-Disposition":"attachment","X-Content-Type-Options":"nosniff"})

    @api.get("/evidence/{chunk_id}")
    async def evidence(chunk_id:str,quote:str=Query("",max_length=16000)):
        from .evidence import evidence_view
        try: chunk=store.evidence_chunk(chunk_id)
        except KeyError: raise HTTPException(404,"Unknown evidence chunk")
        return evidence_view(chunk,quote)

    @api.get("/documents/{id}/chunks")
    async def chunks(id:str):
        if not store.document_exists(id): raise HTTPException(404,"Unknown document")
        return await asyncio.to_thread(store.chunks,id)

    @api.get("/documents/{id}/chunks/page")
    async def chunk_page(id:str,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=200)):
        if not store.document_exists(id): raise HTTPException(404,"Unknown document")
        return await asyncio.to_thread(store.chunk_page,id,offset,limit)

    @api.post("/documents/{id}/retry",status_code=202)
    async def retry(id:str, mode:Literal["failed","all"]="failed"):
        if id in active: raise HTTPException(409,"Document still processing")
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
    async def graph_labels(q:str=Query("",max_length=300),limit:int=Query(100,ge=1,le=1000),document_id:str | None=None):
        from .graph_adapter import graph_labels
        return await asyncio.to_thread(graph_labels,store,q,limit,document_id)

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
            if not store.document_exists(document_id): raise HTTPException(404,"Unknown document")
        return await asyncio.to_thread(browse_instances,store,document_id,q,class_uri,offset,limit,focus)

    @api.get("/export")
    async def export(document_id:str | None=None, format:Literal["json","ttl"]="json"):
        if document_id:
            if not store.document_exists(document_id): raise HTTPException(404,"Unknown document")
        data = await asyncio.to_thread(store.export,document_id)
        if format == "ttl":
            from fastapi.responses import Response
            from .exporter import rdf_turtle
            return Response(rdf_turtle(data),media_type="text/turtle",
                            headers={"Content-Disposition":"attachment; filename=gptonto.ttl"})
        return JSONResponse(data,headers={"Content-Disposition":"attachment; filename=gptonto.json"})

    @app.get("/",response_class=HTMLResponse)
    async def index():
        if (Path(__file__).parent/"graph_webui"/"index.html").is_file():
            from fastapi.responses import RedirectResponse
            return RedirectResponse("graph-ui/")
        return (Path(__file__).parent/"web.html").read_text(encoding="utf-8")

    @app.get("/legacy-ui",response_class=HTMLResponse)
    async def legacy_ui():
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
