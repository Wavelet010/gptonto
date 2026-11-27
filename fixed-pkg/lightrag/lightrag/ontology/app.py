"""Standalone GPTOnto extraction service. No RAG, vector DB or reranker initialization."""
import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from .storage import Store
from .schema import compile_ttl
from .documents import split_document
from .engine import extract_chunks

load_dotenv()

class Options(BaseModel):
    strategy: Literal["F","R","P","V","S","H"] = "H"
    size: int = Field(2000, ge=64, le=16000)
    overlap: int = Field(100, ge=0)
    heading_depth: int = Field(1,ge=1,le=6)
    heading_formats: list[Literal["chapter","chinese","bracket_cn","bracket_num","number","markdown"]] | None = None
    number_base: int | None = Field(None,ge=1,le=6)
    profile_ids: list[str] | None = None

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
    semaphore = asyncio.Semaphore(max(1,int(os.getenv("GPTONTO_LLM_CONCURRENCY","4"))))
    document_semaphore = asyncio.Semaphore(max(1,int(os.getenv("GPTONTO_DOCUMENT_CONCURRENCY","2"))))
    key = os.getenv("GPTONTO_API_KEY")
    async def auth(authorization: str | None = Header(None)):
        if key and not hmac.compare_digest(authorization or "", "Bearer "+key):
            raise HTTPException(401,"Invalid API key")

    async def llm(prompt, system):
        from lightrag.llm.openai import openai_complete_if_cache
        model = os.getenv("LLM_MODEL")
        if not model:
            raise ValueError("LLM_MODEL not configured")
        async with semaphore:
            return await openai_complete_if_cache(model,prompt,system_prompt=system,
                base_url=os.getenv("LLM_BINDING_HOST"),api_key=os.getenv("LLM_BINDING_API_KEY"),
                stream=False,timeout=int(os.getenv("GPTONTO_LLM_TIMEOUT","120")))

    async def process(did):
        async with document_semaphore:
            try:
                doc = store.document(did)
                store.status(did,"PROCESSING")
                chunks = await split_document(doc["filename"],doc["source"],did,doc["options"])
                if not chunks: raise ValueError("No usable chunks")
                records = await extract_chunks(chunks,doc["snapshot"],llm,
                    concurrency=int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
                store.commit(did,records,doc["snapshot"])
            except asyncio.CancelledError:
                store.status(did,"FAILED","Interrupted; retry document")
                raise
            except Exception as exc:
                store.status(did,"FAILED",type(exc).__name__+": "+str(exc)[:300])

    def enqueue(did):
        task = asyncio.create_task(process(did))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield
        active = list(tasks)
        for task in active: task.cancel()
        await asyncio.gather(*active,return_exceptions=True)

    app = FastAPI(title="GPTOnto", lifespan=lifespan)
    from fastapi import APIRouter
    api = APIRouter(prefix="/ontology-api",dependencies=[Depends(auth)])

    async def read(file, limit):
        data = await file.read(limit+1)
        if not data or len(data)>limit:
            raise HTTPException(413,"Empty or oversized upload")
        return data

    @api.post("/ontologies")
    async def upload_ontology(file: UploadFile=File(...), name: str=Form(...)):
        try:
            ttl = (await read(file,2*1024*1024)).decode("utf-8-sig")
            schema = await asyncio.to_thread(compile_ttl,ttl)
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
    async def retry(id:str):
        try: store.retry(id)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        enqueue(id)
        return {"id":id,"status":"QUEUED"}

    @api.delete("/documents/{id}")
    async def delete(id:str):
        try: store.delete_document(id)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return {"deleted":id}

    @api.get("/export")
    async def export(document_id:str | None=None):
        if document_id:
            try: store.document(document_id)
            except KeyError: raise HTTPException(404,"Unknown document")
        return JSONResponse(store.export(document_id),headers={"Content-Disposition":"attachment; filename=gptonto.json"})

    @app.get("/",response_class=HTMLResponse)
    async def index():
        return (Path(__file__).parent/"web.html").read_text(encoding="utf-8")

    app.include_router(api)
    return app


def main():
    import uvicorn
    if not os.getenv("GPTONTO_API_KEY") and os.getenv("GPTONTO_HOST","127.0.0.1") not in {"127.0.0.1","localhost","::1"}:
        raise RuntimeError("Set GPTONTO_API_KEY before exposing service")
    uvicorn.run(create_app(),host=os.getenv("GPTONTO_HOST","127.0.0.1"),
                port=int(os.getenv("GPTONTO_PORT","9621")),workers=1)

if __name__ == "__main__":
    main()
