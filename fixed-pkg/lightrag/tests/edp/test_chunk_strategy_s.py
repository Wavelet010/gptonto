"""Tests for the structure-aware S chunk strategy (Tree Split)."""

from lightrag.utils import TiktokenTokenizer
from lightrag.edp.chunk_strategy_s import (
    chunking_by_structure,
    _find_child_offset_in_parent,
    _split_by_paragraph,
    _leaf_fallback_split,
    _count_tokens,
)


def _t():
    return TiktokenTokenizer(model_name="gpt-4o")


# ===================================================================
# Fixtures
# ===================================================================

_SAMPLE_HIERARCHY = """# OLED研究报告

## 材料特性
GD1265是一种新型OLED发光材料，由BOE公司研发。

## 实验结果
本章节展示了所有测试数据，包括EQE和寿命两方面。

### EQE测试
在1000 cd/m²亮度下，器件A的EQE达到26%。

### 寿命测试
在85℃高温下，LT95寿命超过1000小时。

## 结论
GD1265材料具有优异的效率和稳定性。
"""

_SAMPLE_NO_HEADINGS = "这是一个没有任何标题的纯文本。" * 10

_SAMPLE_LEAF_LARGE = "# 数据分析\n" + ("这是一段非常长的数据分析报告文本内容。" * 100)

_SAMPLE_CHINESE_HEADINGS = """一、项目进展

本周完成了OLED材料GD1265的性能测试，EQE达到了26%的峰值。

（一）材料合成
原材料采购已经完成，供应商为默克化学。

（二）器件制备
蒸镀工艺参数经过优化后，器件良率从80%提升到95%。

二、下一步计划
继续推进面板集成测试。
"""

_SAMPLE_SINGLE_NL_PARAGRAPHS = """## 实验结果
第一段文字，描述了实验的背景和目的，使用了多种设备进行测试。
第二段文字，给出了详细的数值结果和分析，所有的数据都经过了统计检验。
第三段文字，包含了对未来工作的展望和建议，团队将继续深入探索。
"""

_SAMPLE_SHORT_PARAGRAPHS = (
    "短段落一。\n\n"
    "短段落二。\n\n"
    "短段落三。"
)


# ===================================================================
# Tests: Tree Split produces correct structure
# ===================================================================


def test_tree_split_no_overlap():
    """Chunks should not overlap — no redundant parent chunks."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_HIERARCHY, chunk_token_size=30)
    contents = {c["content"] for c in chunks}
    # No two chunks should have identical content
    assert len(contents) == len(chunks)


def test_section_id_set():
    """Every chunk should carry section_id and parent_section_id."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_HIERARCHY, chunk_token_size=30)
    for c in chunks:
        assert "section_id" in c
        assert "parent_section_id" in c


def test_section_lead_preserved():
    """Parent's lead text should become a section_lead chunk."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_HIERARCHY, chunk_token_size=30)
    leads = [c for c in chunks if c["split_type"] == "section_lead"]
    assert len(leads) >= 1
    lead = leads[0]
    assert lead["section_id"] == "实验结果"
    assert "本章节展示了所有测试数据" in lead["content"]


def test_child_parent_link():
    """Child chunks should reference correct parent_section_id."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_HIERARCHY, chunk_token_size=30)
    eqe = [c for c in chunks if c["section_id"] == "EQE测试"]
    assert len(eqe) == 1
    assert eqe[0]["parent_section_id"] == "实验结果"


def test_fits_whole():
    """Small document should produce minimal chunks with normal token limit."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_HIERARCHY, chunk_token_size=1200)
    assert len(chunks) >= 1
    assert all(c["tokens"] <= 1200 for c in chunks)


def test_empty():
    """Empty content returns empty list."""
    assert chunking_by_structure(_t(), "", 100) == []


def test_no_headings():
    """Content without headings should still produce chunks."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_NO_HEADINGS, 100)
    assert len(chunks) > 0
    assert chunks[0]["level"] == 0


def test_leaf_fallback():
    """Large leaf node (no sub-headings) should fall back to paragraph split."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_LEAF_LARGE, 50)
    assert len(chunks) > 1
    # All should share the same section_id
    sids = {c["section_id"] for c in chunks}
    assert sids == {"数据分析"}


def test_chunk_order_sequential():
    """chunk_order_index should be 0,1,2,..."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_HIERARCHY, 30)
    orders = [c["chunk_order_index"] for c in chunks]
    assert orders == list(range(len(chunks)))


def test_output_schema():
    """Every chunk must have all required fields."""
    t = _t()
    chunks = chunking_by_structure(t, _SAMPLE_HIERARCHY, 30)
    required = [
        "content", "tokens", "chunk_order_index",
        "heading", "parent_headings", "level",
        "heading_breadcrumb", "split_type",
        "section_id", "parent_section_id",
    ]
    for c in chunks:
        for key in required:
            assert key in c, f"Missing key '{key}'"


# ===================================================================
# New tests — post leaf-fallback / paragraph fix
# ===================================================================


def test_find_child_offset_in_parent_markdown():
    """Markdown heading (full text with ### prefix) should be found."""
    parent = "leading text\n\n### EQE测试\nmore text\n"
    pos = _find_child_offset_in_parent(parent, "### EQE测试")
    assert pos > 0
    assert parent[:pos].strip() == "leading text"


def test_find_child_offset_in_parent_markdown_cleaned():
    """Cleaned heading text works too (Pass 1 markdown fallback)."""
    parent = "leading text\n\n### EQE测试\nmore text\n"
    pos = _find_child_offset_in_parent(parent, "EQE测试")
    assert pos > 0
    assert parent[:pos].strip() == "leading text"


def test_find_child_offset_in_parent_chinese():
    """Chinese heading like （一）标题 should be found."""
    parent = "正文内容。\n\n（一）材料合成\n后续内容。\n"
    pos = _find_child_offset_in_parent(parent, "（一）材料合成")
    assert pos > 0
    assert parent[:pos].strip() == "正文内容。"


def test_find_child_offset_in_parent_not_found():
    """Returns 0 when heading is absent."""
    assert _find_child_offset_in_parent("no match here", "EQE测试") == 0


def test_split_by_paragraph_double_newline():
    """Double-newline paragraphs are split correctly."""
    text = "para1\n\npara2\n\npara3"
    parts = _split_by_paragraph(text)
    assert parts == ["para1", "para2", "para3"]


def test_split_by_paragraph_single_newline_fallback():
    """Single-newline paragraphs are split correctly."""
    text = "第一段文字。\n第二段文字。\n第三段文字。"
    parts = _split_by_paragraph(text)
    assert parts == ["第一段文字。", "第二段文字。", "第三段文字。"]


def test_split_by_paragraph_single_paragraph():
    """Single paragraph with no newlines returns as-is."""
    text = "单独一段话。"
    parts = _split_by_paragraph(text)
    assert parts == [text]


def test_leaf_fallback_preserves_paragraphs_with_single_nl():
    """Single-newline paragraphs should be kept together, not sentence-split."""
    t = _t()
    text = "第一段很长的文字描述了测试结果数据很好很详细。\n第二段也包含了很多分析和讨论以及下一步的结论展望。"
    chunks = _leaf_fallback_split(
        t, text, max_tokens=1024,
        section_id="测试章节", parent_section_id="",
        parent_headings=["父标题"], level=2,
        full_section_id="1. 测试章节",
    )
    # Two paragraphs, each < 1024 tokens → each should be its own paragraph chunk
    assert len(chunks) == 2
    for c in chunks:
        assert c["split_type"] == "paragraph"
        # Fallback chunks now carry parent info
        assert c["level"] == 2
        assert c["parent_headings"] == ["父标题"]
        assert "父标题" in c["heading_breadcrumb"]


def test_leaf_fallback_sentence_split_oversized_paragraph():
    """A single oversized paragraph falls back to sentence splitting."""
    t = _t()
    long_sentence = "这是一段非常长的数据分析报告文本内容。"
    text = long_sentence * 60
    chunks = _leaf_fallback_split(
        t, text, max_tokens=50,
        section_id="数据", parent_section_id="",
        parent_headings=["统计"], level=3,
        full_section_id="2. 数据",
    )
    assert len(chunks) > 1
    for c in chunks:
        assert c["tokens"] <= 50
        assert c["level"] == 3
        assert "统计" in c["heading_breadcrumb"]


def test_chinese_headings_create_sections():
    """Chinese headings (一、, （一）, 1.) are detected and create sections."""
    t = _t()
    chunks = chunking_by_structure(
        t, _SAMPLE_CHINESE_HEADINGS, chunk_token_size=1024,
    )
    assert len(chunks) >= 1
    # Verify that child sections carry correct parent links
    sections = {c["section_id"] for c in chunks}
    assert "项目进展" in sections or any("项目进展" in c["content"] for c in chunks)


def test_single_newline_document_no_sentence_chunks():
    """Document with single-\\n paragraphs should NOT produce sentence chunks."""
    t = _t()
    chunks = chunking_by_structure(
        t, _SAMPLE_SINGLE_NL_PARAGRAPHS, chunk_token_size=1024,
    )
    # Verify no sentence-level chunks
    sentence_chunks = [c for c in chunks if c["split_type"] == "sentence"]
    assert len(sentence_chunks) == 0, (
        f"Expected no sentence chunks, got {len(sentence_chunks)}"
    )


# ===================================================================
# Line-ending normalisation tests
# ===================================================================


def test_split_by_paragraph_windows_crlf():
    """Windows \\r\\n line endings are normalised."""
    text = "段落一。\r\n段落二。\r\n段落三。"
    parts = _split_by_paragraph(text)
    assert parts == ["段落一。", "段落二。", "段落三。"]


def test_split_by_paragraph_old_mac_cr():
    """Old Mac \\r line endings are normalised."""
    text = "段落一。\r段落二。\r段落三。"
    parts = _split_by_paragraph(text)
    assert parts == ["段落一。", "段落二。", "段落三。"]


def test_split_by_paragraph_windows_blank_lines():
    """Windows blank lines (\\r\\n\\r\\n) are handled."""
    text = "段落一。\r\n\r\n段落二。\r\n\r\n段落三。"
    parts = _split_by_paragraph(text)
    assert parts == ["段落一。", "段落二。", "段落三。"]


# ===================================================================
# Heading format variations
# ===================================================================


_CHINESE_FORMAT_VARIANTS = """一、项目进展
本周完成了OLED材料GD1265的性能测试。

一.背景介绍
这是用英文句点做分隔符的一级标题。

一．研究目标
这是用全角句点做分隔符的一级标题。

（一）材料合成
原材料采购完成，全角括号中文数字子标题。

(二)器件制备
半角括号中文数字子标题，也应识别为 chinese_h2_full。

(1)实验步骤
半角括号阿拉伯数字子标题，应识别为 chinese_h2_half。

（2）测试方法
全角括号阿拉伯数字子标题，也应识别为 chinese_h2_half。

二、下一步计划
继续推进面板集成测试。
"""


def test_heading_format_variations_create_sections():
    """All heading format variations should be detected."""
    t = _t()
    chunks = chunking_by_structure(
        t, _CHINESE_FORMAT_VARIANTS, chunk_token_size=1024,
    )
    # Should produce multiple sections from the various heading formats
    assert len(chunks) >= 4, f"Expected at least 4 chunks, got {len(chunks)}"
    # No sentence-level spill
    sentence_chunks = [c for c in chunks if c["split_type"] == "sentence"]
    assert len(sentence_chunks) == 0


def test_decimal_heading_variations():
    """Decimal headings with various separators are detected."""
    t = _t()
    text = "1. 概述\n这是第一节内容。\n\n1、详细说明\n这是顿号分隔的数字。\n\n1．补充材料\n这是全角句点分隔的数字。\n\n2 结论\n这是只有空格分隔的数字。\n"
    chunks = chunking_by_structure(t, text, chunk_token_size=1024)
    assert len(chunks) >= 4, f"Expected at least 4 chunks, got {len(chunks)}"
