"""Tests for the EDP Metadata Filter expression parser."""

from lightrag.edp.metadata_filter import EDPMetadataFilter


def test_simple_equality():
    result = EDPMetadataFilter.parse('material=="GD1265"')
    assert "material" in result
    assert "GD1265" in result
    assert "==" in result


def test_compound_and():
    result = EDPMetadataFilter.parse('a=="x" && b=="y"')
    assert "and" in result
    assert 'a == "x"' in result
    assert 'b == "y"' in result


def test_compound_or():
    result = EDPMetadataFilter.parse('a=="x" || b=="y"')
    assert "or" in result


def test_numeric_comparison():
    result = EDPMetadataFilter.parse("count>=5")
    assert ">=" in result
    assert "5" in result


def test_empty_returns_empty():
    assert EDPMetadataFilter.parse("") == ""
    assert EDPMetadataFilter.parse(None) == ""
    assert EDPMetadataFilter.parse("   ") == ""


def test_chinese_values():
    result = EDPMetadataFilter.parse('knowledge_type=="实验结果"')
    assert "实验结果" in result


def test_validate():
    assert EDPMetadataFilter.validate('a=="x"') is True
    assert EDPMetadataFilter.validate("") is False
    assert EDPMetadataFilter.validate(None) is False
