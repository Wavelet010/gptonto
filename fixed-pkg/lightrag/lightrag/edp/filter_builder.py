"""Filter Builder — JSON filter → backend-specific expression.

Accepts structured JSON and translates to Milvus expr / future backends.
Never exposes raw backend syntax at the API level.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FilterResult:
    """Backend-independent filter representation."""
    backend: str                        # "milvus" | "nano" | "qdrant"
    expr: str                           # backend-specific filter expression
    fields: list[str] = field(default_factory=list)  # field names used
    is_empty: bool = True               # True when no filtering needed


# Fields that live in the Milvus schema (not inside $meta).
# When meta_prefix is True, every OTHER field is wrapped as $meta["field"].
_RESERVED_FIELDS: frozenset[str] = frozenset({
    "id", "vector", "full_doc_id", "content", "file_path", "created_at",
})


class FilterBuilder:
    """Translate JSON metadata_filter to backend-specific expressions.

    Supported JSON shapes::

        {"field": "value"}                       # ==
        {"field": ["v1", "v2"]}                  # IN (...)
        {"field": {"gte": 1, "lte": 10}}         # range
        {"field": {"contains": "text"}}           # LIKE %text%
        {"f1": "a", "f2": "b"}                    # AND
        {"$or": [{"a": 1}, {"b": 2}]}             # OR
    """

    @staticmethod
    def parse(metadata_filter: dict | str | None,
              meta_prefix: bool = False) -> FilterResult:
        """Parse a JSON filter dict to a backend-independent result.

        Accepts dict or JSON string (backward compat). Returns FilterResult
        with ``is_empty=True`` when no filter should be applied.

        When *meta_prefix* is True, dynamic fields are wrapped as
        ``$meta["field"]`` so the expression works against Milvus's
        dynamic JSON field.
        """
        if metadata_filter is None:
            return FilterResult(backend="nano", expr="", is_empty=True)

        if isinstance(metadata_filter, str):
            stripped = metadata_filter.strip()
            if not stripped:
                return FilterResult(backend="nano", expr="", is_empty=True)
            try:
                metadata_filter = json.loads(stripped)
            except json.JSONDecodeError:
                return FilterResult(backend="nano", expr="", is_empty=True)

        if not isinstance(metadata_filter, dict) or not metadata_filter:
            return FilterResult(backend="nano", expr="", is_empty=True)

        return FilterBuilder._build(metadata_filter, meta_prefix=meta_prefix)

    @staticmethod
    def to_milvus_expr(metadata_filter: dict | str | None,
                       meta_prefix: bool = False) -> str:
        """Shortcut: parse JSON → Milvus expr string."""
        result = FilterBuilder.parse(metadata_filter, meta_prefix=meta_prefix)
        return result.expr

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build(filt: dict[str, Any], meta_prefix: bool = False) -> FilterResult:
        """Build a FilterResult from a validated dict.

        Supports ``$or`` for OR logic::

            {"$or": [{"a": 1}, {"b": 2}]}  →  (a == 1) or (b == 2)
            {"x": 1, "$or": [{"a": 2}, {"b": 3}]}  →  x == 1 and ((a == 2) or (b == 3))
        """
        parts: list[str] = []
        fields: list[str] = []

        for field, value in filt.items():
            if field == "$or":
                if isinstance(value, list) and value:
                    or_parts: list[str] = []
                    for sub in value:
                        if isinstance(sub, dict) and sub:
                            sub_result = FilterBuilder._build(sub, meta_prefix=meta_prefix)
                            if not sub_result.is_empty:
                                or_parts.append(f"({sub_result.expr})")
                                fields.extend(sub_result.fields)
                    if or_parts:
                        parts.append("(" + " or ".join(or_parts) + ")")
            else:
                clause = FilterBuilder._clause(field, value, meta_prefix=meta_prefix)
                if clause:
                    parts.append(clause)
                    fields.append(field)

        if not parts:
            return FilterResult(backend="nano", expr="", is_empty=True)

        expr = " and ".join(parts)
        return FilterResult(
            backend="milvus",
            expr=expr,
            fields=list(set(fields)),
            is_empty=False,
        )

    @staticmethod
    def _clause(field: str, value: Any, meta_prefix: bool = False) -> str:
        """Build a single filter clause for *field*."""
        # Wrap dynamic fields in $meta["field"] when meta_prefix is True
        ref = (
            f'$meta["{field}"]'
            if (meta_prefix and field not in _RESERVED_FIELDS)
            else field
        )

        # List → IN (...)
        if isinstance(value, list):
            if len(value) == 1:
                return f'{ref} == {FilterBuilder._quote(value[0])}'
            quoted = ", ".join(FilterBuilder._quote(v) for v in value)
            return f"{ref} in [{quoted}]"

        # Dict → range / contains operators
        if isinstance(value, dict):
            sub: list[str] = []
            for op, val in value.items():
                if op == "gte":
                    sub.append(f"{ref} >= {FilterBuilder._number(val)}")
                elif op == "lte":
                    sub.append(f"{ref} <= {FilterBuilder._number(val)}")
                elif op == "gt":
                    sub.append(f"{ref} > {FilterBuilder._number(val)}")
                elif op == "lt":
                    sub.append(f"{ref} < {FilterBuilder._number(val)}")
                elif op == "ne":
                    sub.append(f"{ref} != {FilterBuilder._quote(val)}")
                elif op == "contains":
                    sub.append(f'{ref} like "%{FilterBuilder._escape_like(val)}%"')
            return " and ".join(sub) if sub else ""

        # Scalar → ==
        return f"{ref} == {FilterBuilder._quote(value)}"

    @staticmethod
    def _quote(value: Any) -> str:
        """Quote a string value for Milvus expr."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value)
        # Escape backslash, double-quote, and control characters.
        s = s.replace("\\", "\\\\")
        s = s.replace('"', '\\"')
        s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return f'"{s}"'

    @staticmethod
    def _number(value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    @staticmethod
    def _escape_like(value: Any) -> str:
        """Escape special chars in a LIKE pattern."""
        s = str(value)
        for ch in ["\\", "%", "_"]:
            s = s.replace(ch, "\\" + ch)
        return s
