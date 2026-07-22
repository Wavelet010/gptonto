"""Progressive Fallback — strict → relax → global.

Implements the metadata filter fallback strategy for vector search.
Ensures recall rate stays high even when filters are too restrictive.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from lightrag.edp.filter_builder import FilterBuilder, FilterResult

logger = logging.getLogger(__name__)

# Callable signature: async def search(filter_expr: str) -> list[dict]
SearchFunc = Callable[[str], Any]


class ProgressiveFallback:
    """Progressive fallback for metadata-filtered vector search.

    Strategy (soft mode):
        1. strict  — use full filter
        2. relax   — broaden filter (if steps defined)
        3. global  — remove filter entirely

    Default behaviour when no profile/config is provided:
        strict → global (single-step fallback).
    """

    def __init__(
        self,
        filter_json: dict | None,
        *,
        top_k: int = 10,
        min_results: int = 5,
        relax_fields: list[str] | None = None,
    ) -> None:
        self._filter_json = filter_json
        self._top_k = top_k
        self._min_results = min_results or max(3, top_k // 2)
        self._relax_fields = relax_fields or []

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def execute(self, search: SearchFunc) -> list[dict]:
        """Run progressive fallback search.

        Args:
            search: async callable ``(filter_expr: str) -> list[dict]``.

        Returns:
            Best-effort result list (up to top_k items).
        """
        if not self._filter_json:
            return await search("")

        # --- Step 1: strict ---
        strict_expr = FilterBuilder.to_milvus_expr(self._filter_json)
        if not strict_expr:
            return await search("")

        results = await search(strict_expr)
        logger.debug(
            "Fallback strict: expr=%r → %d results (min=%d)",
            strict_expr, len(results), self._min_results,
        )
        if len(results) >= self._min_results:
            return results[: self._top_k]

        # --- Step 2: relax ---
        if self._relax_fields:
            relaxed = self._build_relaxed_filter()
            relax_expr = FilterBuilder.to_milvus_expr(relaxed)
            if relax_expr and relax_expr != strict_expr:
                relax_results = await search(relax_expr)
                merged = self._merge_results(results, relax_results)
                logger.debug(
                    "Fallback relax: expr=%r → total=%d results",
                    relax_expr, len(merged),
                )
                if len(merged) >= self._min_results:
                    return merged[: self._top_k]
                results = merged

        # --- Step 3: global ---
        global_results = await search("")
        merged = self._merge_results(results, global_results)
        logger.debug(
            "Fallback global: total=%d results (final)",
            len(merged),
        )
        return merged[: self._top_k]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_relaxed_filter(self) -> dict:
        """Build a relaxed filter by OR-ing relax_fields with original."""
        original = self._filter_json or {}
        relaxed: dict[str, Any] = {}

        # For relax: use IN with broader values, or drop restrictive fields.
        for field in self._relax_fields:
            if field in original:
                # Keep the field but drop the value constraint → effectively
                # we keep the original field AND OR with broader set.
                pass

        # Simple relax: just use original filter without the most restrictive
        # field (drop the last field).
        if len(original) > 1:
            keys = list(original.keys())
            return {k: original[k] for k in keys[:-1]}

        # If only one field, can't relax further → return original.
        return dict(original)

    @staticmethod
    def _merge_results(
        existing: list[dict],
        additional: list[dict],
    ) -> list[dict]:
        """Merge two result lists, deduplicating by id, preserving order."""
        seen: set[str] = {r.get("id", "") for r in existing}
        merged = list(existing)
        for r in additional:
            rid = r.get("id", "")
            if rid not in seen:
                seen.add(rid)
                merged.append(r)
        return merged
