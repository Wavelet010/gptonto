"""Structure-aware heading-tree chunking — the ``"S"`` strategy.

Splits content by DFS-walking the heading tree (markdown + Chinese formats).
Each section whose *total* content fits within ``chunk_token_size`` is emitted
as a single chunk. Oversized sections recurse into sub-headings; only leaf
sections fall back to paragraph / sentence splitting.

Every chunk carries ``section_id`` (the nearest heading that spawned it) and
``parent_section_id`` (its parent heading).

Uses :class:`HeadingDetector` for multi-stage, score-based heading detection
that supports Chinese documents (一、（一）、第一章、1.1 etc).
"""

from __future__ import annotations

import re
from typing import Any

from lightrag.utils import Tokenizer
from lightrag.edp.heading_detector import HeadingDetector

HEADING_BREADCRUMB_SEP = " → "


# ======================================================================
# Public entry point
# ======================================================================


def chunking_by_structure(
    tokenizer: Tokenizer,
    content: str,
    chunk_token_size: int,
    *,
    chunk_overlap_token_size: int = 0,
    doc_title: str = "",
    max_heading_level: int | None = None,
    heading_levels: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Split *content* into chunks guided by its markdown heading hierarchy.

    Returns chunks with ``section_id``, ``parent_section_id``,
    ``heading_path``, ``level``, and ``split_type``.

    Parameters:
        max_heading_level: If set, headings deeper than this level are
            ignored (treated as body text of their parent).
        heading_levels: Override default levels per pattern tag.
            E.g. ``{"chinese_h2_full": 3, "decimal_base": 1}``.
            For ``decimal``, the base level of ``1.``; deeper decimals
            (``1.1``, ``1.1.1``) add +1 per dot.
            Tags: chinese_h1, chinese_h2_full, chinese_h2_half,
            decimal_base, chapter_section.
    """
    if not content or not content.strip():
        return []

    headings = _parse_headings(content)

    # Apply per-pattern absolute level overrides (profile config).
    if heading_levels:
        for h in headings:
            lv = heading_levels.get(h.tag)
            if lv is not None:
                if h.tag == "decimal":
                    # decimal_base: level of "1." (default 3).
                    # Original level = dots + 3, so dots = level - 3.
                    dots = h.level - 3
                    h.level = max(1, min(6, lv + dots))
                else:
                    h.level = max(1, min(6, lv))

    # Filter by max heading level (deeper headings become body text)
    if max_heading_level is not None and max_heading_level > 0:
        headings = [h for h in headings if h.level <= max_heading_level]

    # Edge-case: no headings at all — treat whole text as one leaf.
    if not headings:
        root = _Section(
            heading="",
            parent_headings=[],
            level=0,
            text=content,
            start=0,
            end=len(content),
        )
        chunks = _dfs_tree_split(root, tokenizer, chunk_token_size,
                                 parent_section_id="")
        _assign_order(chunks)
        return chunks

    tree = _build_section_tree(content, headings)
    chunks: list[dict[str, Any]] = []
    for node in tree:
        chunks.extend(
            _dfs_tree_split(node, tokenizer, chunk_token_size,
                            parent_section_id="")
        )
    _assign_order(chunks)
    return chunks


# ======================================================================
# Data structures
# ======================================================================


class _Heading:
    __slots__ = ("level", "text", "start", "end", "tag", "full_text")
    def __init__(self, level: int, text: str, start: int, end: int, tag: str = "", full_text: str = "") -> None:
        self.level = level       # 1..6
        self.text = text.strip()
        self.start = start       # char offset of '#'
        self.end = end           # char offset after heading line
        self.tag = tag           # pattern tag: chinese_h1, decimal, …
        self.full_text = full_text  # original heading line (with numbering prefix)


class _Section:
    """A heading-bounded region of the document."""
    __slots__ = ("heading", "full_heading", "parent_headings",
                 "parent_full_headings", "level",
                 "text", "start", "end", "children", "total_tokens")
    def __init__(
        self,
        heading: str,
        parent_headings: list[str],
        level: int,
        text: str,
        start: int,
        end: int,
        full_heading: str = "",
        parent_full_headings: list[str] | None = None,
    ) -> None:
        self.heading = heading
        self.full_heading = full_heading  # original heading line with prefix
        self.parent_headings = list(parent_headings)
        self.parent_full_headings = list(parent_full_headings) if parent_full_headings else []
        self.level = level
        self.text = text          # all content under this heading
        self.start = start
        self.end = end
        self.children: list[_Section] = []
        self.total_tokens: int = 0  # computed later


# ======================================================================
# Heading parsing
# ======================================================================


def _parse_headings(content: str) -> list[_Heading]:
    """Extract headings using the multi-stage HeadingDetector."""
    lines = content.split("\n")
    offsets: list[int] = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    candidates = HeadingDetector.build(lines, char_offsets=offsets)
    return [
        _Heading(c.level, c.text, c.char_offset, c.char_offset + len(c.full_text),
                 tag=c.pattern_tag, full_text=c.full_text)
        for c in candidates
    ]


def _build_section_tree(
    content: str, headings: list[_Heading]
) -> list[_Section]:
    """Build a tree of _Section nodes from a flat heading list.

    Returns the top-level sections (all H1, or synthesized for shallowest).
    For each heading, all content up to the next heading at ≤ level is its
    body text.  Deeper headings become children.
    """
    if not headings:
        return []

    # Leading text before first heading (level-0 root).
    if headings[0].start > 0:
        leading = content[: headings[0].start].strip()
        if leading:
            root = _Section("", [], 0, leading, 0, headings[0].start)
            # attach H1 children later
            # We'll handle this via the stack
            pass

    # Build with a stack so each section knows its parent chain.
    # Stack holds (section, min_level_for_exit) pairs.
    root_children: list[_Section] = []
    stack: list[tuple[_Section, list[_Heading]]] = []  # (section, all_ancestors)

    for i, h in enumerate(headings):
        # Build parent chain: all ancestors with shallower level.
        ancestors: list[_Heading] = []
        for prev in headings[:i]:
            if prev.level < h.level:
                # Keep only the shallowest at each branch.
                ancestors = [a for a in ancestors if a.level < prev.level]
                ancestors.append(prev)

        parent_headings = [a.text for a in ancestors]
        parent_full_headings = [a.full_text for a in ancestors]

        # Section body: from this heading line → next heading at ≤ h.level.
        body_start = h.end + 1
        body_end = len(content)
        for j in range(i + 1, len(headings)):
            if headings[j].level <= h.level:
                body_end = headings[j].start
                break
        body_text = content[body_start:body_end].strip()

        sec = _Section(
            heading=h.text,
            parent_headings=parent_headings,
            level=h.level,
            text=body_text,
            start=h.start,
            end=body_end,
            full_heading=h.full_text,
            parent_full_headings=parent_full_headings,
        )

        # Attach to tree: find parent (last ancestor at level < h.level).
        parent_sec = None
        for a in reversed(ancestors):
            # Find the _Section object for this ancestor
            for s in _flatten_sections(root_children):
                if s.heading == a.text and s.level == a.level:
                    parent_sec = s
                    break
            if parent_sec:
                break

        if parent_sec:
            parent_sec.children.append(sec)
        else:
            root_children.append(sec)

    return root_children


def _flatten_sections(roots: list[_Section]):
    """Yield all sections DFS."""
    for r in roots:
        yield r
        yield from _flatten_sections(r.children)


# ======================================================================
# Token counting
# ======================================================================


def _count_tokens(tokenizer: Tokenizer, text: str) -> int:
    if not text:
        return 0
    return len(tokenizer.encode(text))


def _compute_total_tokens(
    tokenizer: Tokenizer, node: _Section
) -> int:
    """Total tokens for *node* = body text tokens (children are *inside* body
    text already, so we just count the text)."""
    node.total_tokens = _count_tokens(tokenizer, node.text)
    for child in node.children:
        _compute_total_tokens(tokenizer, child)
    return node.total_tokens


# ======================================================================
# DFS Tree Split
# ======================================================================


def _dfs_tree_split(
    node: _Section,
    tokenizer: Tokenizer,
    max_tokens: int,
    parent_section_id: str,
) -> list[dict[str, Any]]:
    """Core recursive split.

    Returns a flat list of chunk dicts for *node* and all descendants.
    """
    token_count = _count_tokens(tokenizer, node.text)

    # ---------- Case 1: fits → one chunk -----------------------------
    if token_count <= max_tokens:
        ch = _make_chunk(node, node.text, split_type="section",
                         section_id=node.heading or "",
                         parent_section_id=parent_section_id)
        ch["tokens"] = token_count
        return [ch]

    # ---------- Case 2: oversized, has children → split by children ---
    if node.children:
        chunks: list[dict[str, Any]] = []

        # 2a. Extract leading text (before first child heading).
        first_child = node.children[0]
        lead_end = _find_child_offset_in_parent(node.text, first_child.full_heading or first_child.heading)
        lead_text = node.text[:lead_end].strip()
        if lead_text:
            lead_tokens = _count_tokens(tokenizer, lead_text)
            if lead_tokens <= max_tokens:
                ch = _make_chunk(node, lead_text,
                                 split_type="section_lead",
                                 section_id=node.heading,
                                 parent_section_id=parent_section_id)
                ch["tokens"] = lead_tokens
                chunks.append(ch)
            else:
                # Leading text itself too large → paragraph fallback.
                fallback = _leaf_fallback_split(
                    tokenizer, lead_text, max_tokens,
                    section_id=node.heading,
                    parent_section_id=parent_section_id,
                    parent_headings=list(node.parent_full_headings or node.parent_headings),
                    level=node.level,
                    full_section_id=node.full_heading or "",
                )
                chunks.extend(fallback)

        # 2b. Recurse into each child.
        for child in node.children:
            child_chunks = _dfs_tree_split(
                child, tokenizer, max_tokens,
                parent_section_id=node.heading,
            )
            chunks.extend(child_chunks)

        return chunks

    # ---------- Case 3: oversized leaf → fallback ---------------------
    return _leaf_fallback_split(
        tokenizer, node.text, max_tokens,
        section_id=node.heading,
        parent_section_id=parent_section_id,
        parent_headings=list(node.parent_full_headings or node.parent_headings),
        level=node.level,
        full_section_id=node.full_heading or "",
    )


def _find_child_offset_in_parent(parent_text: str, child_full_heading: str) -> int:
    """Find byte offset where *child_full_heading* starts inside *parent_text*.

    *child_full_heading* is the original heading line with its numbering
    prefix (e.g. ``### EQE测试``, ``（一）材料合成``, ``1. 引言``).  We
    search for it as a line-anchored match inside the parent's body text.

    Returns 0 when the heading cannot be located, meaning leading text is
    empty — safe fallback for documents whose first child heading sits
    right at the start of the parent section.
    """
    if not child_full_heading:
        return 0
    # Pass 1: Markdown — ``# Child Heading`` at line start
    md = re.compile(
        r"^#{1,6}\s+" + re.escape(child_full_heading) + r"\s*$",
        re.MULTILINE,
    )
    m = md.search(parent_text)
    if m:
        return m.start()
    # Pass 2: Plain-text — the full heading line as-is
    plain = re.compile(
        r"^" + re.escape(child_full_heading) + r"\s*$",
        re.MULTILINE,
    )
    m = plain.search(parent_text)
    if m:
        return m.start()
    return 0


# ======================================================================
# Leaf fallback (paragraph → sentence)
# ======================================================================


def _leaf_fallback_split(
    tokenizer: Tokenizer,
    text: str,
    max_tokens: int,
    *,
    section_id: str,
    parent_section_id: str,
    parent_headings: list[str] | None = None,
    level: int = 0,
    full_section_id: str = "",
) -> list[dict[str, Any]]:
    """Split an oversized leaf section by paragraph, then sentence.

    Paragraphs that fit within *max_tokens* are kept as-is.  A paragraph
    that is still too large is further split into sentences; adjacent
    sentences are greedily merged to fill the token budget.
    """
    _parent_headings = list(parent_headings) if parent_headings else []
    chunks: list[dict[str, Any]] = []

    paragraphs = _split_by_paragraph(text)
    pending_figure = ""  # Buffer figure-only paragraphs at the start

    for para in paragraphs:
        # Figure / drawing / caption paragraph: merge into adjacent chunk
        # instead of discarding — image resources may be loaded later by ID.
        if _is_figure_only(para):
            if chunks:
                # Merge into previous chunk
                prev = chunks[-1]
                prev["content"] = prev["content"] + "\n" + para
                prev["tokens"] = _count_tokens(tokenizer, prev["content"])
            else:
                # No previous chunk yet — buffer for the next real paragraph
                pending_figure = (pending_figure + "\n" + para).strip()
            continue

        # Prepend any pending figure/drawing content to this paragraph
        if pending_figure:
            para = pending_figure + "\n" + para
            pending_figure = ""

        p_tokens = _count_tokens(tokenizer, para)
        if p_tokens <= max_tokens:
            ch = _make_fallback_chunk(
                para, p_tokens, "paragraph",
                section_id, parent_section_id,
                parent_headings=_parent_headings, level=level,
                full_section_id=full_section_id,
            )
            chunks.append(ch)
        else:
            sentences = _split_by_sentence(para)
            # Greedy merge: combine adjacent sentences up to max_tokens
            buf_text = ""
            buf_tokens = 0
            for sent in sentences:
                s_tokens = _count_tokens(tokenizer, sent)
                if buf_tokens + s_tokens <= max_tokens:
                    buf_text += sent
                    buf_tokens = _count_tokens(tokenizer, buf_text)
                else:
                    if buf_text:
                        ch = _make_fallback_chunk(
                            buf_text, buf_tokens, "sentence",
                            section_id, parent_section_id,
                            parent_headings=_parent_headings, level=level,
                            full_section_id=full_section_id,
                        )
                        chunks.append(ch)
                    buf_text = sent
                    buf_tokens = s_tokens
            if buf_text:
                ch = _make_fallback_chunk(
                    buf_text, buf_tokens, "sentence",
                    section_id, parent_section_id,
                    parent_headings=_parent_headings, level=level,
                    full_section_id=full_section_id,
                )
                chunks.append(ch)

    # If the entire leaf section is figure-only (no real text paragraphs),
    # still preserve the content — don't discard it.
    if pending_figure and not chunks:
        p_tokens = _count_tokens(tokenizer, pending_figure)
        ch = _make_fallback_chunk(
            pending_figure, p_tokens, "paragraph",
            section_id, parent_section_id,
            parent_headings=_parent_headings, level=level,
            full_section_id=full_section_id,
        )
        chunks.append(ch)

    return chunks


def _make_fallback_chunk(
    text: str,
    tokens: int,
    split_type: str,
    section_id: str,
    parent_section_id: str,
    *,
    parent_headings: list[str] | None = None,
    level: int = 0,
    full_section_id: str = "",
) -> dict[str, Any]:
    _parent_headings = list(parent_headings) if parent_headings else []
    _heading_display = full_section_id or section_id
    breadcrumb = (
        HEADING_BREADCRUMB_SEP.join(
            _parent_headings + ([_heading_display] if _heading_display else [])
        )
        if _heading_display
        else ""
    )
    ch = {
        "content": text.strip(),
        "tokens": tokens,
        "chunk_order_index": 0,
        "heading": section_id,
        "parent_headings": _parent_headings,
        "level": level,
        "heading_breadcrumb": breadcrumb,
        "split_type": split_type,
        "section_id": section_id,
        "parent_section_id": parent_section_id,
    }
    return ch


# ======================================================================
# Chunk factory
# ======================================================================


def _make_chunk(
    sec: _Section,
    text: str,
    *,
    split_type: str,
    section_id: str,
    parent_section_id: str,
) -> dict[str, Any]:
    # Breadcrumb uses full heading text (with numbering prefix) so users see
    # e.g. "二、网电对抗态势 → （一）雷达对抗 → 1. 截获记录"
    # rather than stripped "网电对抗态势 → 雷达对抗 → 截获记录".
    parent_chain = sec.parent_full_headings if sec.parent_full_headings else sec.parent_headings
    heading_display = sec.full_heading or sec.heading
    breadcrumb = (
        HEADING_BREADCRUMB_SEP.join(
            parent_chain + ([heading_display] if heading_display else [])
        )
        if heading_display
        else ""
    )
    return {
        "content": text.strip(),
        "tokens": 0,              # filled by caller
        "chunk_order_index": 0,   # assigned later
        "heading": sec.heading,
        "parent_headings": list(sec.parent_headings),
        "level": sec.level,
        "heading_breadcrumb": breadcrumb,
        "split_type": split_type,
        "section_id": section_id,
        "parent_section_id": parent_section_id,
    }


def _assign_order(chunks: list[dict[str, Any]]) -> None:
    for idx, ch in enumerate(chunks):
        ch["chunk_order_index"] = idx


# ======================================================================
# Paragraph / sentence splitters (leaf fallback only)
# ======================================================================


def _split_by_paragraph(text: str) -> list[str]:
    """Split *text* into paragraphs.

    Normalises all common line endings (``\\r\\n`` Windows, ``\\r`` old
    Mac) to ``\\n`` first, then tries blank-line separation followed by
    single-newline separation.
    """
    # Normalise line endings: \r\n (Windows), \r (old Mac) → \n
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    # Pass 1: blank-line separation (standard Markdown)
    parts = re.split(r"\n\s*\n", normalized)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return parts
    # Pass 2: single-newline separation (plain-text / docx export)
    if "\n" in normalized:
        lines = [ln.strip() for ln in normalized.split("\n") if ln.strip()]
        if len(lines) > 1:
            return lines
    return [text]


def _is_figure_only(text: str) -> bool:
    """Return True if *text* contains only figure/drawing references — no
    retrievable text content."""
    lines = text.strip().split("\n")
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # XML tag line: <drawing .../>, <img ...>, <table ...>
        if re.match(r"^<(drawing|img|image|table)\b", s, re.IGNORECASE):
            continue
        # Figure/table caption line
        if re.match(r"^(图\s*\d|表\s*\d|Fig\.?\s*\d|Figure\s+\d|Table\s+\d)",
                    s, re.IGNORECASE):
            continue
        # Has real content → not figure-only
        return False
    return True  # all lines are figure lines or empty


def _split_by_sentence(text: str) -> list[str]:
    # CJK: split on punctuation without requiring whitespace.
    cjk_pat = re.compile(r"(?<=[。！？])(?=[^。！？\s])")
    parts = cjk_pat.split(text)
    if len(parts) > 1:
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) > 1:
            return parts
    # Western: punctuation + whitespace + capital.
    western_pat = re.compile(r"(?<=[.!?])\s+(?=[A-Z一-鿿])")
    parts = western_pat.split(text)
    return [p.strip() for p in parts if p.strip()] or [text]
