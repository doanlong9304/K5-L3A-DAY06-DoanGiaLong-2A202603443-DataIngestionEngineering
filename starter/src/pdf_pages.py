"""Extract, normalize and classify PDF pages for the corpus manifest.

Extraction uses PyMuPDF (`pip install pymupdf==2.1`). pypdf was compared on the
same file and split some Vietnamese syllables (e.g. "Điề u", "h ệ thống",
"nhân t ạo"), while PyMuPDF did not, so PyMuPDF is the primary reader.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .models import DocumentPage

# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
_INLINE_WS = re.compile(r"[ \t  -​ 　]+")


def normalize_text(text: str) -> str:
    """Unicode NFC + collapse redundant whitespace, keeping line structure.

    - NFC so composed/decomposed Vietnamese diacritics compare equal.
    - Runs of spaces/tabs/NBSP inside a line -> one space; strip each line.
    - Drop empty lines (PDF layout padding). Newlines are kept because
      headings ("Chương", "Mục", "Điều") start on their own line.
    """
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = (_INLINE_WS.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract every physical page (1-based) with normalized text.

    Pages with no extractable text are kept with text="" and
    metadata["empty"]=True so they can be recorded, not silently dropped.
    """
    pages: list[DocumentPage] = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            text = normalize_text(page.get_text("text") or "")
            pages.append(
                DocumentPage(
                    document_id=document_id,
                    source=source,
                    page=index,
                    text=text,
                    metadata={"empty": not text, "image_count": len(page.get_images())},
                )
            )
    return pages


# --------------------------------------------------------------------------
# Page classification (rule-based, specific to Vietnamese legal documents)
# --------------------------------------------------------------------------
_PAGE_NUMBER_LINE = re.compile(r"^\d{1,3}$")  # running page number at top of page
_APPENDIX_HEADING = re.compile(r"^Phụ lục [IVXLC]+$")
_PREAMBLE_START = re.compile(r"^Căn cứ\b")  # national heading on page 1 ends here
_PASSAGE_START = re.compile(r"^Luật này được Quốc hội\b")
_SIGNATURE_MARK = re.compile(r"^CHỦ TỊCH QUỐC HỘI$")
_LAW_STRUCTURE = re.compile(r"^(Chương [IVXLC]+|Mục \d+|Điều \d+\.|\d+\.|[a-zđ]\))", re.M)

MIN_CONTENT_CHARS = 200  # below this, a page is "header only / too little content"


@dataclass
class PageDecision:
    page: int
    included: bool
    reason: str
    chars_page: int  # normalized chars of the whole page (after page-number strip)
    chars_content: int  # normalized chars kept as law content
    content: str
    trimmed: list[dict] = field(default_factory=list)  # parts removed from an included page


def _strip_page_number(page: DocumentPage) -> str:
    lines = page.text.split("\n")
    if lines and _PAGE_NUMBER_LINE.match(lines[0]) and int(lines[0]) == page.page:
        lines = lines[1:]
    return "\n".join(lines)


def classify_pages(pages: list[DocumentPage]) -> list[PageDecision]:
    """Decide for each physical page whether it counts as law content.

    Rules, in order:
      1. no text -> excluded "blank"
      2. a page at/after the first "Phụ lục <roman>" heading -> excluded "appendix"
      3. page 1: drop the national heading block before "Căn cứ ..." (kept page)
      4. a page with "CHỦ TỊCH QUỐC HỘI": drop from "Luật này được Quốc hội ..."
         to the end (passage clause + signature); keep the rest
      5. remaining content < MIN_CONTENT_CHARS or no Chương/Điều/khoản structure
         -> excluded "header_or_signature_only"
    """
    decisions: list[PageDecision] = []
    in_appendix = False
    for page in pages:
        body = _strip_page_number(page)
        chars_page = len(body)
        lines = body.split("\n") if body else []

        if not body:
            decisions.append(PageDecision(page.page, False, "blank", 0, 0, ""))
            continue
        if not in_appendix and any(_APPENDIX_HEADING.match(l) for l in lines[:3]):
            in_appendix = True
        if in_appendix:
            decisions.append(PageDecision(page.page, False, "appendix", chars_page, 0, ""))
            continue

        trimmed: list[dict] = []
        start = 0
        if page.page == 1:
            idx = next((i for i, l in enumerate(lines) if _PREAMBLE_START.match(l)), None)
            if idx:
                trimmed.append({"part": "national_heading", "text": "\n".join(lines[:idx])})
                start = idx
        end = len(lines)
        if any(_SIGNATURE_MARK.match(l) for l in lines):
            idx = next((i for i, l in enumerate(lines) if _PASSAGE_START.match(l)), None)
            if idx is None:
                idx = next(i for i, l in enumerate(lines) if _SIGNATURE_MARK.match(l))
            trimmed.append({"part": "passage_and_signature", "text": "\n".join(lines[idx:])})
            end = idx

        content = "\n".join(lines[start:end])
        if len(content) < MIN_CONTENT_CHARS or not _LAW_STRUCTURE.search(content):
            decisions.append(
                PageDecision(page.page, False, "header_or_signature_only", chars_page, 0, "", trimmed)
            )
            continue
        decisions.append(
            PageDecision(page.page, True, "law_content", chars_page, len(content), content, trimmed)
        )
    return decisions
