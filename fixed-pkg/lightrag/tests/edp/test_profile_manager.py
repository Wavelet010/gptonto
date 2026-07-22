"""Tests for the EDP Profile Manager."""

import tempfile
from pathlib import Path

from lightrag.edp.profile_manager import ProfileManager
from lightrag.edp.models import ParserProfile


def test_list_profiles():
    pm = ProfileManager()
    profiles = pm.list_profiles()
    assert "oled_research_v1" in profiles


def test_get_profile():
    pm = ProfileManager()
    profile = pm.get_profile("oled_research_v1")
    assert isinstance(profile, ParserProfile)
    assert profile.profile_name == "oled_research_v1"
    assert profile.chunk_token_size == 1200
    # context builder config
    assert profile.context_builder.enabled is True
    assert profile.context_builder.include_doc_title is True
    # metadata builder config
    doc_fields = profile.metadata_builder.document_fields
    assert len(doc_fields) == 3
    assert {f.name for f in doc_fields} == {"material", "customer", "project"}


def test_get_profile_cached():
    pm = ProfileManager()
    p1 = pm.get_profile("oled_research_v1")
    p2 = pm.get_profile("oled_research_v1")
    assert p1 is p2  # same instance from cache


def test_missing_profile():
    pm = ProfileManager()
    try:
        pm.get_profile("nonexistent_profile_xyz")
        assert False, "Should have raised"
    except FileNotFoundError:
        pass


def test_reload_clears_cache():
    pm = ProfileManager()
    p1 = pm.get_profile("oled_research_v1")
    pm.reload_profiles()
    p2 = pm.get_profile("oled_research_v1")
    assert p1 is not p2  # new instance after reload


def test_custom_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yml_path = Path(tmpdir) / "custom_test.yml"
        yml_path.write_text("""
profile_name: custom_test
chunk_token_size: 500
context_builder:
  enabled: true
metadata_builder:
  document_fields: []
  heading_fields: []
  llm_fields: []
  rule_fields: []
""")
        pm = ProfileManager(custom_dirs=[Path(tmpdir)])
        assert "custom_test" in pm.list_profiles()
        profile = pm.get_profile("custom_test")
        assert profile.chunk_token_size == 500
