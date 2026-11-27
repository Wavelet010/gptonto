"""Bridge the original LightRAG multimodal phase without initializing RAG."""
import os


def create_multimodal_runtime(store):
    from lightrag.utils import priority_limit_async_func_call
    from .llm_runtime import SQLiteLLMCache
    from lightrag.pipeline import _PipelineMixin
    timeout=max(1,int(os.getenv("GPTONTO_LLM_TIMEOUT","120")))
    concurrency=max(1,int(os.getenv("GPTONTO_LLM_CONCURRENCY","4")))
    enabled=os.getenv("GPTONTO_LLM_CACHE","true").lower() in {"1","true","yes"}
    cache=SQLiteLLMCache(store)
    model=os.getenv("VLM_MODEL") or os.getenv("VLM_BINDING_MODEL")
    llm_model=os.getenv("LLM_MODEL")
    vlm_host=os.getenv("VLM_BINDING_HOST") or os.getenv("LLM_BINDING_HOST")

    @priority_limit_async_func_call(concurrency,llm_timeout=timeout,queue_name="gptonto-multimodal")
    async def provider(prompt,**kwargs):
        from lightrag.llm.openai import openai_complete_if_cache
        vision=bool(kwargs.get("image_inputs"))
        selected=model if vision else llm_model
        if not selected: raise ValueError("Configure VLM_MODEL or LLM_MODEL for requested modality")
        kwargs.pop("stream",None)
        return await openai_complete_if_cache(selected,prompt,stream=False,timeout=timeout,
            base_url=vlm_host if vision else os.getenv("LLM_BINDING_HOST"),
            api_key=(os.getenv("VLM_BINDING_API_KEY") or os.getenv("LLM_BINDING_API_KEY")) if vision else os.getenv("LLM_BINDING_API_KEY"),**kwargs)

    class Adapter(_PipelineMixin):
        def __init__(self,tokenizer):
            self.tokenizer=tokenizer
            self.role_llm_funcs={"vlm":provider if model else None,"extract":provider}
            self.llm_response_cache=cache if enabled else None
        def _build_global_config(self):
            return {"vlm_process_enable":bool(model),"enable_llm_cache_for_entity_extract":enabled,
                    "llm_model_name":llm_model,"llm_binding":"openai","llm_binding_host":os.getenv("LLM_BINDING_HOST"),
                    "vlm_model":model,"vlm_binding":"openai","vlm_binding_host":vlm_host,
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

    return analyze,provider.shutdown
