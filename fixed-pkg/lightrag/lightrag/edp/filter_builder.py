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


class FilterBuilder:
    """Translate JSON metadata_filter to backend-specific expressions.

    Supported JSON shapes::

        {"field": "value"}                       # ==
        {"field": ["v1", "v2"]}                  # IN (...)
        {"field": {"gte": 1, "lte": 10}}         # range
        {"f1": "a", "f2": "b"}                    # AND
    """

    @staticmethod
    def parse(metadata_filter: dict | str | None) -> FilterResult:
        """Parse a JSON filter dict to a backend-independent result.

        Accepts dict or JSON string (backward compat). Returns FilterResult
        with ``is_empty=True`` when no filter should be applied.
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
                # Not valid JSON, treat as empty.
                return FilterResult(backend="nano", expr="", is_empty=True)

        if not isinstance(metadata_filter, dict) or not metadata_filter:
            return FilterResult(backend="nano", expr="", is_empty=True)

        return FilterBuilder._build(metadata_filter)

    @staticmethod
    def to_milvus_expr(metadata_filter: dict | str | None) -> str:
        """Shortcut: parse JSON → Milvus expr string."""
        result = FilterBuilder.parse(metadata_filter)
        return result.expr

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build(filt: dict[str, Any]) -> FilterResult:
        """Build a FilterResult from a validated dict.

        Supports ``$or`` for OR logic::

            {"$or": [{"a": 1}, {"b": 2}]}  →  (a == 1) or (b == 2)
            {"x": 1, "$or": [{"a": 2}, {"b": 3}]}  →  x == 1 and ((a == 2) or (b == 3))
        """
        parts: list[str] = []
        fields: list[str] = []

        for field, value in filt.items():
            if field == "$or":
                # Nested OR group: each element is a sub-filter dict (ANDed internally)
                if isinstance(value, list) and value:
                    or_parts: list[str] = []
                    for sub in value:
                        if isinstance(sub, dict) and sub:
                            sub_result = FilterBuilder._build(sub)
                            if not sub_result.is_empty:
                                or_parts.append(f"({sub_result.expr})")
                                fields.extend(sub_result.fields)
                    if or_parts:
                        parts.append("(" + " or ".join(or_parts) + ")")
            else:
                clause = FilterBuilder._clause(field, value)
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
    def _clause(field: str, value: Any) -> str:
        """Build a single filter clause for *field*."""
        # List → IN (...)
        if isinstance(value, list):
            if len(value) == 1:
                return f'{field} == {FilterBuilder._quote(value[0])}'
            quoted = ", ".join(FilterBuilder._quote(v) for v in value)
            return f"{field} in [{quoted}]"

        # Dict → range operators
        if isinstance(value, dict):
            sub: list[str] = []
            for op, val in value.items():
                if op == "gte":
                    sub.append(f"{field} >= {FilterBuilder._number(val)}")
                elif op == "lte":
                    sub.append(f"{field} <= {FilterBuilder._number(val)}")
                elif op == "gt":
                    sub.append(f"{field} > {FilterBuilder._number(val)}")
                elif op == "lt":
                    sub.append(f"{field} < {FilterBuilder._number(val)}")
                elif op == "ne":
                    sub.append(f"{field} != {FilterBuilder._quote(val)}")
                elif op == "contains":
                    sub.append(f'{field} like "%{FilterBuilder._escape_like(val)}%"')
            return " and ".join(sub) if sub else ""

        # Scalar → ==
        return f"{field} == {FilterBuilder._quote(value)}"

    @staticmethod
    def _quote(value: Any) -> str:
        """Quote a string value for Milvus expr."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value)
        # Escape internal double quotes.
        s = s.replace('"', '\\"')
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
        for ch in ["%", "_", "\\"]:
            s = s.replace(ch, "\\" + ch)
        return s
