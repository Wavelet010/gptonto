"""Tests for the EDP Context Builder."""

from lightrag.edp.context_builder import build_embedding_text, enrich_chunks_with_context
from lightrag.edp.models import ContextBuilderConfig


def test_build_embedding_text_full():
    chunk = {"content": "EQE达到26%", "heading_breadcrumb": "实验结果 → EQE测试"}
    config = ContextBuilderConfig(sep=" | ", enabled=True,
                                  include_doc_title=True, include_heading_path=True)
    result = build_embedding_text(chunk, doc_title="GD1265研究报告", config=config)
    assert "GD1265研究报告" in result
    assert "实验结果 → EQE测试" in result
    assert "EQE达到26%" in result
    assert result == "GD1265研究报告 | 实验结果 → EQE测试 | EQE达到26%"


def test_build_embedding_text_no_title():
    chunk = {"content": "文本", "heading_breadcrumb": "章节"}
    config = ContextBuilderConfig(include_doc_title=False, include_heading_path=True)
    result = build_embedding_text(chunk, doc_title="", config=config)
    assert "章节" in result
    assert "文本" in result


def test_build_embedding_text_disabled():
    chunk = {"content": "文本", "heading_breadcrumb": "章节"}
    config = ContextBuilderConfig(enabled=False)
    result = build_embedding_text(chunk, doc_title="标题", config=config)
    assert result == "文本"


def test_enrich_chunks_sets_both_fields():
    chunks = [{"content": "chunk1"}, {"content": "chunk2"}]
    config = ContextBuilderConfig(sep=" | ")
    enriched = enrich_chunks_with_context(chunks, doc_title="Doc", config=config)
    for c in enriched:
        assert "embedding_text" in c
        assert "chunk_text" in c
        assert c["chunk_text"] == c["content"]


def test_chunk_text_preserves_original():
    chunks = [{"content": "原始内容"}]
    enriched = enrich_chunks_with_context(chunks, doc_title="Doc")
    assert enriched[0]["chunk_text"] == "原始内容"
    # embedding_text should be different (enriched)
    assert enriched[0]["embedding_text"] != "原始内容"
