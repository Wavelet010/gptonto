#!/usr/bin/env python3
"""Comprehensive test of the filename_fields metadata extraction feature."""

import sys
import os
import json
import tempfile
import traceback

# Ensure lightrag is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixed-pkg", "lightrag"))

from lightrag.edp.models import (
    FilenameRule,
    FilenameFieldConfig,
    MetadataBuilderConfig,
    MetadataFieldConfig,
    ContextBuilderConfig,
)
from lightrag.edp.metadata_builder import (
    _topo_sort_fields,
    _substitute_value,
    _extract_one_filename_field,
    MetadataBuilder,
)

# ====================================================================
# Test counters
# ====================================================================
passed = 0
failed = 0
errors = []


def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        msg = f"  FAIL: {label}"
        if detail:
            msg += f" -- {detail}"
        print(msg)
        errors.append((label, detail))


def req(label: str, expected: str, actual: str):
    """Rich equality check: show both values on mismatch."""
    ok = expected == actual
    check(label, ok, detail=f"expected {expected!r}, got {actual!r}" if not ok else "")


# ====================================================================
# A. Filename extraction logic
# ====================================================================

print("\n" + "=" * 70)
print("A. Filename extraction logic")
print("=" * 70)

# ---- A1. _topo_sort_fields normal ordering ----
print("\n--- A1: _topo_sort_fields with normal ordering ---")
fields_norm = [
    FilenameFieldConfig(name="a", rules=[]),
    FilenameFieldConfig(name="b", depends_on="a", rules=[]),
    FilenameFieldConfig(name="c", depends_on="b", rules=[]),
]
ordered = _topo_sort_fields(fields_norm)
names = [f.name for f in ordered]
check("Normal ordering: a before b before c", names == ["a", "b", "c"],
      f"got {names}")

# ---- A2. _topo_sort_fields with depends_on before its dependency ----
print("\n--- A2: _topo_sort_fields with reverse-order depends_on ---")
fields_rev = [
    FilenameFieldConfig(name="c", depends_on="b", rules=[]),
    FilenameFieldConfig(name="a", rules=[]),
    FilenameFieldConfig(name="b", depends_on="a", rules=[]),
]
ordered_rev = _topo_sort_fields(fields_rev)
names_rev = [f.name for f in ordered_rev]
idx_a = names_rev.index("a")
idx_b = names_rev.index("b")
idx_c = names_rev.index("c")
check("Reverse: a before b", idx_a < idx_b, f"got {names_rev}")
check("Reverse: b before c", idx_b < idx_c, f"got {names_rev}")

# ---- A3. _topo_sort_fields with circular dependency ----
print("\n--- A3: _topo_sort_fields with circular dependency ---")
fields_circ = [
    FilenameFieldConfig(name="a", depends_on="b", rules=[]),
    FilenameFieldConfig(name="b", depends_on="a", rules=[]),
]
try:
    ordered_circ = _topo_sort_fields(fields_circ)
    # Should not crash; just returns remaining fields at end
    names_circ = [f.name for f in ordered_circ]
    check("Circular: no crash", True)
    check("Circular: both fields returned in some order",
          set(names_circ) == {"a", "b"},
          f"got {names_circ}")
except Exception as e:
    check("Circular: no crash", False, f"raised {type(e).__name__}: {e}")

# ---- A4. _substitute_value with {matched} ----
print("\n--- A4: _substitute_value with {matched} ---")
import re
m4 = re.search(r"([A-Z]+)-(\d+)", "ABC-123")
val4 = _substitute_value("{matched}", m4)
req("Substitute {matched}", "ABC-123", val4)

# ---- A5. _substitute_value with {1} ----
print("\n--- A5: _substitute_value with {1} ---")
val5 = _substitute_value("{1}", m4)
req("Substitute {1}", "ABC", val5)

# ---- A6. _substitute_value with literal string ----
print("\n--- A6: _substitute_value with literal ---")
val6 = _substitute_value("雷达", m4)
req("Substitute literal", "雷达", val6)

# ---- A7. _substitute_value with template containing both ----
print("\n--- A7: _substitute_value with template mixing placeholders ---")
val7 = _substitute_value("type={1},full={matched}", m4)
req("Substitute mixed template", "type=ABC,full=ABC-123", val7)

# ---- A8. _substitute_value with {1} and no capture group ----
print("\n--- A8: _substitute_value with {1} and no capture group ---")
m8 = re.search(r"\d+", "abc 123 def")
val8 = _substitute_value("{1}", m8)
# When no capture group 1, falls back to match.group(0) per code
req("Substitute {1} with no capture group", "123", val8)

# ---- A9. extract_filename_metadata main test ----
print("\n--- A9: extract_filename_metadata with Chinese filename ---")
mb_cfg = MetadataBuilderConfig(
    filename_fields=[
        FilenameFieldConfig(
            name="title",
            source="filename",
            auto_file_name=True,
        ),
        FilenameFieldConfig(
            name="doc_category",
            source="filename",
            rules=[
                FilenameRule(pattern="快报", value="快报", priority=10),
                FilenameRule(pattern="周报", value="周报", priority=10),
                FilenameRule(pattern="要报", value="要报", priority=10),
                FilenameRule(pattern="月报", value="月报", priority=10),
                FilenameRule(pattern="制度规范|规定|条例|案例", value="制度规范", priority=10),
            ],
        ),
        FilenameFieldConfig(
            name="entity_name",
            source="filename",
            rules=[
                FilenameRule(pattern=r"([A-Za-z0-9/-]+)雷达", value="{1}", priority=10),
                FilenameRule(pattern=r"E-2K|F20|F16", value="{matched}", priority=10),
            ],
        ),
        FilenameFieldConfig(
            name="entity_type",
            source="filename",
            depends_on="entity_name",
            # NOTE: depends_on sets the target to entity_name's extracted VALUE
            # ("AN/SPY-1D"), not the original filename.  Pattern "雷达" won't
            # match "AN/SPY-1D".  This is a CONFIG ISSUE in general.yml.
            rules=[
                FilenameRule(pattern="雷达", value="雷达", priority=10),
                FilenameRule(pattern="通信|话音", value="通信", priority=10),
            ],
        ),
    ],
)

mb = MetadataBuilder(mb_cfg)
result = mb.extract_filename_metadata("2026年第03周周报-黄东海AN/SPY-1D雷达")

print(f"  Result: {result!r}")
req("A9a: title = filename base",
   "2026年第03周周报-黄东海AN/SPY-1D雷达",
   result.get("title", ""))
req("A9b: doc_category = 周报", "周报", result.get("doc_category", ""))
req("A9c: entity_name = AN/SPY-1D", "AN/SPY-1D", result.get("entity_name", ""))
req("A9d: entity_type = '' (depends_on → target='AN/SPY-1D', '雷达' not in 'AN/SPY-1D')",
   "", result.get("entity_type", ""))

# Also test entity_type WITHOUT depends_on (against original filename)
mb_no_dep = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="entity_name",
                rules=[FilenameRule(pattern=r"([A-Za-z0-9/-]+)雷达", value="{1}", priority=10)],
            ),
            FilenameFieldConfig(
                name="entity_type",
                # NO depends_on: matches against original filename
                rules=[
                    FilenameRule(pattern="雷达", value="雷达", priority=10),
                ],
            ),
        ],
    ),
)
result_no_dep = mb_no_dep.extract_filename_metadata("2026年第03周周报-黄东海AN/SPY-1D雷达")
req("A9e: entity_type without depends_on = '雷达' (matches original filename)",
   "雷达", result_no_dep.get("entity_type", ""))

# ---- A10. empty filename ----
print("\n--- A10: extract_filename_metadata with empty filename ---")
result_empty = mb.extract_filename_metadata("")
req("Empty filename returns {}",
   "{}" if not result_empty else str(result_empty), "{}")
check("Empty filename returns empty dict", result_empty == {})

# ---- A11. invalid regex doesn't crash ----
print("\n--- A11: invalid regex (malformed pattern) ---")
mb_invalid_re = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="bad_field",
                rules=[FilenameRule(pattern=r"[invalid", value="{matched}", priority=10)],
            ),
        ],
    ),
)
try:
    result_bad = mb_invalid_re.extract_filename_metadata("testfile.txt")
    check("Invalid regex: no crash", True)
    check("Invalid regex: returns empty string", result_bad.get("bad_field", "") == "",
          f"got {result_bad!r}")
except Exception as e:
    check("Invalid regex: no crash", False, f"raised {type(e).__name__}: {e}")

# ---- A12. depends_on field not found -> empty value ----
print("\n--- A12: depends_on field not found ---")
mb_dep_missing = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="child",
                depends_on="nonexistent_parent",
                rules=[FilenameRule(pattern=".*", value="should_not_appear", priority=10)],
            ),
        ],
    ),
)
result_dep_missing = mb_dep_missing.extract_filename_metadata("somefile")
req("Depends-on missing: empty string", "",
   result_dep_missing.get("child", ""))

# ---- A13. auto_file_name and rules both present ----
print("\n--- A13: auto_file_name and rules both present ---")
# auto_file_name: true means the field should return filename_base directly,
# regardless of rules
mb_both = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="title",
                auto_file_name=True,
                rules=[FilenameRule(pattern=".*", value="RULES_WIN", priority=10)],
            ),
        ],
    ),
)
result_both = mb_both.extract_filename_metadata("mydocument")
req("auto_file_name wins over rules", "mydocument",
   result_both.get("title", ""))

# ---- A14. Multiple rules with same priority -> first wins ----
print("\n--- A14: same priority -> first match wins ---")
mb_same_prio = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="category",
                rules=[
                    FilenameRule(pattern="A", value="FIRST", priority=10),
                    FilenameRule(pattern="A", value="SECOND", priority=10),
                ],
            ),
        ],
    ),
)
result_prio = mb_same_prio.extract_filename_metadata("ABC")
req("Same priority: first match wins", "FIRST",
   result_prio.get("category", ""))

# ---- A15. Multiple rules with different priorities -> highest wins ----
print("\n--- A15: different priorities -> highest wins ---")
mb_diff_prio = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="category",
                rules=[
                    FilenameRule(pattern="周报", value="LOW_PRIO", priority=5),
                    FilenameRule(pattern="周报", value="HIGH_PRIO", priority=10),
                ],
            ),
        ],
    ),
)
result_diff_prio = mb_diff_prio.extract_filename_metadata("2026年第03周周报")
req("Higher priority wins", "HIGH_PRIO",
   result_diff_prio.get("category", ""))

# ---- A16. Very long filename ----
print("\n--- A16: very long filename ---")
long_name = "A" * 10000 + "周报" + "B" * 10000
result_long = mb.extract_filename_metadata(long_name)
req("Long filename: doc_category still works", "周报",
   result_long.get("doc_category", ""))
check("Long filename: title matches full name",
      result_long.get("title", "") == long_name)

# ---- A17. Filename with no extension (already base) ----
print("\n--- A17: filename with no extension (already base) ---")
mb_no_ext = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(name="title", auto_file_name=True),
        ],
    ),
)
result_no_ext = mb_no_ext.extract_filename_metadata("myfile")
req("No extension: title = input", "myfile",
   result_no_ext.get("title", ""))

# ---- A18. {1} without capture group in match ----
print("\n--- A18: {1} without capture group (fallback behavior) ---")
# The code does: try match.group(1) -> IndexError -> match.group(0)
mb_no_capture = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="test_field",
                rules=[FilenameRule(pattern=r"雷达", value="{1}", priority=10)],
            ),
        ],
    ),
)
result_no_cap = mb_no_capture.extract_filename_metadata("AN/SPY-1D雷达")
req("{1} without capture group: falls back to match(0)", "雷达",
   result_no_cap.get("test_field", ""))
# Based on code: try match.group(1) -> IndexError -> match.group(0)

# ====================================================================
# B. apply_to_chunks integration
# ====================================================================

print("\n" + "=" * 70)
print("B. apply_to_chunks integration")
print("=" * 70)

# ---- B1. Verify heading + filename + rule + manual + document LLM + chunk LLM ----
print("\n--- B1: apply_to_chunks order verification (without LLM) ---")
# Use a MetadataBuilder with all source types except LLM (no LLM available)
mb_full = MetadataBuilder(
    MetadataBuilderConfig(
        heading_fields=[
            MetadataFieldConfig(name="heading_path", source="heading", type="string"),
            MetadataFieldConfig(name="section_depth", source="heading", type="number"),
        ],
        rule_fields=[
            MetadataFieldConfig(name="has_number", source="rule", type="boolean",
                                rule_pattern=r"\d+"),
        ],
        manual_fields=[
            MetadataFieldConfig(name="doc_type", source="manual", type="string"),
        ],
        filename_fields=[
            FilenameFieldConfig(name="doc_category", rules=[
                FilenameRule(pattern="周报", value="周报", priority=10),
            ]),
        ],
    ),
)

# First extract filename metadata
fn_result = mb_full.extract_filename_metadata("2026周报")
check("B1: filename metadata extracted",
      fn_result.get("doc_category") == "周报",
      f"got {fn_result!r}")

# Create chunks
chunks = [
    {"content": "这是第一段内容，包含数字123", "heading_breadcrumb": "首页", "level": 1},
    {"content": "这是第二段内容", "heading_breadcrumb": "首页 > 详情", "level": 2},
]

# Apply without LLM
import asyncio

async def test_apply():
    await mb_full.apply_to_chunks(chunks, None, "full content here",
                                   extra_doc_meta=fn_result)
    return chunks

chunks_result = asyncio.run(test_apply())

# Check chunk 0
meta0 = chunks_result[0].get("_edp_meta", {})
print(f"  Chunk 0 _edp_meta: {meta0!r}")
check("B1a: heading_path present", "heading_path" in meta0,
      f"keys={list(meta0.keys())}")
check("B1b: section_depth present", "section_depth" in meta0)
check("B1c: doc_category from filename", meta0.get("doc_category") == "周报",
      f"got {meta0.get('doc_category')!r}")
check("B1d: has_number from rule present", "has_number" in meta0)
check("B1e: doc_type from manual present", "doc_type" in meta0)
check("B1f: manual field defaults to empty string",
      meta0.get("doc_type") == "")
check("B1g: has_number is boolean True for content with digits",
      meta0.get("has_number") is True)

meta1 = chunks_result[1].get("_edp_meta", {})
check("B1h: has_number is boolean False for content without digits",
      meta1.get("has_number") is False)

# ---- B2. Verify chunks are mutated in-place ----
print("\n--- B2: chunks mutated in-place ---")
check("B2: chunk[0] has _edp_meta", "_edp_meta" in chunks_result[0])
check("B2: chunk[1] has _edp_meta", "_edp_meta" in chunks_result[1])
# The same list object should be mutated
check("B2: original chunks list mutated",
      "_edp_meta" in chunks[0],
      "chunks[0] still has no _edp_meta")

# ====================================================================
# C. Pipeline integration verification (code-level checks)
# ====================================================================

print("\n" + "=" * 70)
print("C. Pipeline integration verification")
print("=" * 70)

# We can verify by reading the actual pipeline.py source
import ast

pipeline_path = os.path.join(
    os.path.dirname(__file__), "fixed-pkg", "lightrag", "lightrag", "pipeline.py"
)

print("\n--- C1: Check doc_title priority ---")
# Scan pipeline.py for the doc_title logic
with open(pipeline_path, "r", encoding="utf-8") as f:
    content = f.read()
    # Check that doc_title is set from filename first, with content fallback
    if "if not doc_title.strip():" in content:
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "if not doc_title.strip():" in line:
                # Show context lines
                start = max(0, i - 1)
                end = min(len(lines), i + 10)
                context = lines[start:end]
                code_block = "\n".join(context)
                # Check for filename extraction before content first line
                has_filename_fallback = "splitext" in code_block and "basename" in code_block
                has_content_fallback = "first_line" in code_block
                check("C1: doc_title from filename (splitext/basename)",
                      has_filename_fallback)
                check("C1: doc_title fallback to content first_line",
                      has_content_fallback)
                break

print("\n--- C2: Check old _edp_llm_metadata and _edp_llm_call are gone ---")
has_old_metadata = "_edp_llm_metadata" in content
has_old_call = "_edp_llm_call" in content
old_style_meta = "_edp_llm_metadata" in content
old_style_call = "_edp_llm_call" in content
check("C2: old _edp_llm_metadata removed", not old_style_meta)
check("C2: old _edp_llm_call removed", not old_style_call)

print("\n--- C3: Verify imports in pipeline.py ---")
has_metadata_builder_import = "from lightrag.edp.metadata_builder import" in content
has_models_import = "from lightrag.edp.models import" in content
has_profile_manager_import = "from lightrag.edp.profile_manager import" in content
check("C3: imports MetadataBuilder from edp.metadata_builder",
      has_metadata_builder_import)
check("C3: imports from edp.models", has_models_import)
check("C3: imports ProfileManager", has_profile_manager_import)

# Check that apply_to_chunks is called
has_apply_to_chunks = "apply_to_chunks" in content
check("C3: calls apply_to_chunks", has_apply_to_chunks)

# Check that extract_filename_metadata is called
has_extract_filename = "extract_filename_metadata" in content
check("C3: calls extract_filename_metadata", has_extract_filename)

# ====================================================================
# D. Pydantic validation
# ====================================================================

print("\n" + "=" * 70)
print("D. Pydantic / YAML validation")
print("=" * 70)

print("\n--- D1: Parse general.yml correctly ---")
import yaml

general_yml_path = os.path.join(
    os.path.dirname(__file__),
    "fixed-pkg", "lightrag", "lightrag", "edp", "profiles", "general.yml",
)
with open(general_yml_path, "r", encoding="utf-8") as f:
    general_data = yaml.safe_load(f)

try:
    mb_config_general = MetadataBuilderConfig(**general_data["metadata_builder"])
    check("D1a: general.yml parses to MetadataBuilderConfig", True)
    check("D1b: has filename_fields",
          len(mb_config_general.filename_fields) > 0,
          f"got {len(mb_config_general.filename_fields)} fields")
    field_names = [f.name for f in mb_config_general.filename_fields]
    check("D1c: has title field", "title" in field_names, f"names={field_names}")
    check("D1d: has doc_category field", "doc_category" in field_names,
          f"names={field_names}")
    check("D1e: has entity_name field", "entity_name" in field_names,
          f"names={field_names}")
    check("D1f: has entity_type field", "entity_type" in field_names,
          f"names={field_names}")
    check("D1g: has target_country field", "target_country" in field_names,
          f"names={field_names}")
    check("D1h: title has auto_file_name=True",
          all(f.auto_file_name for f in mb_config_general.filename_fields if f.name == "title"))
    check("D1i: entity_type has depends_on='entity_name'",
          all(f.depends_on == "entity_name"
              for f in mb_config_general.filename_fields if f.name == "entity_type"))
    # Verify rules on specific fields
    for f in mb_config_general.filename_fields:
        if f.name == "doc_category":
            check("D1j: doc_category has 5 rules", len(f.rules) == 5,
                  f"got {len(f.rules)} rules")
        if f.name == "entity_type":
            check("D1k: entity_type has 2 rules", len(f.rules) == 2,
                  f"got {len(f.rules)} rules")
except Exception as e:
    check("D1a: general.yml parses", False, f"raised {type(e).__name__}: {e}")
    traceback.print_exc()

print("\n--- D2: Parse week_report.yml (no filename_fields) ---")
week_yml_path = os.path.join(
    os.path.dirname(__file__),
    "fixed-pkg", "lightrag", "lightrag", "edp", "profiles", "week_report.yml",
)
with open(week_yml_path, "r", encoding="utf-8") as f:
    week_data = yaml.safe_load(f)

try:
    mb_config_week = MetadataBuilderConfig(**week_data["metadata_builder"])
    check("D2a: week_report.yml parses to MetadataBuilderConfig", True)
    check("D2b: filename_fields defaults to empty list",
          len(mb_config_week.filename_fields) == 0,
          f"got {len(mb_config_week.filename_fields)} fields (should be 0)")
    check("D2c: document_fields present",
          len(mb_config_week.document_fields) > 0)
    check("D2d: heading_fields present",
          len(mb_config_week.heading_fields) > 0)
    check("D2e: llm_fields present",
          len(mb_config_week.llm_fields) > 0)
    check("D2f: rule_fields present",
          len(mb_config_week.rule_fields) > 0)
    check("D2g: manual_fields present",
          len(mb_config_week.manual_fields) > 0)
except Exception as e:
    check("D2a: week_report.yml parses", False, f"raised {type(e).__name__}: {e}")
    traceback.print_exc()

print("\n--- D3: Parse day_report.yml (no filename_fields, should default) ---")
day_yml_path = os.path.join(
    os.path.dirname(__file__),
    "fixed-pkg", "lightrag", "lightrag", "edp", "profiles", "day_report.yml",
)
with open(day_yml_path, "r", encoding="utf-8") as f:
    day_data = yaml.safe_load(f)

try:
    mb_config_day = MetadataBuilderConfig(**day_data["metadata_builder"])
    check("D3a: day_report.yml parses to MetadataBuilderConfig", True)
    check("D3b: filename_fields defaults to empty list",
          len(mb_config_day.filename_fields) == 0,
          f"got {len(mb_config_day.filename_fields)} fields (should be 0)")
except Exception as e:
    check("D3a: day_report.yml parses", False, f"raised {type(e).__name__}: {e}")


# ====================================================================
# E. More edge cases
# ====================================================================

print("\n" + "=" * 70)
print("E. Additional edge cases")
print("=" * 70)

# ---- E1. Multiple rules with same priority, multiple matches ----
print("\n--- E1: Multiple rules match, same priority, first match wins ---")
mb_multi_match = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="category",
                rules=[
                    FilenameRule(pattern="快报", value="快报", priority=10),
                    FilenameRule(pattern="周报", value="周报", priority=10),
                    FilenameRule(pattern="要报", value="要报", priority=10),
                ],
            ),
        ],
    ),
)
result_multi = mb_multi_match.extract_filename_metadata("2026年周报快报要报")
req("E1: First rule in config order wins (快报 before 周报)",
   "快报",
   result_multi.get("category", ""))

# ---- E2. Regex special characters in filename ----
print("\n--- E2: Special regex characters in filename ---")
mb_special = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="has_special",
                rules=[FilenameRule(pattern=r"\(秘密\)", value="秘密", priority=10)],
            ),
        ],
    ),
)
result_special = mb_special.extract_filename_metadata("2026年报告(秘密)")
req("E2: Regex special chars handled", "秘密",
   result_special.get("has_special", ""))

# ---- E3. Multiple filename_fields with same name ----
print("\n--- E3: Multiple fields with same name (last wins in doc_metadata) ---")
mb_dup_name = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(name="same_name",
                                rules=[FilenameRule(pattern="A", value="FIRST",
                                                    priority=10)]),
            FilenameFieldConfig(name="same_name",
                                rules=[FilenameRule(pattern="B", value="SECOND",
                                                    priority=10)]),
        ],
    ),
)
result_dup_name = mb_dup_name.extract_filename_metadata("AB")
req("E3: Duplicate name: later value stored",
   "SECOND", result_dup_name.get("same_name", ""))

# ---- E4. Unmatched filename_fields return empty string ----
print("\n--- E4: No rule matches -> empty string ---")
mb_no_match = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="category",
                rules=[FilenameRule(pattern="周报", value="周报", priority=10)],
            ),
        ],
    ),
)
result_no_match = mb_no_match.extract_filename_metadata("daily_report")
req("E4: No match = empty string", "",
   result_no_match.get("category", ""))

# ---- E5. depends_on chain of 3 fields ----
print("\n--- E5: Chain of depends_on (a -> b -> c) ---")
mb_chain = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="a",
                rules=[FilenameRule(pattern="XYZ", value="found_a", priority=10)],
            ),
            FilenameFieldConfig(
                name="b",
                depends_on="a",
                rules=[FilenameRule(pattern="found_a", value="found_b", priority=10)],
            ),
            FilenameFieldConfig(
                name="c",
                depends_on="b",
                rules=[FilenameRule(pattern="found_b", value="found_c", priority=10)],
            ),
        ],
    ),
)
result_chain = mb_chain.extract_filename_metadata("XYZ-other")
req("E5a: a = found_a", "found_a", result_chain.get("a", ""))
req("E5b: b = found_b", "found_b", result_chain.get("b", ""))
req("E5c: c = found_c", "found_c", result_chain.get("c", ""))

# ---- E6. Non-string output_key ----
print("\n--- E6: output_key mapping ---")
mb_output_key = MetadataBuilder(
    MetadataBuilderConfig(
        filename_fields=[
            FilenameFieldConfig(
                name="internal_name",
                output_key="output_name",
                rules=[FilenameRule(pattern="周报", value="周报_value", priority=10)],
            ),
        ],
    ),
)
result_output_key = mb_output_key.extract_filename_metadata("周报文件")
check("E6: uses output_key as storage key",
      "output_name" in result_output_key,
      f"keys={list(result_output_key.keys())}")
check("E6: internal_name not in result",
      "internal_name" not in result_output_key,
      f"keys={list(result_output_key.keys())}")
req("E6: value via output_key", "周报_value",
   result_output_key.get("output_name", ""))


# ====================================================================
# Summary
# ====================================================================

print("\n" + "=" * 70)
print("TEST SUMMARY")
print("=" * 70)
total = passed + failed
print(f"  Total: {total}")
print(f"  Passed: {passed}")
print(f"  Failed: {failed}")
if errors:
    print("\n  Failed tests:")
    for label, detail in errors:
        print(f"    - {label}: {detail}")

sys.exit(0 if failed == 0 else 1)
