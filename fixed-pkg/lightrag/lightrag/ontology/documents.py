"""Reuse LightRAG native document parsing and all five original chunk algorithms."""
import os
import tempfile
from pathlib import Path
from .heading import heading_chunks


def parse_document(filename, data, doc_id, directory, engine="auto"):
    suffix = Path(filename).suffix.lower()
    os.environ.setdefault("NATIVE_MD_IMAGE_DOWNLOAD_ENABLED", "false")
    if engine == "native" or (engine == "auto" and suffix in {".docx", ".md"}):
        if suffix not in {".docx", ".md"}:
            raise ValueError("Native parser supports DOCX and Markdown")
        from lightrag.parser.docx.parser import NativeDocxParser
        from lightrag.parser.markdown.parser import NativeMarkdownParser
        from lightrag.sidecar import write_sidecar
        parser = NativeDocxParser() if suffix == ".docx" else NativeMarkdownParser()
        source = directory / ("document"+suffix)
        source.write_bytes(data)
        parsed_dir = directory / "parsed"
        asset_dir = parsed_dir / "document.blocks.assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        blocks, warnings, metadata = parser.extract(source, parsed_dir=parsed_dir, asset_dir=asset_dir, base_name="document")
        if not blocks:
            raise ValueError("No document blocks")
        ir = parser.build_ir(blocks, document_name=source.name, asset_dir_name=asset_dir.name, metadata=metadata)
        parsed = write_sidecar(ir, parsed_dir=parsed_dir, doc_id="doc-"+doc_id,
                              engine=parser.engine_name, clean_parsed_dir=False,
                              block_drawing_path_style=parser.sidecar_path_style)
        return parsed["content"], parsed["blocks_path"]
    from lightrag.parser.registry import suffix_capabilities
    if suffix.lstrip(".") not in suffix_capabilities("legacy"):
        raise ValueError("Unsupported document type")
    from lightrag.parser.legacy.extractors import extract_text
    return extract_text(data,suffix,pdf_password=os.getenv("PDF_DECRYPT_PASSWORD")), None


async def parse_external(filename,data,doc_id,directory,engine):
    """Use upstream download/IR/sidecar hooks, without the RAG persistence layer."""
    import asyncio
    from lightrag.parser.registry import get_parser, suffix_capabilities, engine_endpoint_configured, engine_endpoint_requirement
    from lightrag.sidecar import write_sidecar
    suffix = Path(filename).suffix.lower()
    if suffix.lstrip(".") not in suffix_capabilities(engine):
        raise ValueError("Unsupported suffix for "+engine)
    if not engine_endpoint_configured(engine):
        raise ValueError("Parser requires "+str(engine_endpoint_requirement(engine)))
    parser = get_parser(engine)
    source = directory / ("document"+suffix)
    source.write_bytes(data)
    raw = directory / "raw"
    raw.mkdir()
    await parser.download_into(raw,source,upload_name=source.name)
    def normalize():
        ir = parser.build_ir(raw,source.name)
        parser.validate_ir(ir,file_path=filename,raw_dir=raw)
        parsed = write_sidecar(ir,parsed_dir=directory/"parsed",doc_id="doc-"+doc_id,engine=engine)
        return parsed["content"],parsed["blocks_path"]
    return await asyncio.to_thread(normalize)


async def split_document(filename, data, doc_id, options):
    import asyncio
    from lightrag.utils import TiktokenTokenizer, EmbeddingFunc, enforce_chunk_token_limit_before_embedding
    from lightrag.chunker import (chunking_by_fixed_token, chunking_by_recursive_character,
        chunking_by_paragraph_semantic, chunking_by_structure, chunking_by_semantic_vector)
    strategy,size,overlap = options["strategy"], options["size"], options["overlap"]
    tokenizer = TiktokenTokenizer()
    with tempfile.TemporaryDirectory(prefix="gptonto-") as tmp:
        engine = options.get("parser","auto")
        if engine in {"mineru","docling"}:
            text, blocks = await parse_external(filename,data,doc_id,Path(tmp),engine)
        else:
            text, blocks = await asyncio.to_thread(parse_document,filename,data,doc_id,Path(tmp),engine)
        resolved_engine = ("native" if Path(filename).suffix.lower() in {".docx",".md"} else "legacy") if engine=="auto" else engine
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
        return [{**chunk,"chunk_order_index":i,"chunking_strategy":strategy,"parse_engine":resolved_engine} for i,chunk in enumerate(chunks) if chunk.get("content","").strip()]
