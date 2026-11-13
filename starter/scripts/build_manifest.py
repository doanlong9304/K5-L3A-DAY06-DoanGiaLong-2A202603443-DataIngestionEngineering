"""Download (if missing), extract, classify and write the corpus manifest.

Run from starter/ (extra dependency, not in the starter requirements.txt):
    pip install pymupdf==2.1
    python scripts/build_manifest.py

Outputs:
    data/raw/luat143-2025.pdf            original file, never modified
    data/manifest.json                   corpus manifest (incl. accessed_at, sha256)
    data/processed/luat143-2025.pages.jsonl  normalized content of counted pages,
                                         used to verify benchmark evidence quotes
Re-running reuses the downloaded file (sha256-checked against the manifest)
and the original accessed_at date, so the manifest is reproducible.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pdf_pages import MIN_CONTENT_CHARS, classify_pages, load_pdf  # noqa: E402

SOURCE_ID = "luat-dau-tu-143-2025-qh15"
SOURCE_URL = "https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/01/luat143-2025.pdf"
PDF_PATH = ROOT / "data" / "raw" / "luat143-2025.pdf"
MANIFEST_PATH = ROOT / "data" / "manifest.json"
PAGES_PATH = ROOT / "data" / "processed" / "luat143-2025.pages.jsonl"
LICENSE = (
    "Văn bản quy phạm pháp luật – không thuộc đối tượng bảo hộ quyền tác giả "
    "theo Luật Sở hữu trí tuệ Việt Nam; nguồn chính thức chinhphu.vn"
)
MIN_COUNTED_PAGES = 30
REASON_TEXT = {
    "blank": "Trang trống: không có text, không có ảnh (không cần OCR)",
    "appendix": "Trang Phụ lục: bảng danh mục bị trải phẳng khi trích xuất, mất cấu trúc cột",
    "header_or_signature_only": "Chỉ có quốc hiệu/tiêu đề hoặc chữ ký, ít nội dung",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_download() -> dict:
    """Reuse the PDF + accessed_at recorded in an existing manifest; else download."""
    if PDF_PATH.exists() and MANIFEST_PATH.exists():
        old = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if sha256(PDF_PATH) != old["file"]["sha256"]:
            raise SystemExit(f"{PDF_PATH} differs from sha256 in manifest; refusing to continue.")
        return {"accessed_at": old["accessed_at"], "sha256": old["file"]["sha256"],
                "bytes": old["file"]["bytes"]}
    PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as resp:
        if resp.status != 200:
            raise SystemExit(f"Download failed: HTTP {resp.status}")
        PDF_PATH.write_bytes(resp.read())
    return {
        "accessed_at": datetime.now(timezone.utc).astimezone().date().isoformat(),
        "sha256": sha256(PDF_PATH),
        "bytes": PDF_PATH.stat().st_size,
    }


def article_text(full_text: str, number: int) -> str:
    """Text of 'Điều <number>.' up to the next article heading."""
    m = re.search(rf"^Điều {number}\..*?(?=^Điều {number + 1}\.|\Z)", full_text, re.M | re.S)
    if not m:
        raise SystemExit(f"Không tìm thấy Điều {number} trong văn bản.")
    return m.group(0)


def effective_dates(full_text: str) -> dict:
    """Verbatim clauses 1-3 of Điều 51 (no guessing); line breaks joined with spaces."""
    art = article_text(full_text, 51)
    out = {"article": "Điều 51. Hiệu lực thi hành"}
    for k in (1, 2, 3):
        m = re.search(rf"^{k}\. (.*?)(?=^{k + 1}\. |\Z)", art, re.M | re.S)
        if not m:
            raise SystemExit(f"Không tìm thấy khoản {k} Điều 51.")
        out[f"khoan_{k}"] = re.sub(r"\s+", " ", f"{k}. {m.group(1)}").strip()
    return out


def main() -> None:
    record = ensure_download()
    rel_pdf = PDF_PATH.relative_to(ROOT).as_posix()
    pages = load_pdf(PDF_PATH, SOURCE_ID, rel_pdf)
    decisions = classify_pages(pages)
    included = [d for d in decisions if d.included]
    excluded = [d for d in decisions if not d.included]

    if len(included) < MIN_COUNTED_PAGES:
        raise SystemExit(f"Chỉ có {len(included)} trang được tính (< {MIN_COUNTED_PAGES}). Dừng.")

    full_text = "\n".join(d.content for d in included)
    header = pages[0].text
    number = re.search(r"Luật số: (\S+)", header)
    passage = next(
        (t["text"] for d in decisions for t in d.trimmed if t["part"] == "passage_and_signature"), ""
    )
    passed = re.search(r"thông qua ngày (\d{1,2} tháng \d{1,2} năm \d{4})", passage.replace("\n", " "))

    with pymupdf.open(PDF_PATH) as doc:
        pdf_meta = {k: v for k, v in doc.metadata.items() if v}

    manifest = {
        "source_id": SOURCE_ID,
        "title": "Luật Đầu tư",
        "document_type": "Luật",
        "version": number.group(1) if number else None,
        "issuer": "Quốc hội khóa XV, Kỳ họp thứ 10",
        "passed_date_verbatim": passed.group(1) if passed else None,
        "effective_date_verbatim": effective_dates(full_text),
        "source_path_or_url": {"path": rel_pdf, "url": SOURCE_URL},
        "license_or_permission": LICENSE,
        "accessed_at": record["accessed_at"],
        "file": {"sha256": record["sha256"], "bytes": record["bytes"], "pdf_metadata": pdf_meta},
        "extraction": {
            "library": f"PyMuPDF {pymupdf.__version__}",
            "normalization": "Unicode NFC; gộp khoảng trắng/NBSP trong dòng; strip dòng; bỏ dòng trống; "
            "bỏ dòng số trang ở đầu trang",
            "ocr": "Không dùng (PDF có lớp text; trang trống không chứa ảnh)",
        },
        "page_counting_method": (
            "Đếm trang vật lý (1-based) theo PDF. Trang được tính là trang có nội dung Chương/Điều "
            "của luật, sau khi loại phần quốc hiệu (trang 1, trước 'Căn cứ...') và phần thông qua + "
            f"chữ ký (từ 'Luật này được Quốc hội...'), còn >= {MIN_CONTENT_CHARS} ký tự. Loại: trang "
            "trống, trang Phụ lục (từ dòng 'Phụ lục <số La Mã>' đến hết), trang chỉ có tiêu đề/chữ ký. "
            "Số ký tự = len() của text đã chuẩn hóa (ký tự Unicode, gồm cả ký tự xuống dòng)."
        ),
        "physical_pages": len(pages),
        "counted_page_count": len(included),
        "excluded_page_count": len(excluded),
        "counted_pages": [
            {
                "page_id": f"{SOURCE_ID}:p{d.page:03d}",
                "page": d.page,
                "chars_page": d.chars_page,
                "chars_content": d.chars_content,
                **({"trimmed": [{"part": t["part"], "chars": len(t["text"])} for t in d.trimmed]}
                   if d.trimmed else {}),
            }
            for d in included
        ],
        "excluded_pages": [
            {"page": d.page, "reason": d.reason, "reason_text": REASON_TEXT[d.reason],
             "chars_page": d.chars_page}
            for d in excluded
        ],
        "totals": {
            "chars_content_counted": sum(d.chars_content for d in included),
            "chars_all_pages": sum(d.chars_page for d in decisions),
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    PAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PAGES_PATH.open("w", encoding="utf-8") as f:
        for d in included:
            f.write(json.dumps({"page_id": f"{SOURCE_ID}:p{d.page:03d}", "document_id": SOURCE_ID,
                                "source": rel_pdf, "page": d.page, "chars": d.chars_content,
                                "text": d.content}, ensure_ascii=False) + "\n")

    print(f"Physical pages: {len(pages)} | counted: {len(included)} | excluded: {len(excluded)}")
    print(f"Manifest: {MANIFEST_PATH.relative_to(ROOT)} | pages: {PAGES_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
