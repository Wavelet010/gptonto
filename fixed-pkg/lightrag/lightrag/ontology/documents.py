"""Reuse the original parser lifecycle and six chunk strategies without RAG indexes."""
import os
import tempfile
from pathlib import Path
from .heading import heading_chunks


async def parse_with_registry(filename,data,doc_id,directory,engine="auto",parser_params=None):
    from lightrag.parser.base import ParseContext, ResolvedSource
    from lightrag.parser.registry import get_parser, suffix_capabilities, engine_endpoint_configured, engine_endpoint_requirement
    suffix = Path(filename).suffix.lower()
    from lightrag.parser.routing import resolve_parser_directives, encode_parse_engine
    directives = resolve_parser_directives(filename) if engine == "auto" else None
    if directives:
        engine = directives.engine
    params = {**(directives.engine_params if directives else {}),**(parser_params or {})}
    encoded_engine = encode_parse_engine(engine,params)
    if suffix.lstrip(".") not in suffix_capabilities(engine):
        raise ValueError("Unsupported suffix for "+engine)
    if not engine_endpoint_configured(engine):
        raise ValueError("Parser requires "+str(engine_endpoint_requirement(engine)))
    parser = get_parser(engine)
    source = directory / ("document"+suffix)
    source.write_bytes(data)
    os.environ.setdefault("NATIVE_MD_IMAGE_DOWNLOAD_ENABLED","false")

    class ParseSink:
        async def _persist_parsed_full_docs(self, doc_id, record):
            # GPTOnto persists source and chunk results itself; the original
            # parser still executes its normal lifecycle, with no RAG writes.
            return None

    class LocalContext(ParseContext):
        def source_path(self, parser_engine):
            return source
        def resolve(self, parser_engine):
            return ResolvedSource(source,source.name,directory/"parsed")
        async def archive_source(self, source_path):
            # Source bytes are already persisted in the ontology document row.
            return None

    context = LocalContext(ParseSink(),"doc-"+doc_id,source.name,{"parse_engine":encoded_engine})
    result = await parser.parse(context)
    return result.content,result.blocks_path or None,engine


async def parse_external(filename,data,doc_id,directory,engine):
    text,blocks,_ = await parse_with_registry(filename,data,doc_id,directory,engine)
    return text,blocks


async def split_document(filename, data, doc_id, options, *, artifact_sink=None, multimodal=None):
    import asyncio
    from lightrag.utils import TiktokenTokenizer, EmbeddingFunc, enforce_chunk_token_limit_before_embedding
    from lightrag.chunker import (chunking_by_fixed_token, chunking_by_recursive_character,
        chunking_by_paragraph_semantic, chunking_by_structure, chunking_by_semantic_vector)
    strategy,size,overlap = options["strategy"], options["size"], options["overlap"]
    tokenizer = TiktokenTokenizer()
    with tempfile.TemporaryDirectory(prefix="gptonto-") as tmp:
        engine = options.get("parser","auto")
        text,blocks,resolved_engine = await parse_with_registry(filename,data,doc_id,Path(tmp),engine,options.get("parser_params"))
        if not text.strip():
            raise ValueError("No usable text; scanned PDFs require OCR before upload")
        if strategy == "H":
            chunks = heading_chunks(tokenizer,text,size,depth=options["heading_depth"],
                formats=options.get("heading_formats"),number_base=options.get("number_base"))
        elif strategy == "S":
            chunks = chunking_by_structure(tokenizer,text,size)
        elif strategy == "P":
            chunks = chunking_by_paragraph_semantic(tokenizer,text,size,blocks_path=blocks,
                chunk_overlap_token_size=overlap,doc_id=doc_id)
        elif strategy == "R":
            chunks = chunking_by_recursive_character(tokenizer,text,size,chunk_overlap_token_size=overlap)
        elif strategy == "V":
            from lightrag.llm.openai import openai_embed
            from functools import partial
            if not os.getenv("EMBEDDING_MODEL") or not os.getenv("EMBEDDING_DIM"):
                raise ValueError("V requires EMBEDDING_MODEL and EMBEDDING_DIM")
            embed = EmbeddingFunc(embedding_dim=int(os.environ["EMBEDDING_DIM"]),
                func=partial(openai_embed.func,model=os.environ["EMBEDDING_MODEL"],
                    base_url=os.getenv("EMBEDDING_BINDING_HOST"),
                    api_key=os.getenv("EMBEDDING_BINDING_API_KEY") or os.getenv("LLM_BINDING_API_KEY")))
            chunks = await chunking_by_semantic_vector(tokenizer,text,size,embedding_func=embed)
        else:
            chunks = chunking_by_fixed_token(tokenizer,text,size,chunk_overlap_token_size=overlap)
        if strategy != "H":
            chunks = enforce_chunk_token_limit_before_embedding(chunks, tokenizer, size)
        # Keep extracted text and raw parser sidecars together with exact chunk evidence.
        from .evidence import locate_chunks, pack_artifacts
        chunks=locate_chunks(text,chunks)
        if options.get("modalities"):
            if not blocks or multimodal is None:
                raise ValueError("Multimodal analysis requires a sidecar parser and configured model")
            extra=await multimodal(doc_id,filename,text,blocks,options["modalities"],tokenizer,len(chunks))
            chunks.extend({**c,"evidence_kind":"multimodal_analysis"} for c in extra)
        if artifact_sink:
            bundle=await asyncio.to_thread(pack_artifacts,Path(tmp),text)
            artifact_sink(bundle)
        return [{**chunk,"chunk_order_index":i,"chunking_strategy":strategy,"parse_engine":resolved_engine} for i,chunk in enumerate(chunks) if chunk.get("content","").strip()]
