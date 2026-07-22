"""Metadata Filter — convert user-friendly filter expressions to Milvus expr.

Supports the enterprise-filter mini-language:
    field == "value"
    field != "value"
    field >= 123
    field < 100
    expr && expr
    expr || expr
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# === Tokenizer ===
_TOKEN_RE = re.compile(
    r"""
    (==|!=|>=|<=|>|<)            # operators (longest match first)
    |&&                           # logical AND
    |\|\|                         # logical OR
    |"[^"]*"                      # double-quoted string
    |'[^']*'                      # single-quoted string
    |\d+\.?\d*                    # number
    |[a-zA-Z_]\w*                 # field identifier
    |[()]                         # parentheses
    """
    , re.VERBOSE,
)

_OPS_MAP = {
    "==": "==",
    "!=": "!=",
    ">=": ">=",
    "<=": "<=",
    ">": ">",
    "<": "<",
    "&&": "and",
    "||": "or",
}


class EDPMetadataFilter:
    """Parse user-facing filter expressions into Milvus-compatible strings."""

    @staticmethod
    def parse(expression: str | None) -> str:
        """Convert a user filter expression to a Milvus expr string.

        Examples:
            ``material=="GD1265"`` → ``material == "GD1265"``
            ``a=="x" && b>="y"`` → ``a == "x" and b >= "y"``

        Returns an empty string for None, empty, or unparseable input
        (soft filter fallback).
        """
        if not expression or not expression.strip():
            return ""

        try:
            tokens = _tokenize(expression.strip())
            if not tokens:
                return ""
            result = _to_milvus_expr(tokens)
            return result
        except Exception:
            logger.debug(
                "Failed to parse metadata filter expression: %s", expression,
                exc_info=True,
            )
            return ""

    @staticmethod
    def validate(expression: str) -> bool:
        """Return True if *expression* can be parsed successfully."""
        return bool(EDPMetadataFilter.parse(expression))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _tokenize(expr: str) -> list[str]:
    """Tokenize the input into a flat list of tokens."""
    tokens: list[str] = []
    pos = 0
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(expr, pos)
        if m:
            raw = m.group(0)
            tokens.append(raw)
            pos = m.end()
        else:
            raise ValueError(f"Unexpected character at position {pos}: {expr[pos]!r}")
    return tokens


def _to_milvus_expr(tokens: list[str]) -> str:
    """Convert token list to a Milvus expr string."""
    parts: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _OPS_MAP:
            parts.append(_OPS_MAP[tok])
        elif tok.startswith('"') or tok.startswith("'"):
            # Keep quoted strings as-is (Milvus understands double quotes).
            val = tok[1:-1]  # strip outer quotes
            parts.append(f'"{val}"')
        elif tok.isdigit() or (tok[0].isdigit() and tok.replace(".", "", 1).isdigit()):
            parts.append(tok)
        elif re.match(r"^[a-zA-Z_]\w*$", tok):
            parts.append(tok)
        else:
            parts.append(tok)
        i += 1

    return " ".join(parts)
