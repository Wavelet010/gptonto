"""Metadata Builder — extract structured metadata from documents and chunks.

Supports four metadata sources:
- ``document``: extracted once per document (LLM or rule).
- ``heading``: automatically derived from the heading tree.
- ``llm``: per-chunk LLM extraction based on a prompt template.
- ``rule``: per-chunk regex extraction.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Callable

from lightrag.edp.models import MetadataBuilderConfig, MetadataFieldConfig

logger = logging.getLogger(__name__)


class MetadataBuilder:
    """Builds per-chunk metadata from four configurable sources."""

    def __init__(self, config: MetadataBuilderConfig) -> None:
        self.config = config
        self._doc_metadata: dict[str, Any] = {}
        self._rule_cache: dict[str, re.Pattern] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def build_chunk_metadata(
        self,
        chunk: dict[str, Any],
        *,
        llm_func: Callable | None = None,
    ) -> dict[str, Any]:
        """Build the complete metadata dict for a single chunk.

        Document-level fields are only extracted once (cached).
        Heading fields are derived automatically from the chunk's heading info.
        LLM and rule fields are extracted per-chunk.
        """
        meta: dict[str, Any] = {}

        # 1. Document-level metadata (extracted once).
        if self.config.document_fields and not self._doc_metadata:
            # Doc-level extraction is called separately via set_doc_metadata().
            pass
        meta.update(self._doc_metadata)

        # 2. Heading-derived metadata.
        if self.config.heading_fields:
            heading_meta = self._extract_heading_metadata(chunk)
            meta.update(heading_meta)

        # 3. Per-chunk LLM extraction.
        if self.config.llm_fields and llm_func is not None:
            llm_meta = await self._extract_llm_metadata(
                chunk.get("content", ""), llm_func
            )
            meta.update(llm_meta)

        # 4. Rule-based extraction.
        if self.config.rule_fields:
            rule_meta = self._extract_rule_metadata(chunk.get("content", ""))
            meta.update(rule_meta)

        return meta

    # ------------------------------------------------------------------
    # Document-level (called once per document, before chunking)
    # ------------------------------------------------------------------

    def set_doc_metadata(self, meta: dict[str, Any]) -> None:
        """Set document-level metadata (extracted once per document)."""
        self._doc_metadata.update(meta)

    # ------------------------------------------------------------------
    # Heading extraction
    # ------------------------------------------------------------------

    def _extract_heading_metadata(self, chunk: dict[str, Any]) -> dict[str, Any]:
        """Extract heading-derived fields from the chunk."""
        meta: dict[str, Any] = {}
        for field in self.config.heading_fields:
            if field.name == "heading_path":
                meta[field.name] = chunk.get("heading_breadcrumb", "")
            elif field.name == "section_depth":
                meta[field.name] = chunk.get("level", 0)
            elif field.name == "knowledge_type":
                # Derive from top-level heading (first element of parent chain or current).
                parents = chunk.get("parent_headings", [])
                if parents:
                    meta[field.name] = parents[0]
                else:
                    meta[field.name] = chunk.get("heading", "")
            elif field.name in ("section_id", "parent_section_id"):
                # Direct from chunk (set by tree-split chunker).
                meta[field.name] = chunk.get(field.name, "")
            else:
                # Generic: try chunk key matching field name.
                meta[field.name] = chunk.get(field.name, "")
        return meta

    # ------------------------------------------------------------------
    # LLM extraction
    # ------------------------------------------------------------------

    async def _extract_llm_metadata(
        self, content: str, llm_func: Callable
    ) -> dict[str, Any]:
        """Extract per-chunk LLM fields by calling *llm_func* with the field prompt."""
        meta: dict[str, Any] = {}
        for field in self.config.llm_fields:
            if not field.llm_prompt:
                continue
            try:
                prompt = f"{field.llm_prompt}\n\nText:\n{content[:4000]}"
                result = await llm_func(prompt)
                key = field.output_key or field.name
                meta[key] = result
            except Exception:
                logger.debug(
                    "LLM metadata extraction failed for field '%s'", field.name
                )
        return meta

    # ------------------------------------------------------------------
    # Rule extraction
    # ------------------------------------------------------------------

    def _extract_rule_metadata(self, content: str) -> dict[str, Any]:
        """Extract rule-based fields using regex patterns."""
        meta: dict[str, Any] = {}
        for field in self.config.rule_fields:
            if not field.rule_pattern:
                continue
            key = field.output_key or field.name
            try:
                pattern = self._rule_cache.get(field.name)
                if pattern is None:
                    pattern = re.compile(field.rule_pattern)
                    self._rule_cache[field.name] = pattern

                match = pattern.search(content)
                if field.type == "boolean":
                    meta[key] = match is not None
                else:
                    meta[key] = match.group(0) if match else None
            except re.error:
                logger.debug("Invalid regex for field '%s'", field.name)
        return meta

    # ------------------------------------------------------------------
    # Manual fields (placeholder, filled by user in Milvus/Attu later)
    # ------------------------------------------------------------------

    def _extract_manual_metadata(self) -> dict[str, Any]:
        """Return empty-placeholder values for manual fields.

        These fields exist so the key is present on every chunk in Milvus,
        ready to be hand-edited via Attu or the Milvus API.
        """
        meta: dict[str, Any] = {}
        for field in self.config.manual_fields:
            key = field.output_key or field.name
            if field.type == "boolean":
                meta[key] = False
            elif field.type == "number":
                meta[key] = 0
            else:
                meta[key] = ""
        return meta
