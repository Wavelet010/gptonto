"""Bridge the original LightRAG multimodal phase without initializing RAG."""
import os


def create_multimodal_runtime(store,provider=None):
    from .llm_runtime import SQLiteLLMCache,create_model_provider
    owns_provider=provider is None
    provider=provider or create_model_provider()
    from lightrag.pipeline import _PipelineMixin
    timeout=max(1,int(os.getenv("GPTONTO_LLM_TIMEOUT","120")))
    concurrency=max(1,int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
    enabled=os.getenv("GPTONTO_LLM_CACHE","true").lower() in {"1","true","yes"}
    cache=SQLiteLLMCache(store)
    model=os.getenv("VLM_MODEL") or os.getenv("VLM_BINDING_MODEL")
    llm_model=os.getenv("LLM_MODEL")
    vlm_host=os.getenv("VLM_BINDING_HOST") or os.getenv("LLM_BINDING_HOST")

    class Adapter(_PipelineMixin):
        def __init__(self,tokenizer):
            self.tokenizer=tokenizer
            self.role_llm_funcs={"vlm":provider if model else None,"extract":provider}
            self.llm_response_cache=cache if enabled else None
        def _build_global_config(self):
            return {"vlm_process_enable":bool(model),"enable_llm_cache_for_entity_extract":enabled,
                    "llm_model_name":llm_model,"llm_binding":"openai","llm_binding_host":os.getenv("LLM_BINDING_HOST"),
                    "vlm_model":model,"vlm_binding":"openai","vlm_binding_host":vlm_host,
                    "llm_cache_identities":{"vlm":{"role":"vlm","binding":"openai","model":model,"host":vlm_host},
                                            "extract":{"role":"extract","binding":"openai","model":llm_model,"host":os.getenv("LLM_BINDING_HOST")}},
                    "addon_params":{"language":os.getenv("SUMMARY_LANGUAGE","Chinese")}}

    async def analyze(did,filename,text,blocks,modalities,tokenizer,start):
        if "images" in modalities and not model:
            raise ValueError("VLM_MODEL must be configured for image analysis")
        flags="".join({"images":"i","tables":"t","equations":"e"}[m] for m in set(modalities))
        adapter=Adapter(tokenizer)
        result=await adapter.analyze_multimodal("doc-"+did,filename,{"content":text,"blocks_path":blocks},process_options=flags)
        if not result.get("multimodal_processed") and not result.get("analyzing_stage_skipped"):
            raise ValueError("Multimodal analysis did not complete")
        return adapter._build_mm_chunks_from_sidecars("doc-"+did,filename,blocks,start,process_options=flags)

    async def close():
        if owns_provider: await provider.shutdown()
    return analyze,close
