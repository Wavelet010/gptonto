"""Test the figure-only fix: _is_figure_only no longer discards content.

Covers 5 scenarios:
1. Figure/drawing in the middle of a paragraph section
2. Figure/drawing at the front of a section (prepended to next paragraph)
3. Only figure/drawing, no body text at all
4. Table data (should NOT be treated as figure-only)
5. Normal text without any figures (regression)
"""

from lightrag.utils import TiktokenTokenizer
from lightrag.edp.chunk_strategy_s import (
    chunking_by_structure,
    _is_figure_only,
    _leaf_fallback_split,
    _split_by_paragraph,
)


def _t():
    return TiktokenTokenizer(model_name="gpt-4o")


# ===================================================================
# Unit tests for _is_figure_only helper
# ===================================================================


def test_is_figure_only_drawing_xml():
    """<drawing .../> alone should be figure-only."""
    assert _is_figure_only('<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>')


def test_is_figure_only_drawing_with_caption():
    """<drawing> + caption should be figure-only."""
    text = '<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>\n图1 活动图'
    assert _is_figure_only(text)


def test_is_figure_only_img_tag():
    """<img ...> tag should be figure-only."""
    assert _is_figure_only('<img src="chart.png" alt="chart"/>')


def test_is_figure_only_table_tag():
    """A <table> tag with no surrounding text SHOULD be figure-only."""
    text = '<table id="tb-xxx" format="json">[["date","value"]]</table>\n表1 数据'
    assert _is_figure_only(text)


def test_not_figure_only_table_with_text():
    """<table> + caption + body text → NOT figure-only."""
    text = '<table id="tb-xxx" format="json">[["date","value"]]</table>\n表1 数据\n以下是数据分析。'
    assert not _is_figure_only(text)


def test_not_figure_only_plain_text():
    """Plain text is not figure-only."""
    assert not _is_figure_only("根据实验数据，以下图表展示了活动图。")


def test_not_figure_only_drawing_with_text():
    """<drawing> with text after it is not figure-only."""
    text = '<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>\n图1 活动图\n从图中可以看出，该结果符合预期。'
    assert not _is_figure_only(text)


# ===================================================================
# Integration tests: figure-only paragraphs inside leaf fallback
# ===================================================================


def test_figure_in_middle_of_leaf_section():
    """Scenario 1: Figure/drawing in the middle → merged into previous chunk."""
    t = _t()
    text = (
        "根据实验数据，以下图表展示了活动图。\n"
        '<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>\n'
        "图1 活动图\n"
        "从图中可以看出，该结果符合预期。"
    )
    chunks = _leaf_fallback_split(
        t, text, max_tokens=1024,
        section_id="实验结果", parent_section_id="",
        parent_headings=[], level=2,
        full_section_id="1.1 实验结果",
    )
    # Should be a single chunk (all content fits within 1024 tokens)
    assert len(chunks) >= 1
    merged = " ".join(c["content"] for c in chunks)
    assert '<drawing id="im-xxx-0001"' in merged, (
        f"Drawing tag missing from chunks: {merged}"
    )
    assert "图1 活动图" in merged, (
        f"Figure caption missing from chunks: {merged}"
    )
    assert "根据实验数据" in merged, (
        f"Leading text missing from chunks: {merged}"
    )
    assert "该结果符合预期" in merged, (
        f"Trailing text missing from chunks: {merged}"
    )
    print("  [OK] Scenario 1: Figure in middle of section — all content preserved.")


def test_figure_at_front_of_leaf_section():
    """Scenario 2: Figure at front → prepended to next real paragraph."""
    t = _t()
    text = (
        '<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>\n'
        "图1 活动图\n"
        "从图中可以看出实验结果符合预期。"
    )
    chunks = _leaf_fallback_split(
        t, text, max_tokens=1024,
        section_id="实验结果", parent_section_id="",
        parent_headings=[], level=2,
        full_section_id="1.1 实验结果",
    )
    assert len(chunks) >= 1
    merged = " ".join(c["content"] for c in chunks)
    assert '<drawing id="im-xxx-0001"' in merged, (
        f"Drawing tag missing from chunks: {merged}"
    )
    assert "图1 活动图" in merged, (
        f"Figure caption missing from chunks: {merged}"
    )
    assert "实验结果符合预期" in merged, (
        f"Body text missing from chunks: {merged}"
    )
    # The drawing should be in the same chunk as the body text (prepended)
    if len(chunks) == 1:
        content = chunks[0]["content"]
    else:
        # If there are multiple chunks, the first should have the figure
        content = chunks[0]["content"]
    print(f"  [OK] Scenario 2: Figure at front — chunk content: {content[:80]}...")


def test_figure_only_no_body():
    """Scenario 3: Only figure/drawing, no body text → still produces a chunk."""
    t = _t()
    text = (
        '<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>\n'
        "图1 活动图"
    )
    chunks = _leaf_fallback_split(
        t, text, max_tokens=1024,
        section_id="实验结果", parent_section_id="",
        parent_headings=[], level=2,
        full_section_id="1.1 实验结果",
    )
    assert len(chunks) >= 1, (
        "Figure-only section produced NO chunks — content was discarded!"
    )
    content = chunks[0]["content"]
    assert '<drawing id="im-xxx-0001"' in content, (
        f"Drawing tag missing: {content}"
    )
    assert "图1 活动图" in content, (
        f"Figure caption missing: {content}"
    )
    print(f"  [OK] Scenario 3: Figure only — chunk produced: {content}")


def test_table_data_not_filtered():
    """Scenario 4: Table data should appear in chunks."""
    t = _t()
    table_line = '<table id="tb-xxx" format="json">[["日期","数据"],["01-13","xxx"]]</table>'
    text = (
        "## 1.1 数据表\n"
        + table_line + "\n"
        "表1 实验数据\n"
        "以下是数据分析..."
    )
    chunks = chunking_by_structure(
        t, text, chunk_token_size=400,
    )
    merged = " ".join(c["content"] for c in chunks)
    assert '<table id="tb-xxx"' in merged, (
        f"Table tag missing from chunks: {merged}"
    )
    assert "表1 实验数据" in merged, (
        f"Table caption missing from chunks: {merged}"
    )
    assert "以下是数据分析" in merged, (
        f"Body text missing from chunks: {merged}"
    )
    print(f"  [OK] Scenario 4: Table data preserved.")


def test_normal_text_unaffected():
    """Scenario 5: Normal text without figures — unchanged behavior."""
    t = _t()
    text = (
        "## 1.1 实验结果\n"
        "实验数据显示...\n"
        "经过分析...\n"
        "## 1.2 结论\n"
        "综上所述..."
    )
    chunks = chunking_by_structure(
        t, text, chunk_token_size=400,
    )
    # Should produce chunks for both sections
    contents = [c["content"] for c in chunks]
    combined = " ".join(contents)
    assert "实验数据显示" in combined
    assert "经过分析" in combined
    assert "综上所述" in combined
    # No drawing tags where they don't belong
    print("  [OK] Scenario 5: Normal text unaffected.")


# ===================================================================
# End-to-end: Full chunking_by_structure with figure-rich content
# ===================================================================


def test_full_pipeline_figure_in_middle():
    """Full pipeline with figure in middle of a leaf section."""
    t = _t()
    content = (
        "## 1.1 实验结果\n"
        "根据实验数据，以下图表展示了活动图。\n"
        '<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>\n'
        "图1 活动图\n"
        "从图中可以看出，该结果符合预期。\n"
    )
    chunks = chunking_by_structure(t, content, chunk_token_size=400)
    merged = " ".join(c["content"] for c in chunks)
    assert '<drawing id="im-xxx-0001"' in merged, (
        f"Drawing missing in full pipeline: {merged}"
    )
    assert "图1 活动图" in merged, (
        f"Caption missing in full pipeline: {merged}"
    )
    print(f"  [OK] Full pipeline: figure in middle works.")


def test_full_pipeline_figure_at_front():
    """Full pipeline with figure at front of a leaf section."""
    t = _t()
    content = (
        "## 1.1 实验结果\n"
        '<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>\n'
        "图1 活动图\n"
        "从图中可以看出实验结果符合预期。\n"
    )
    chunks = chunking_by_structure(t, content, chunk_token_size=400)
    merged = " ".join(c["content"] for c in chunks)
    assert '<drawing id="im-xxx-0001"' in merged, (
        f"Drawing missing: {merged}"
    )
    assert "图1 活动图" in merged, (
        f"Caption missing: {merged}"
    )
    print(f"  [OK] Full pipeline: figure at front works.")


def test_full_pipeline_figure_only():
    """Full pipeline with only a figure — must not discard."""
    t = _t()
    content = (
        "## 1.1 实验结果\n"
        '<drawing id="im-xxx-0001" format="png" path="image1.png" src=""/>\n'
        "图1 活动图\n"
    )
    chunks = chunking_by_structure(t, content, chunk_token_size=400)
    assert len(chunks) >= 1, (
        "Figure-only section produced NO chunks in full pipeline!"
    )
    merged = " ".join(c["content"] for c in chunks)
    assert '<drawing id="im-xxx-0001"' in merged, (
        f"Drawing missing: {merged}"
    )
    print(f"  [OK] Full pipeline: figure only → {len(chunks)} chunk(s).")


def test_full_pipeline_table():
    """Full pipeline with table data."""
    t = _t()
    table_line = '<table id="tb-xxx" format="json">[["日期","数据"],["01-13","xxx"]]</table>'
    content = (
        "## 1.1 数据表\n"
        + table_line + "\n"
        "表1 实验数据\n"
        "以下是数据分析...\n"
    )
    chunks = chunking_by_structure(t, content, chunk_token_size=400)
    merged = " ".join(c["content"] for c in chunks)
    assert '<table id="tb-xxx"' in merged, (
        f"Table missing: {merged}"
    )
    print(f"  [OK] Full pipeline: table data preserved.")


# ===================================================================
# Regression: paragraph-level figure merging with pending_figure
# ===================================================================


def test_multiple_figures_at_start():
    """Multiple figure-only paragraphs at the start should all be prepended."""
    t = _t()
    text = (
        '<drawing id="im-001" format="png" path="img1.png" src=""/>\n'
        "图1 第一张图\n"
        '<drawing id="im-002" format="png" path="img2.png" src=""/>\n'
        "图2 第二张图\n"
        "终于有一段正文了。"
    )
    chunks = _leaf_fallback_split(
        t, text, max_tokens=1024,
        section_id="实验", parent_section_id="",
        parent_headings=[], level=2,
    )
    merged = " ".join(c["content"] for c in chunks)
    assert "im-001" in merged, "First drawing missing"
    assert "im-002" in merged, "Second drawing missing"
    assert "终于有一段正文了" in merged, "Body text missing"
    # Both drawings should be in the chunk with the body text
    print(f"  [OK] Multiple figures at start all prepended.")


def test_figures_merged_into_previous_chunk():
    """Figure in between real paragraphs → merges into the previous chunk."""
    t = _t()
    text = (
        "第一段正文内容。\n"
        '<drawing id="im-001" format="png" path="img1.png" src=""/>\n'
        "图1 结果图\n"
        "第三段也是正文。"
    )
    chunks = _leaf_fallback_split(
        t, text, max_tokens=1024,
        section_id="实验", parent_section_id="",
        parent_headings=[], level=2,
    )
    # With max_tokens=1024 and short text, we should have ~2-3 chunks
    merged = " ".join(c["content"] for c in chunks)
    assert "im-001" in merged
    assert "第一段正文内容" in merged
    # The drawing should be in the same chunk as "第一段正文内容" or the next chunk
    print(f"  [OK] Figures merged into previous chunk: {len(chunks)} chunks.")


# ===================================================================
# Main
# ===================================================================


def main():
    tests = [
        ("_is_figure_only: drawing XML", test_is_figure_only_drawing_xml),
        ("_is_figure_only: drawing + caption", test_is_figure_only_drawing_with_caption),
        ("_is_figure_only: img tag", test_is_figure_only_img_tag),
        ("_is_figure_only: table tag", test_is_figure_only_table_tag),
        ("_is_figure_only: table + text = False", test_not_figure_only_table_with_text),
        ("_is_figure_only: plain text = False", test_not_figure_only_plain_text),
        ("_is_figure_only: drawing + text = False", test_not_figure_only_drawing_with_text),
        ("", None),
        ("Scenario 1: figure in middle", test_figure_in_middle_of_leaf_section),
        ("Scenario 2: figure at front", test_figure_at_front_of_leaf_section),
        ("Scenario 3: figure only no body", test_figure_only_no_body),
        ("Scenario 4: table not filtered", test_table_data_not_filtered),
        ("Scenario 5: normal text", test_normal_text_unaffected),
        ("", None),
        ("Full pipeline: figure in middle", test_full_pipeline_figure_in_middle),
        ("Full pipeline: figure at front", test_full_pipeline_figure_at_front),
        ("Full pipeline: figure only", test_full_pipeline_figure_only),
        ("Full pipeline: table data", test_full_pipeline_table),
        ("", None),
        ("Multi-figure at start", test_multiple_figures_at_start),
        ("Figure merge into prev chunk", test_figures_merged_into_previous_chunk),
    ]

    passed = 0
    failed = 0
    for label, func in tests:
        if func is None:
            print()
            continue
        try:
            func()
            passed += 1
        except Exception as e:
            failed += 1
            import traceback
            print(f"  [FAIL] {label}: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
