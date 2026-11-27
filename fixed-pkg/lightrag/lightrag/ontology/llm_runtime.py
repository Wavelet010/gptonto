"""Reuse upstream LightRAG's LLM queue and cache key/response handling.

SQLite is only the KV adapter; no graph or retrieval indexes are initialized.
"""
import asyncio
import json
import os
import time


class SQLiteLLMCache:
    def __init__(self, store, bypass_reads=False):
        self.store = store
        self.bypass_reads = bypass_reads
        self.global_config = {"enable_llm_cache_for_entity_extract": True}

    async def get_by_id(self, key):
        if self.bypass_reads:
            return None
        def read():
            with self.store.connect() as db:
                row = db.execute("SELECT data FROM llm_cache WHERE id=?",(key,)).fetchone()
                return json.loads(row[0]) if row else None
        return await asyncio.to_thread(read)

    async def index_done_callback(self):
        # SQLite writes are committed immediately; original parser flush contract.
        return None

    async def upsert(self, data):
        def write():
            with self.store.connect() as db:
                db.executemany("""INSERT INTO llm_cache(id,data) VALUES(?,?)
                    ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=CURRENT_TIMESTAMP""",
                    [(k,json.dumps({**v,"create_time":int(time.time())},ensure_ascii=False)) for k,v in data.items()])
        await asyncio.to_thread(write)


def create_model_provider():
    """One original LightRAG priority queue for extraction, metadata and VLM."""
    from lightrag.utils import priority_limit_async_func_call,remove_think_tags
    from .engine import parse_json
    timeout=max(1,int(os.getenv("GPTONTO_LLM_TIMEOUT","120")))
    concurrency=max(1,int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
    queue_size=max(1,int(os.getenv("GPTONTO_LLM_QUEUE_SIZE","1000")))
    model=os.getenv("LLM_MODEL")
    host=os.getenv("LLM_BINDING_HOST")
    api_key=os.getenv("LLM_BINDING_API_KEY")
    vision_model=os.getenv("VLM_MODEL") or os.getenv("VLM_BINDING_MODEL")

    @priority_limit_async_func_call(concurrency,llm_timeout=timeout,max_queue_size=queue_size,
                                   queue_name="gptonto-model",concurrency_group=None)
    async def provider(prompt,system_prompt=None,require_object=False,**kwargs):
        from lightrag.llm.openai import openai_complete_if_cache
        vision=bool(kwargs.get("image_inputs"))
        selected=vision_model if vision else model
        if not selected: raise ValueError("Required model not configured")
        kwargs.pop("stream",None)
        raw=await openai_complete_if_cache(selected,prompt,system_prompt=system_prompt,
            base_url=(os.getenv("VLM_BINDING_HOST") or host) if vision else host,
            api_key=(os.getenv("VLM_BINDING_API_KEY") or api_key) if vision else api_key,
            stream=False,timeout=timeout,**kwargs)
        raw=remove_think_tags(raw)
        if require_object and not isinstance(parse_json(raw),dict):
            raise ValueError("Expected a JSON object")
        return raw
    return provider


def create_llm_runtime(store,provider=None):
    from lightrag.utils import use_llm_func_with_cache
    provider=provider or create_model_provider()
    cache=SQLiteLLMCache(store)
    fresh_cache=SQLiteLLMCache(store,bypass_reads=True)
    enabled=os.getenv("GPTONTO_LLM_CACHE","true").lower() in {"1","true","yes"}
    model=os.getenv("LLM_MODEL")
    host=os.getenv("LLM_BINDING_HOST")
    async def extract_provider(prompt,system_prompt=None,**kwargs):
        return await provider(prompt,system_prompt=system_prompt,require_object=True,**kwargs)

    async def invoke(prompt,system,*,fresh=False):
        output,_=await use_llm_func_with_cache(prompt,extract_provider,
            llm_response_cache=(fresh_cache if fresh else cache) if enabled else None,
            system_prompt=system,cache_type="gptonto_extract_v1",
            llm_cache_identity={"binding":"openai","model":model,"host":host})
        return output

    async def fresh(prompt,system):
        return await invoke(prompt,system,fresh=True)

    return invoke,fresh,provider.shutdown,provider.get_queue_stats
