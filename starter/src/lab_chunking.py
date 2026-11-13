"""Chunking strategies over the counted law pages, with traceable metadata.

All counted pages are concatenated into one text stream; every chunk keeps its
character span in that stream, from which page(s), chapter, section (Mục) and
article (Điều) are derived. Metadata values come only from headings in the text.
"""
from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass

from .models import Chunk

CHAPTER_RE = re.compile(r"^Chương ([IVXLC]+)$", re.M)
SECTION_RE = re.compile(r"^Mục (\d+)$", re.M)
ARTICLE_RE = re.compile(r"^Điều (\d+)\.", re.M)
KHOAN_RE = re.compile(r"^\d+\. ", re.M)  # khoản "1. "
POINT_RE = re.compile(r"^[a-zđ]\) ", re.M)  # điểm "a) "


@dataclass
class Stream:
    """Counted pages joined by '\n', with page start offsets and headings."""

    document_id: str
    source: str
    text: str
    page_starts: list[int]
    page_numbers: list[int]
    headings: dict[str, list[tuple[int, str, str]]]  # kind -> [(offset, value, title)]

    @classmethod
    def build(cls, document_id: str, source: str, pages: list[tuple[int, str]]) -> "Stream":
        parts, starts, offset = [], [], 0
        for _, content in pages:
            starts.append(offset)
            parts.append(content)
            offset += len(content) + 1
        text = "\n".join(parts)
        headings: dict[str, list[tuple[int, str, str]]] = {}
        for kind, rx in (("chapter", CHAPTER_RE), ("section", SECTION_RE), ("article", ARTICLE_RE)):
            items = []
            for m in rx.finditer(text):
                line_end = text.find("\n", m.start())
                line = text[m.start(): line_end if line_end != -1 else len(text)]
                title = line
                if kind != "article":  # title is the uppercase line(s) after "Chương X"/"Mục N"
                    rest = text[line_end + 1:].split("\n")
                    title_lines = []
                    for l in rest:
                        if l and l == l.upper() and any(c.isalpha() for c in l):
                            title_lines.append(l)
                        else:
                            break
                    title = " ".join(title_lines)
                items.append((m.start(), m.group(1), title))
            headings[kind] = items
        return cls(document_id, source, text, starts, [p for p, _ in pages], headings)

    def page_at(self, offset: int) -> int:
        return self.page_numbers[bisect.bisect_right(self.page_starts, offset) - 1]

    def heading_at(self, kind: str, offset: int) -> tuple[str, str] | None:
        items = self.headings[kind]
        idx = bisect.bisect_right([o for o, _, _ in items], offset) - 1
        if idx < 0:
            return None
        if kind == "section":  # a Mục only lives inside its chapter
            chap = self.heading_at("chapter", offset)
            chap_off = max((o for o, _, _ in self.headings["chapter"] if o <= offset), default=-1)
            if items[idx][0] < chap_off or chap is None:
                return None
        return items[idx][1], items[idx][2]

    def make_chunk(self, strategy: str, start: int, end: int, text: str, ordinal: int,
                   extra: dict | None = None) -> Chunk:
        chap = self.heading_at("chapter", start)
        sec = self.heading_at("section", start)
        art = self.heading_at("article", start)
        digest = hashlib.sha1(f"{strategy}|{self.document_id}|{ordinal}|{text}".encode()).hexdigest()[:16]
        return Chunk(
            chunk_id=f"{strategy}-{digest}",
            document_id=self.document_id,
            source=self.source,
            page=self.page_at(start),
            text=text,
            metadata={
                "page_end": self.page_at(max(start, end - 1)),
                "chapter": chap[0] if chap else None,
                "chapter_title": chap[1] if chap else None,
                "section": sec[0] if sec else None,
                "article": int(art[0]) if art else None,
                "char_start": start,
                "char_end": end,
                **(extra or {}),
            },
        )


def _dedupe_ordinals(items: list[tuple[int, int, str]]) -> list[int]:
    """Ordinal per identical text, so duplicate texts still get distinct stable IDs."""
    seen: dict[str, int] = {}
    out = []
    for _, _, text in items:
        out.append(seen.get(text, 0))
        seen[text] = out[-1] + 1
    return out


# --------------------------------------------------------------------------
# Strategy A: fixed-size windows inside each page
# --------------------------------------------------------------------------
def fixed_size(stream: Stream, strategy: str, size: int, overlap: int) -> list[Chunk]:
    """Windows of <= size chars with `overlap` chars shared; never cross a page.

    The window end is moved back to the last whitespace (if one exists in the
    last 20% of the window) so Vietnamese syllables are not cut in half.
    """
    if not 0 <= overlap < size:
        raise ValueError("overlap must be in [0, size)")
    spans: list[tuple[int, int, str]] = []
    bounds = stream.page_starts + [len(stream.text) + 1]
    for i in range(len(stream.page_starts)):
        p_start, p_end = bounds[i], bounds[i + 1] - 1
        pos = p_start
        while pos < p_end:
            end = min(pos + size, p_end)
            if end < p_end:
                cut = stream.text.rfind(" ", pos + int(size * 0.8), end)
                cut_nl = stream.text.rfind("\n", pos + int(size * 0.8), end)
                cut = max(cut, cut_nl)
                if cut > pos:
                    end = cut
            spans.append((pos, end, stream.text[pos:end].strip()))
            if end >= p_end:
                break
            pos = max(end - overlap, pos + 1)
            while pos < end and not stream.text[pos - 1].isspace():  # start on a word boundary
                pos += 1
    ordinals = _dedupe_ordinals(spans)
    return [stream.make_chunk(strategy, s, e, t, o) for (s, e, t), o in zip(spans, ordinals) if t]


# --------------------------------------------------------------------------
# Strategy B: structure-aware (Điều -> khoản -> điểm), bounded size
# --------------------------------------------------------------------------
def _split_units(text: str, base: int, rx: re.Pattern) -> list[tuple[int, int]]:
    """Split [base, base+len(text)) at lines matching rx; returns absolute spans."""
    starts = [m.start() for m in rx.finditer(text)]
    if not starts or starts[0] != 0:
        starts = [0] + starts
    starts.append(len(text))
    return [(base + a, base + b) for a, b in zip(starts, starts[1:]) if text[a:b].strip()]


def _hard_split(stream: Stream, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    """Last resort for a single over-long unit: cut at sentence end/whitespace."""
    out, pos = [], start
    while end - pos > limit:
        window = stream.text[pos: pos + limit]
        cut = max(window.rfind("; "), window.rfind(". "), window.rfind(";\n"), window.rfind(".\n"))
        if cut < limit * 0.5:
            cut = max(window.rfind(" "), window.rfind("\n"))
        cut = pos + (cut + 1 if cut > 0 else limit)
        out.append((pos, cut))
        pos = cut
    out.append((pos, end))
    return out


def structure(stream: Stream, strategy: str, max_size: int) -> list[Chunk]:
    """One chunk per Điều when it fits; otherwise pack whole khoản (then điểm)
    up to max_size. Continuation chunks repeat the article heading line so each
    chunk names its Điều. Chương/Mục heading lines are kept out of chunk bodies
    (they become metadata)."""
    text = stream.text
    # Boundaries: every Điều / Chương / Mục heading starts a new block.
    marks = sorted(
        [(o, "article") for o, _, _ in stream.headings["article"]]
        + [(o, "chapter") for o, _, _ in stream.headings["chapter"]]
        + [(o, "section") for o, _, _ in stream.headings["section"]]
    )
    blocks: list[tuple[int, int, str]] = []  # (start, end, kind)
    first = marks[0][0] if marks else len(text)
    if text[:first].strip():
        blocks.append((0, first, "preamble"))
    for (o, kind), nxt in zip(marks, [m[0] for m in marks[1:]] + [len(text)]):
        blocks.append((o, nxt, kind))

    spans: list[tuple[int, int, str]] = []
    for start, end, kind in blocks:
        if kind in ("chapter", "section"):
            continue  # heading + title only; captured as metadata
        body = text[start:end].rstrip("\n")
        end = start + len(body)
        heading = body.split("\n", 1)[0] if kind == "article" else ""
        if len(body) <= max_size:
            spans.append((start, end, body))
            continue
        # khoản level, then điểm level for oversized khoản, then hard split
        head_len = len(f"{heading} (tiếp)") if heading else 0  # prefix added to continuations
        units: list[tuple[int, int]] = []
        for u_start, u_end in _split_units(body, start, KHOAN_RE):
            if u_end - u_start <= max_size - head_len - 1:
                units.append((u_start, u_end))
                continue
            for p_start, p_end in _split_units(text[u_start:u_end], u_start, POINT_RE):
                if p_end - p_start <= max_size - head_len - 1:
                    units.append((p_start, p_end))
                else:
                    units.extend(_hard_split(stream, p_start, p_end, max_size - head_len - 1))
        # greedy packing of consecutive units
        group_start, group_end = units[0]
        for u_start, u_end in units[1:]:
            if u_end - group_start + head_len + 1 <= max_size:
                group_end = u_end
            else:
                spans.append((group_start, group_end, ""))
                group_start, group_end = u_start, u_end
        spans.append((group_start, group_end, ""))
        # fill text: first group already starts with the heading; others get it prefixed
        for i in range(len(spans) - 1, -1, -1):
            s, e, t = spans[i]
            if t:
                break
            raw = text[s:e].rstrip("\n")
            spans[i] = (s, s + len(raw), raw if s == start or not heading else f"{heading} (tiếp)\n{raw}")
    ordinals = _dedupe_ordinals(spans)
    return [stream.make_chunk(strategy, s, e, t, o) for (s, e, t), o in zip(spans, ordinals)]
