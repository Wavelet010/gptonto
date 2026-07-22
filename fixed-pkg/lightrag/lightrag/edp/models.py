"""EDP data models — Parser Profile, context/meta builder configs."""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class ContextBuilderConfig(BaseModel):
    """Configuration for the context builder (embedding_text construction)."""
    enabled: bool = True
    mode: Literal["rich", "compact", "raw"] = "rich"
    compact_fields: list[str] = Field(default_factory=list)
    include_doc_title: bool = True
    include_heading_path: bool = True
    sep: str = " | "


class MetadataFieldConfig(BaseModel):
    """A single metadata field definition within a profile."""
    name: str
    source: Literal["document", "heading", "llm", "rule", "manual"]
    type: str = "string"  # string | number | boolean | text
    description: str = ""
    llm_prompt: str | None = None
    rule_pattern: str | None = None
    output_key: str | None = None  # storage key, defaults to `name`


class MetadataBuilderConfig(BaseModel):
    """Configuration for all metadata sources in a profile."""
    document_fields: list[MetadataFieldConfig] = Field(default_factory=list)
    heading_fields: list[MetadataFieldConfig] = Field(default_factory=list)
    llm_fields: list[MetadataFieldConfig] = Field(default_factory=list)
    rule_fields: list[MetadataFieldConfig] = Field(default_factory=list)
    manual_fields: list[MetadataFieldConfig] = Field(default_factory=list)


class ParserProfile(BaseModel):
    """A parser profile that defines context and metadata extraction strategy.

    Chunking strategy is chosen independently via API or LIGHTRAG_PARSER env."""
    profile_name: str
    description: str = ""
    chunk_token_size: int = 1200
    chunk_overlap_token_size: int = 0
    heading_levels: dict[str, int] = Field(default_factory=dict)
    context_builder: ContextBuilderConfig = Field(default_factory=ContextBuilderConfig)
    metadata_builder: MetadataBuilderConfig = Field(default_factory=MetadataBuilderConfig)
