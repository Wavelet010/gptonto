"""LightRAG Enterprise Document Parser (EDP) — structure-aware chunking,
context enrichment, metadata extraction, and profile-driven parsing."""

from lightrag.edp.models import (
    ContextBuilderConfig,
    MetadataBuilderConfig,
    MetadataFieldConfig,
    ParserProfile,
)

from lightrag.edp.context_builder import (
    build_embedding_text,
    enrich_chunks_with_context,
)

from lightrag.edp.metadata_builder import MetadataBuilder

from lightrag.edp.metadata_filter import EDPMetadataFilter

from lightrag.edp.profile_manager import ProfileManager

from lightrag.edp.chunk_strategy_s import chunking_by_structure

__all__ = [
    # models
    "ContextBuilderConfig",
    "MetadataBuilderConfig",
    "MetadataFieldConfig",
    "ParserProfile",
    # context builder
    "build_embedding_text",
    "enrich_chunks_with_context",
    # metadata
    "MetadataBuilder",
    "EDPMetadataFilter",
    # profile
    "ProfileManager",
    # chunker
    "chunking_by_structure",
]
