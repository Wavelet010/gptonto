"""Reuse upstream LightRAG's LLM queue and cache key/response handling.

SQLite is only the KV adapter; no graph or retrieval indexes are initialized.
"""
import asyncio
import json
import os
import time


class SQLiteLLMCache:
    def __init__(self, store):
        self.store = store
        self.global_config = {"enable_llm_cache_for_entity_extract": True}

    async def get_by_id(self, key):
        def read():
            with self.store.connect() as db:
                row = db.execute("SELECT data FROM llm_cache WHERE id=?",(key,)).fetchone()
                return json.loads(row[0]) if row else None
        return await asyncio.to_thread(read)

    async def upsert(self, data):
        def write():
            with self.store.connect() as db:
                db.executemany("""INSERT INTO llm_cache(id,data) VALUES(?,?)
                    ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=CURRENT_TIMESTAMP""",
                    [(k,json.dumps({**v,"create_time":int(time.time())},ensure_ascii=False)) for k,v in data.items()])
        await asyncio.to_thread(write)


def create_llm_runtime(store):
    from lightrag.utils import priority_limit_async_func_call, use_llm_func_with_cache, remove_think_tags
    from .engine import parse_json
    timeout = max(1,int(os.getenv("GPTONTO_LLM_TIMEOUT","120")))
    concurrency = max(1,int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
    queue_size = max(1,int(os.getenv("GPTONTO_LLM_QUEUE_SIZE","1000")))
    cache = SQLiteLLMCache(store)
    enabled = os.getenv("GPTONTO_LLM_CACHE","true").lower() in {"1","true","yes"}
    model = os.getenv("LLM_MODEL")
    host = os.getenv("LLM_BINDING_HOST")
    api_key = os.getenv("LLM_BINDING_API_KEY")

    @priority_limit_async_func_call(concurrency,llm_timeout=timeout,max_queue_size=queue_size,
                                   queue_name="gptonto-extract",concurrency_group=None)
    async def provider(prompt,system_prompt=None,**kwargs):
        from lightrag.llm.openai import openai_complete_if_cache
        if not model:
            raise ValueError("LLM_MODEL not configured")
        raw = await openai_complete_if_cache(model,prompt,system_prompt=system_prompt,
                base_url=host,api_key=api_key,stream=False,timeout=timeout,**kwargs)
        raw = remove_think_tags(raw)
        # Never cache malformed JSON. Semantic validation is still done by the
        # extraction stage and explicit retries always bypass the cache.
        parsed = parse_json(raw)
        if not isinstance(parsed,dict):
            raise ValueError("Expected a JSON object")
        return raw

    async def invoke(prompt,system,*,fresh=False):
        output,_ = await use_llm_func_with_cache(prompt,provider,
            llm_response_cache=cache if enabled and not fresh else None,
            system_prompt=system,cache_type="gptonto_extract_v1",
            llm_cache_identity={"binding":"openai","model":model,"host":host})
        return output

    async def fresh(prompt,system):
        return await invoke(prompt,system,fresh=True)

    return invoke,fresh,provider.shutdown,provider.get_queue_stats
