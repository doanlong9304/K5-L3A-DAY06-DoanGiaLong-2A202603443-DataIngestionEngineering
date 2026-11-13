"""Verify data/benchmark.json against the normalized corpus before any experiment.

Run from starter/:  python scripts/verify_benchmark.py
Checks: required fields; >=20 questions, >=15 dev, >=5 test; >=3 filter questions
with >=1 in test; each evidence_quote occurs on its gold page(s) after collapsing
whitespace (and reports any other page containing it); the filter's chapter equals
the chapter in force at the evidence position. Exits non-zero on any failure.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "benchmark.json"
PAGES = ROOT / "data" / "processed" / "luat143-2025.pages.jsonl"
REQUIRED = ["id", "split", "question", "expected_answer", "gold_source_id", "gold_page", "evidence_quote"]
CHAPTER = re.compile(r"^Chương ([IVXLC]+)$", re.M)


def flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in PAGES.read_text(encoding="utf-8").splitlines()]
    page_text = {r["page"]: r["text"] for r in rows}
    source_ids = {r["document_id"] for r in rows}

    # chapter in force at the start of each page, and chapter headings inside it
    chapter_at_start, current = {}, None
    for p in sorted(page_text):
        chapter_at_start[p] = current
        for m in CHAPTER.finditer(page_text[p]):
            current = m.group(1)

    def chapter_at(page: int, quote: str) -> str | None:
        text = page_text[page]
        pos = flat(text).find(flat(quote))
        chap = chapter_at_start[page]
        for m in CHAPTER.finditer(text):
            if len(flat(text[: m.start()])) <= pos:
                chap = m.group(1)
        return chap

    errors: list[str] = []
    qs = bench["questions"]
    ids = [q.get("id") for q in qs]
    if len(ids) != len(set(ids)):
        errors.append("duplicate ids")
    for q in qs:
        missing = [k for k in REQUIRED if k not in q]
        if missing:
            errors.append(f"{q.get('id')}: missing {missing}")
            continue
        if q["gold_source_id"] not in source_ids:
            errors.append(f"{q['id']}: unknown gold_source_id")
        quote = flat(q["evidence_quote"])
        found = [p for p, t in page_text.items() if quote in flat(t)]
        on_gold = [p for p in q["gold_page"] if p in found]
        if not on_gold:
            errors.append(f"{q['id']}: evidence not on gold page {q['gold_page']} (found on {found})")
            continue
        extra = sorted(set(found) - set(q["gold_page"]))
        if extra:
            errors.append(f"{q['id']}: evidence also on pages {extra}; add them to gold_page")
        if "filter" in q:
            want = q["filter"].get("chapter")
            got = chapter_at(on_gold[0], q["evidence_quote"])
            if want != got:
                errors.append(f"{q['id']}: filter chapter={want} but evidence is in chapter {got}")

    n_dev = sum(q["split"] == "dev" for q in qs)
    n_test = sum(q["split"] == "test" for q in qs)
    n_filter = sum("filter" in q for q in qs)
    n_filter_test = sum("filter" in q and q["split"] == "test" for q in qs)
    for ok, msg in [(len(qs) >= 20, "need >= 20 questions"), (n_dev >= 15, "need >= 15 dev"),
                    (n_test >= 5, "need >= 5 test"), (n_filter >= 3, "need >= 3 filter questions"),
                    (n_filter_test >= 1, "need >= 1 filter question in test")]:
        if not ok:
            errors.append(msg)

    types: dict[str, int] = {}
    for q in qs:
        types[q.get("type", "?")] = types.get(q.get("type", "?"), 0) + 1
    print(f"questions={len(qs)} dev={n_dev} test={n_test} filter={n_filter} (test {n_filter_test}) types={types}")
    if errors:
        print("FAILED:\n  " + "\n  ".join(errors))
        sys.exit(1)
    print("Benchmark OK: every evidence_quote verified on its gold page.")


if __name__ == "__main__":
    main()
