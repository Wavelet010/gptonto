"""Context Builder — construct ``embedding_text`` from chunk metadata.

Each chunk gets two text representations:
- ``chunk_text`` (== ``content``): untouched original, used by Graph/Entity Extraction.
- ``embedding_text``: enriched text for vectorization, built from mode:

  =========  ==============================================================
  rich       doc_title + heading_path + content (full text, current default)
  compact    only metadata fields listed in ``compact_fields``
  raw        same as content (no enrichment)
  =========
"""

from __future__ import annotations

from typing import Any

from lightrag.edp.models import ContextBuilderConfig


def build_embedding_text(
    chunk: dict[str, Any],
    *,
    doc_title: str = "",
    config: ContextBuilderConfig | None = None,
) -> str:
    """Build enriched embedding text for a single chunk.

    Supports three modes via *config.mode*:

    - ``"rich"``: doc_title + heading_breadcrumb + content
    - ``"compact"``: doc_title + values of *config.compact_fields*
    - ``"raw"``: returns chunk content unchanged
    """
    if config is None:
        config = ContextBuilderConfig()

    if not config.enabled or config.mode == "raw":
        return chunk.get("content", "")

    # --- compact mode: metadata-only ---
    # compact_fields defines the metadata values; doc_title and
    # heading_breadcrumb are optionally prepended when configured
    # AND not already listed in compact_fields (avoid duplication).
    # If summary (LLM-generated) is missing, degrade to rich mode.
    if config.mode == "compact":
        parts: list[str] = []
        compact_set = set(config.compact_fields)
        # Prepend doc_title when configured and not already a compact field
        if config.include_doc_title and doc_title and "title" not in compact_set:
            parts.append(doc_title)
        # Prepend heading_path when configured and not already a compact field
        if config.include_heading_path and "heading_path" not in compact_set:
            breadcrumb = chunk.get("heading_breadcrumb", "")
            if breadcrumb:
                parts.append(breadcrumb)
        # Append compact_fields values
        for field_name in config.compact_fields:
            val = chunk.get(field_name)
            if field_name == "summary" and (not val or val == ""):
                # Summary is the core of compact mode — without it,
                # fall through to rich mode for better structure.
                parts = []
                break
            if val is not None and val != "" and val is not False:
                parts.append(str(val))
        if parts:
            return config.sep.join(parts)
        # Empty parts → fall through to rich mode below

    # --- rich mode (default): doc_title + heading_path + content ---
    parts: list[str] = []
    if config.include_doc_title and doc_title:
        parts.append(doc_title)
    if config.include_heading_path:
        breadcrumb = chunk.get("heading_breadcrumb", "")
        if breadcrumb:
            parts.append(breadcrumb)
    content = chunk.get("content", "")
    if content:
        parts.append(content)
    return config.sep.join(parts)


def enrich_chunks_with_context(
    chunks: list[dict[str, Any]],
    *,
    doc_title: str = "",
    config: ContextBuilderConfig | None = None,
) -> list[dict[str, Any]]:
    """Post-process a chunk list: set ``chunk_text`` and ``embedding_text``.

    ``chunk_text`` preserves the original content for entity extraction and graph.
    ``embedding_text`` is the enriched version used for vector embedding.
    """
    if config is None:
        config = ContextBuilderConfig()

    for chunk in chunks:
        # Preserve original content as chunk_text for downstream extraction.
        chunk["chunk_text"] = chunk.get("content", "")

        if config.enabled:
            chunk["embedding_text"] = build_embedding_text(
                chunk, doc_title=doc_title, config=config
            )
        else:
            # When disabled, embedding_text is the same as chunk_text.
            chunk["embedding_text"] = chunk.get("content", "")

    return chunks
