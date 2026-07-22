"""Multi-stage heading detector — score-based, not regex-pile.

Walks every line through three stages:
  1. Pattern Match  — does it look like a heading?
  2. Context Score  — surrounding blank lines, length, punctuation?
  3. Exclusion      — paragraph labels, figure/table captions, colon-end?

A line with total score >= THRESHOLD (70) is a heading; otherwise normal text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar


# ======================================================================
# Data
# ======================================================================


@dataclass
class HeadingCandidate:
    """Result of heading detection for a single line."""
    is_heading: bool
    level: int              # 1-6; 0 = not a heading
    score: int              # 0-100
    text: str               # cleaned heading text (without numbering prefix)
    full_text: str          # original line (trimmed)
    pattern_tag: str = ""   # which pattern matched (chinese_h1, decimal, …)
    reason: list[str] = field(default_factory=list)
    char_offset: int = 0    # start position in original content


# ======================================================================
# Detector
# ======================================================================


class HeadingDetector:
    """Score-based heading detection for Chinese and English documents.

    Usage::

        hds = HeadingDetector.build(lines_of_text)
        for hd in hds:
            if hd.is_heading:
                print(hd.level, hd.text)
    """

    THRESHOLD: ClassVar[int] = 70
    MAX_HEADING_LENGTH: ClassVar[int] = 80

    # ------------------------------------------------------------------
    # Pattern table: (regex, default_level, base_score, reason_tag)
    # ------------------------------------------------------------------

    _PATTERNS: ClassVar[list[tuple[re.Pattern, int, int, str]]] = []

    @classmethod
    def _init_patterns(cls) -> None:
        if cls._PATTERNS:
            return
        cls._PATTERNS = [
            # Markdown ATX headings: # … ######
            (re.compile(r"^(#{1,6})\s+(.+)"), None, 80, "markdown"),
            # 章节条: 第一章, 第3节, 第二条
            (re.compile(r"^(第[一二三四五六七八九十\d]+[章节条])\s*(.*)"), None, 70, "chapter_section"),
            # 中文编号大标题: 一、二、三、…  (也接受 一. / 一． 作为分隔符)
            (re.compile(r"^([一二三四五六七八九十]+)[、.．]\s*(.*)"), 1, 70, "chinese_h1"),
            # 括号子标题(中文数字): （一）（二） 也接受半角括号 (一) (二)
            (re.compile(r"^[\(（]([一二三四五六七八九十]+)[\)）]\s*(.*)"), 2, 70, "chinese_h2_full"),
            # 括号子标题(阿拉伯数字): (1) (2) 也接受全角括号 （1）（2）
            (re.compile(r"^[\(（](\d+)[\)）]\s*(.*)"), 2, 65, "chinese_h2_half"),
            # 数字编号多级: 1.  1.1  1.1.1  (也接受 1． 1、1空格 作为分隔符)
            (re.compile(r"^(\d+(?:\.\d+)*)[.．、\s]+(.+)"), None, 60, "decimal"),
        ]

    # ------------------------------------------------------------------
    # Exclusion table: (regex, penalty, reason_tag)
    # ------------------------------------------------------------------

    _EXCLUSIONS: ClassVar[list[tuple[re.Pattern, int, str]]] = []

    @classmethod
    def _init_exclusions(cls) -> None:
        if cls._EXCLUSIONS:
            return
        cls._EXCLUSIONS = [
            # 段落标签: 第一段, 第二部分 — NOT headings
            (re.compile(r"^第[一二三四五六七八九十\d]+[段部分]"), 60, "paragraph_label"),
            # 图表编号: 图1, 表2, Figure 1, Table 2
            (re.compile(r"^[图表][\s\d]"), 60, "figure_table"),
            (re.compile(r"^(Figure|Table)\s+\d", re.IGNORECASE), 60, "figure_table_en"),
            # 行尾冒号 — 段落引导语，不是标题
            (re.compile(r"[：:]\s*$"), 30, "colon_end"),
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._init_patterns()
        self._init_exclusions()

    def detect(
        self,
        line: str,
        *,
        prev_line: str = "",
        next_line: str = "",
        char_offset: int = 0,
    ) -> HeadingCandidate:
        """Score *line* and decide if it is a heading."""
        text = line.strip()
        if not text:
            return HeadingCandidate(
                is_heading=False, level=0, score=0,
                text="", full_text=text, pattern_tag="", char_offset=char_offset,
            )

        # ---- 1. Pattern match ----
        score, level, reason_tag, match = self._pattern_score(text)

        # ---- 2. Context features ----
        ctx, ctx_reasons = self._context_score(text, prev_line, next_line)
        score += ctx
        reason = ([reason_tag] if reason_tag else []) + ctx_reasons

        # ---- 3. Exclusions ----
        penalty, excl_reasons = self._exclusion_penalty(text)
        score -= penalty
        if excl_reasons:
            reason.extend(excl_reasons)

        # Clamp.
        score = max(0, min(100, score))

        is_heading = score >= self.THRESHOLD

        # Clean text: strip numbering prefix.
        clean_text = text
        if is_heading and match:
            clean_text = self._clean_text(text, reason_tag, match)

        return HeadingCandidate(
            is_heading=is_heading,
            level=level if is_heading else 0,
            score=score,
            text=clean_text,
            full_text=text,
            pattern_tag=reason_tag,
            reason=reason,
            char_offset=char_offset,
        )

    @classmethod
    def build(
        cls,
        lines: list[str],
        char_offsets: list[int] | None = None,
    ) -> list[HeadingCandidate]:
        """Detect headings in *lines* with context.

        Returns only lines that are headings, sorted by position.
        """
        detector = cls()
        results: list[HeadingCandidate] = []

        if char_offsets is None:
            # Compute offsets.
            char_offsets = []
            pos = 0
            for ln in lines:
                char_offsets.append(pos)
                pos += len(ln) + 1  # +1 for newline

        for i, line in enumerate(lines):
            prev_line = lines[i - 1] if i > 0 else ""
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            offset = char_offsets[i] if i < len(char_offsets) else 0
            candidate = detector.detect(
                line,
                prev_line=prev_line,
                next_line=next_line,
                char_offset=offset,
            )
            if candidate.is_heading:
                results.append(candidate)

        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @classmethod
    def _pattern_score(cls, line: str) -> tuple[int, int, str, re.Match | None]:
        """Return (score, level, reason_tag, match) for the best pattern."""
        best_score = 0
        best_level = 0
        best_reason = ""
        best_match = None

        for pattern, default_level, base_score, tag in cls._PATTERNS:
            m = pattern.match(line)
            if not m:
                continue
            raw_level = default_level

            if tag == "markdown":
                raw_level = len(m.group(1))  # number of #
            elif tag == "decimal":
                # Count dots for level: 1.→3, 1.1→4, 1.1.1→5
                dots = m.group(1).count(".")
                raw_level = dots + 3

            if raw_level is not None and raw_level > 0:
                level = raw_level
            else:
                level = 1

            if base_score > best_score:
                best_score = base_score
                best_level = level
                best_reason = tag
                best_match = m

        return best_score, best_level, best_reason, best_match

    @classmethod
    def _context_score(
        cls,
        line: str,
        prev_line: str,
        next_line: str,
    ) -> tuple[int, list[str]]:
        """Return (score, reasons) from contextual heuristics."""
        score = 0
        reasons: list[str] = []

        # Short line.
        if len(line) < cls.MAX_HEADING_LENGTH:
            score += 15
            reasons.append("short_line")
        else:
            score -= 10  # long line — unlikely heading

        # No sentence-ending punctuation at end.
        if not re.search(r"[。！？．.!?]$", line.rstrip()):
            score += 10
            reasons.append("no_sentence_end")

        # Surrounded by blank lines.
        if not prev_line.strip() or not next_line.strip():
            score += 10
            reasons.append("blank_nearby")

        # Next line is longer (body text after heading).
        if next_line.strip() and len(next_line.strip()) > len(line):
            score += 5
            reasons.append("next_longer")

        return score, reasons

    @classmethod
    def _exclusion_penalty(cls, line: str) -> tuple[int, list[str]]:
        """Return (total_penalty, reasons)."""
        penalty = 0
        reasons: list[str] = []
        for pattern, pen, tag in cls._EXCLUSIONS:
            if pattern.search(line):
                penalty += pen
                reasons.append(f"excl:{tag}")
        return penalty, reasons

    @staticmethod
    def _clean_text(line: str, reason_tag: str, match: re.Match) -> str:
        """Strip numbering prefix from heading text."""
        if reason_tag == "markdown":
            # remove # prefix
            return match.group(2).strip()
        if reason_tag == "chapter_section":
            # remove "第一章" prefix
            return match.group(2).strip() or match.group(1).strip()
        if reason_tag == "chinese_h1":
            # remove "一、" prefix
            return match.group(2).strip() or match.group(1).strip()
        if reason_tag in ("chinese_h2_full", "chinese_h2_half"):
            # remove "（一）" or "(1)" prefix
            return match.group(2).strip() or match.group(1).strip()
        if reason_tag == "decimal":
            # remove "1.1 " prefix
            return match.group(2).strip() or match.group(1).strip()
        return line.strip()
