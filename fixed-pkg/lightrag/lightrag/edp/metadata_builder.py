"""Metadata Builder — extract structured metadata from documents and chunks.

Supports six metadata sources:
- ``filename``: extracted once from the document filename (regex / auto).
- ``document``: extracted once per document (LLM).
- ``heading``: automatically derived from the heading tree.
- ``llm``: per-chunk LLM extraction (batched, with retry and graceful degradation).
- ``rule``: per-chunk regex extraction.
- ``manual``: placeholder values for hand-editing.
"""

from __future__ import annotations

import re
import logging
from typing import Any

from lightrag.edp.models import (
    MetadataBuilderConfig,
    MetadataFieldConfig,
    FilenameFieldConfig,
)

logger = logging.getLogger(__name__)


# =========================================================================
# Public API
# =========================================================================


class MetadataBuilder:
    """Builds per-chunk metadata from six configurable sources.

    Usage::

        mb = MetadataBuilder(config)
        mb.extract_filename_metadata(filename_base)   # optional, once
        await mb.apply_to_chunks(chunks, rag, content)# heading + rule + manual + llm
    """

    def __init__(self, config: MetadataBuilderConfig) -> None:
        self.config = config
        self._doc_metadata: dict[str, Any] = {}
        self._rule_cache: dict[str, re.Pattern] = {}

    # ------------------------------------------------------------------
    # Filename extraction (document-level, called once)
    # ------------------------------------------------------------------

    def extract_filename_metadata(self, filename_base: str) -> dict[str, Any]:
        """Extract metadata from *filename_base* (filename without extension).

        Returns a dict of field-name → value.  Call once per document and
        pass the result to ``apply_to_chunks`` via *extra_doc_meta*.
        """
        if not self.config.filename_fields or not filename_base:
            return {}

        # Topological sort so ``depends_on`` fields are resolved first.
        ordered = _topo_sort_fields(self.config.filename_fields)
        extracted: dict[str, Any] = {}

        for field in ordered:
            val = _extract_one_filename_field(field, filename_base, extracted)
            key = field.output_key or field.name
            extracted[key] = val

        # Also store so build_chunk_metadata can include it.
        self._doc_metadata.update(extracted)
        return extracted

    # ------------------------------------------------------------------
    # Unified chunk-level entry point
    # ------------------------------------------------------------------

    async def apply_to_chunks(
        self,
        chunks: list[dict[str, Any]],
        rag: Any,
        full_content: str,
        *,
        extra_doc_meta: dict[str, Any] | None = None,
    ) -> None:
        """Build metadata for every chunk in *chunks* (mutates in-place).

        Sources are applied in order: heading → filename/doc → rule →
        manual → document (LLM) → llm (chunk-level LLM).
        """
        doc_meta = dict(self._doc_metadata)
        if extra_doc_meta:
            doc_meta.update(extra_doc_meta)

        # ---- heading + rule + manual (no LLM) ----
        for ch in chunks:
            meta: dict[str, Any] = {}
            meta.update(self._extract_heading_metadata(ch))
            meta.update(doc_meta)
            if self.config.rule_fields:
                meta.update(self._extract_rule_metadata(ch.get("content", "")))
            if self.config.manual_fields:
                meta.update(self._extract_manual_metadata())
            ch.setdefault("_edp_meta", {}).update(meta)

        # ---- document-level LLM (once for all chunks) ----
        if self.config.document_fields:
            await _extract_document_llm(rag, self.config.document_fields,
                                        chunks, full_content)

        # ---- chunk-level LLM (batched, per chunk) ----
        if self.config.llm_fields:
            await _extract_chunk_llm(rag, self.config.llm_fields, chunks)

    # ------------------------------------------------------------------
    # Heading extraction
    # ------------------------------------------------------------------

    def _extract_heading_metadata(self, chunk: dict[str, Any]) -> dict[str, Any]:
        """Extract heading-derived fields from the chunk."""
        meta: dict[str, Any] = {}
        for field in self.config.heading_fields:
            key = field.output_key or field.name
            if field.name == "heading_path":
                meta[key] = chunk.get("heading_breadcrumb", "")
            elif field.name == "section_depth":
                meta[key] = chunk.get("level", 0)
            elif field.name == "knowledge_type":
                parents = chunk.get("parent_headings", [])
                if parents:
                    meta[key] = parents[0]
                else:
                    meta[key] = chunk.get("heading", "")
            elif field.name in ("section_id", "parent_section_id"):
                meta[key] = chunk.get(field.name, "")
            else:
                meta[key] = chunk.get(field.name, "")
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
    # Manual fields
    # ------------------------------------------------------------------

    def _extract_manual_metadata(self) -> dict[str, Any]:
        """Return empty-placeholder values for manual fields."""
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


# =========================================================================
# Filename extraction helpers
# =========================================================================


def _topo_sort_fields(
    fields: list[FilenameFieldConfig],
) -> list[FilenameFieldConfig]:
    """Topological sort: no-dep fields first, depends_on fields after.

    Resolves dependencies by ``output_key or name`` to match the storage
    key used during extraction.
    """
    ordered: list[FilenameFieldConfig] = []
    resolved: set[str] = set()
    remaining = list(fields)

    def _key(f: FilenameFieldConfig) -> str:
        return f.output_key or f.name

    while remaining:
        progress = False
        for f in list(remaining):
            if not f.depends_on or f.depends_on in resolved:
                ordered.append(f)
                remaining.remove(f)
                resolved.add(_key(f))
                progress = True
        if not progress:
            ordered.extend(remaining)
            break
    return ordered


def _substitute_value(template: str, match: re.Match) -> str:
    """Replace ``{1}`` / ``{matched}`` placeholders in *template*."""
    if template == "{matched}":
        return match.group(0)
    if template == "{1}":
        try:
            return match.group(1)
        except IndexError:
            return match.group(0)
    result = template.replace("{matched}", match.group(0))
    try:
        result = result.replace("{1}", match.group(1) or "")
    except IndexError:
        result = result.replace("{1}", match.group(0))
    return result


def _extract_one_filename_field(
    field: FilenameFieldConfig,
    filename_base: str,
    extracted: dict[str, Any],
) -> str:
    """Extract a single filename field value."""
    # auto_file_name: use the bare filename directly.
    if field.auto_file_name:
        return filename_base

    # Determine the target string to match against.
    if field.depends_on:
        target = str(extracted.get(field.depends_on, ""))
        if not target:
            return ""
    else:
        target = filename_base

    if not field.rules:
        return ""

    # Sort rules: highest priority first; same priority → original order.
    sorted_rules = sorted(field.rules, key=lambda r: -r.priority)

    best_match: str | None = None
    best_priority: int = -1
    for rule in sorted_rules:
        try:
            m = re.search(rule.pattern, target)
            if m:
                if rule.priority > best_priority:
                    best_match = _substitute_value(rule.value, m)
                    best_priority = rule.priority
        except re.error:
            logger.warning(
                "Invalid regex in filename field '%s': %s",
                field.name, rule.pattern,
            )

    return best_match or ""


# =========================================================================
# LLM extraction (document-level + chunk-level)
# =========================================================================


async def _extract_document_llm(
    rag: Any,
    fields: list[MetadataFieldConfig],
    chunks: list[dict[str, Any]],
    full_content: str,
) -> None:
    """LLM extraction for document-level fields — one call, applied to all chunks."""
    import json as _json

    prompts = []
    prompt_keys: dict[str, str] = {}  # prompt key → output key
    for f in fields:
        if f.llm_prompt:
            key = f.output_key or f.name
            prompts.append(f"- {key}: {f.llm_prompt}")
            prompt_keys[key] = key
    if not prompts:
        return

    sys_msg = (
        "You are a metadata extraction assistant. Extract the requested "
        "fields from the document. Return a JSON object with field names "
        "as keys. Return ONLY the JSON, no explanation."
    )
    user_msg = (
        "Document:\n"
        f"{full_content[:8000]}\n\n"
        "Extract these fields:\n"
        + "\n".join(prompts)
        + "\n\nReturn as JSON:"
    )
    try:
        result = await _llm_call(rag, sys_msg, user_msg)
        raw = _extract_json(result)
        if not raw:
            raise ValueError("empty LLM response")
        doc_meta = _json.loads(raw)
        if isinstance(doc_meta, dict):
            for ch in chunks:
                ch.setdefault("_edp_meta", {}).update(doc_meta)
            logger.info(
                "EDP document metadata extracted: %s",
                list(doc_meta.keys()),
            )
    except Exception:
        logger.warning(
            "EDP document-level LLM extraction failed — "
            "title/equipment will be missing",
            exc_info=True,
        )


async def _extract_chunk_llm(
    rag: Any,
    fields: list[MetadataFieldConfig],
    chunks: list[dict[str, Any]],
) -> None:
    """LLM extraction for chunk-level fields — batched, with retry and
    per-chunk fallback."""
    import json as _json

    field_specs = [f for f in fields if f.llm_prompt]
    if not field_specs:
        return

    prompts = [
        f"- {f.output_key or f.name}: {f.llm_prompt}" for f in field_specs
    ]
    sys_msg = (
        "You are a metadata extraction assistant. For each chunk, extract "
        "the requested fields. Return a JSON array: "
        '[{"chunk":0,"field1":"value1",...},...]. '
        "Return ONLY the JSON, no explanation."
    )

    BATCH_SIZE = 5
    extracted = 0

    for batch_start in range(0, len(chunks), BATCH_SIZE):
        batch_chunks = chunks[batch_start:batch_start + BATCH_SIZE]

        # ---- Try batch extraction first ----
        arr: list | None = None
        for attempt in range(2):
            try:
                blocks = []
                for local_i, ch in enumerate(batch_chunks):
                    blocks.append(
                        f"[Chunk {local_i}]"
                        f"\n{ch.get('content', '')[:2000]}\n"
                    )
                user_msg = (
                    f"Chunks:\n{''.join(blocks)}\n"
                    "Extract these fields for EACH chunk:\n"
                    + "\n".join(prompts)
                    + "\n\nReturn as JSON array:"
                )
                result = await _llm_call(rag, sys_msg, user_msg)
                raw = _extract_json(result)
                if not raw:
                    continue
                arr = _json.loads(raw)
                if isinstance(arr, list) and len(arr) > 0:
                    break
                arr = None
            except Exception:
                arr = None
                if attempt < 1:
                    continue

        if isinstance(arr, list):
            for item in arr:
                local_idx = item.get("chunk", -1)
                global_idx = batch_start + local_idx
                if 0 <= local_idx < len(batch_chunks):
                    item.pop("chunk", None)
                    chunks[global_idx].setdefault(
                        "_edp_meta", {}
                    ).update(item)
                    extracted += 1
            continue  # batch succeeded

        # ---- Batch failed → per-chunk fallback ----
        logger.warning(
            "EDP chunk-level batch [%d:%d] failed — "
            "falling back to per-chunk extraction",
            batch_start, batch_start + len(batch_chunks),
        )
        for offset, ch in enumerate(batch_chunks):
            global_idx = batch_start + offset
            try:
                user_msg = (
                    f"Chunk:\n{ch.get('content', '')[:3000]}\n\n"
                    "Extract these fields for this chunk:\n"
                    + "\n".join(prompts)
                    + "\n\nReturn as JSON object:"
                )
                single_sys = (
                    "You are a metadata extraction assistant. "
                    "Extract the requested fields from the chunk. "
                    "Return a JSON object with field names as keys. "
                    "Return ONLY the JSON, no explanation."
                )
                result = await _llm_call(rag, single_sys, user_msg)
                raw = _extract_json(result)
                if raw:
                    obj = _json.loads(raw)
                    if isinstance(obj, dict):
                        chunks[global_idx].setdefault(
                            "_edp_meta", {}
                        ).update(obj)
                        extracted += 1
            except Exception:
                pass

    if extracted:
        logger.info(
            "EDP chunk-level metadata extracted for %d/%d chunks",
            extracted, len(chunks),
        )
    else:
        logger.warning(
            "EDP chunk-level LLM metadata extraction failed — "
            "summary/keywords will use fallback values."
        )


# =========================================================================
# Shared LLM helpers
# =========================================================================


def _extract_json(text: str) -> str:
    """Strip markdown code fences from LLM JSON responses."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


async def _llm_call(rag: Any, system_prompt: str, user_prompt: str) -> str:
    """Simple LLM call via bypass mode."""
    from lightrag.base import QueryParam

    param = QueryParam(mode="bypass")
    result = await rag.aquery(
        query=user_prompt,
        param=param,
        system_prompt=system_prompt,
    )
    if isinstance(result, str):
        return result
    return str(result)
