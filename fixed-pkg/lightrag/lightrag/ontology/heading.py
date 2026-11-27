"""Strict numbered-heading boundaries; original S implementation stays untouched."""
import re

CN = "一二三四五六七八九十百零〇"
PATTERNS = [
    ("chapter", re.compile(r"^(第["+CN+r"\d]+[章节篇])\s*(.+)$"), 1),
    ("chinese", re.compile(r"^["+CN+r"]+[、.．。]\s*(\S.*)$"), 1),
    ("bracket_cn", re.compile(r"^[（(]["+CN+r"]+[）)]\s*(\S.*)$"), 2),
    ("bracket_num", re.compile(r"^(?:[（(]\d+[）)]|\d+[）)])\s*(\S.*)$"), 3),
    ("number", re.compile(r"^(\d+(?:[.．]\d+)*)(?:[.．、。]\s*|\s+)(?![\d.．])(\S.*)$"), None),
    ("markdown", re.compile(r"^(#{1,6})\s+(\S.*)$"), None),
]

def heading_chunks(tokenizer, content, size, *, depth=1, formats=None, number_base=None):
    if size < 1 or not 1 <= depth <= 6:
        raise ValueError("Invalid heading depth or token size")
    allowed = set(formats) if formats is not None else {p[0] for p in PATTERNS}
    # Numbered-only documents use level 1; mixed Chinese headings default to level 2.
    base = number_base if number_base is not None else (2 if re.search(r"(?m)^\s*(?:#{1,6}\s*)?["+CN+r"]+[、.．。]", content) else 1)
    def match(line):
        line = line.strip()
        if len(line) > 80 or re.search(r"[。！？；;]$", line) or re.match(r"^(?:\d{4}[-/年]|\d+\.\d+\.\d+\.\d+|图\s*\d|表\s*\d)", line):
            return None
        numbered = re.sub(r"^#{1,6}\s*", "", line)
        # Reject numeric measurements, dates and common list rows; Markdown style
        # remains an explicit heading signal where the author supplied one.
        if not line.startswith("#") and re.match(r"^\d+(?:[.．]\d+)?\s*(?:%|℃|°|ms\b|kg\b|km\b|MHz\b|GHz\b|米|公斤|秒|年|月|日)", numbered, re.I):
            return None
        if re.search(r"[。！？；;]$", numbered) or re.match(r"^(?:图|表)\s*\d",numbered):
            return None
        for tag, pattern, level in PATTERNS:
            m = pattern.match(line if tag == "markdown" else numbered)
            if not m or tag not in allowed:
                continue
            if tag == "number":
                # Plain "88 电量" is not a numbered heading. A single number needs punctuation.
                if not re.match(r"^\d+(?:[.．]\d+)*[.．、。]", numbered) and not re.search(r"[.．]",m.group(1)):
                    continue
                level = base + len(re.split(r"[.．]", m.group(1))) - 1
            elif tag == "markdown":
                level = len(m.group(1))
            return tag, level
        return None
    bounds, stack, offset = [(0, [], None, None)], [], 0
    fence = None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})",line)
        if marker:
            ch = marker.group(1)[0]
            if fence is None: fence = ch
            elif fence == ch: fence = None
            offset += len(line)
            continue
        found = None if fence else match(line)
        if found and found[1] <= depth:
            tag, level = found
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][2] if stack else None
            sid = f"section-{offset}"
            stack.append((level, line.strip(), sid))
            bounds.append((offset, [x[1] for x in stack], sid, parent))
        offset += len(line)
    result = []
    for i, (start, path, sid, parent) in enumerate(bounds):
        end = bounds[i+1][0] if i+1 < len(bounds) else len(content)
        body = content[start:end]
        if not body.strip():
            continue
        # Work with character slices, not decoded BPE windows: a token boundary
        # can cut a Chinese character in half. Preserve exact source spans.
        for j, (lo,hi) in enumerate(split_section(tokenizer,body,size)):
            piece = body[lo:hi]
            part = {"content":piece,"tokens":len(tokenizer.encode(piece)),
                    "_source_span":{"start":start+lo,"end":start+hi}}
            result.append({**part, "chunk_order_index": len(result), "heading_path": path,
                "section_id": sid, "parent_section_id": parent, "section_part": j,
                "section_start": start, "section_end": end, "chunking_strategy": "H"})
    return result


def split_section(tokenizer,text,size):
    """Prefer paragraph/sentence boundaries while enforcing a hard token limit."""
    pos, n = 0, len(text)
    while pos < n:
        # Probe a bounded window rather than encoding the entire remaining
        # chapter at every iteration.
        upper = min(n,pos+max(32,size*4))
        while upper < n and len(tokenizer.encode(text[pos:upper])) <= size:
            upper = min(n,pos+2*(upper-pos))
        if len(tokenizer.encode(text[pos:upper])) <= size:
            end = upper
        else:
            low, high = pos+1, upper
            end = pos
            while low <= high:
                mid = (low+high)//2
                if len(tokenizer.encode(text[pos:mid])) <= size:
                    end,low = mid,mid+1
                else:
                    high = mid-1
            if end == pos:
                raise ValueError("Token limit too small for a single character")
            # Prefer a natural boundary near the maximum feasible end.
            if end < n:
                window = text[pos:end]
                candidates = [m.end() for m in re.finditer(r"\n\n+|\n|[。！？；.!?]\s*",window)
                              if m.end() >= max(1,len(window)*0.6)]
                if candidates:
                    cut = pos+candidates[-1]
                    if len(tokenizer.encode(text[pos:cut])) <= size:
                        end = cut
        if text[pos:end].strip():
            yield pos,end
        pos = end
