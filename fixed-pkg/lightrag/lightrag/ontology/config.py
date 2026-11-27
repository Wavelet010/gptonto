"""Resolve and freeze upstream parser and chunker options before enqueue."""
import os
from pathlib import Path


def profile_manager():
    from lightrag.edp.profile_manager import ProfileManager
    mount=Path(os.getenv("GPTONTO_EDP_PROFILES_DIR","/app/lightrag/lightrag/edp/profiles"))
    return ProfileManager(custom_dirs=[mount] if mount.is_dir() else None)


def resolve_options(filename,options,explicit):
    from lightrag.parser.routing import (resolve_parser_directives,parse_process_options,
        resolve_chunk_options,chunk_strategy_key,encode_parse_engine)
    from lightrag.parser.registry import suffix_capabilities,engine_endpoint_configured,engine_endpoint_requirement
    out=dict(options)
    directives=resolve_parser_directives(filename) if out["parser"]=="auto" else None
    process=parse_process_options(directives.process_options) if directives else None
    engine=directives.engine if directives else out["parser"]
    if Path(filename).suffix.lower().lstrip(".") not in suffix_capabilities(engine):
        raise ValueError("Unsupported file type for "+engine)
    if not engine_endpoint_configured(engine):
        raise ValueError("Parser requires "+str(engine_endpoint_requirement(engine)))
    out["resolved_parser"]=engine
    out["parser_params"]={**(directives.engine_params if directives else {}),**out.get("parser_params",{})}
    encode_parse_engine(engine,out["parser_params"]) # original parameter validation
    if process and "strategy" not in explicit and process.chunking_explicit:
        out["strategy"]=process.chunking
    if process and "modalities" not in explicit:
        out["modalities"]=[k for k in ("images","tables","equations") if getattr(process,k)]
    strategy=out["strategy"]
    if strategy!="H":
        key=chunk_strategy_key(strategy)
        params=dict(resolve_chunk_options(None,process_options=strategy)[key])
        if directives:
            params.update(directives.chunk_params.get(strategy,{}))
        params.update(out.get("chunk_params",{}))
        size=params.pop("chunk_token_size",os.getenv("CHUNK_SIZE",1200))
        overlap=params.pop("chunk_overlap_token_size",0 if strategy=="S" else os.getenv("CHUNK_OVERLAP_SIZE",100))
        if "size" not in explicit: out["size"]=int(size)
        if "overlap" not in explicit: out["overlap"]=int(overlap)
        if strategy=="S":
            # Original S embedding_mode is context-only, never an ontology chunker kwarg.
            mode=params.pop("embedding_mode",None)
            name=out.get("edp_profile") or os.getenv("EDP_DEFAULT_PROFILE","")
            if name:
                manager=profile_manager()
                if name not in manager.list_profiles():
                    raise ValueError("Unknown EDP parser profile: "+name)
                profile=manager.get_profile(name)
                out["edp_profile"]=name
                out["edp_profile_data"]=profile.model_dump(mode="json")
                if profile.heading_levels:
                    params["heading_levels"]=profile.heading_levels
            if mode:
                out["edp_embedding_mode"]=mode
        out["chunk_params"]=params
    if not 64<=out["size"]<=16000 or not 0<=out["overlap"]<out["size"]:
        raise ValueError("Resolved chunk size/overlap invalid")
    return out
