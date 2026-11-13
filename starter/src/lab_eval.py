"""Automatic retrieval scoring (Hit@1, Evidence Hit@3, filter compliance) and traces.

Manual labels (coherence, metadata correctness, grounding) are NOT inferred here;
they live in results/manual_labels.json and are only aggregated.
"""
from __future__ import annotations

import re
from typing import Any


def flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def is_hit(question: dict[str, Any], hit: dict[str, Any]) -> bool:
    m = hit["metadata"]
    return (m["document_id"] == question["gold_source_id"]
            and any(m["page"] <= p <= m["page_end"] for p in question["gold_page"])
            and flat(question["evidence_quote"]) in flat(hit["text"]))


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]], filters: dict | None) -> dict[str, Any]:
    flags = [is_hit(question, h) for h in hits]
    quote = flat(question["evidence_quote"])
    gold_page_hits = [h for h in hits if any(h["metadata"]["page"] <= p <= h["metadata"]["page_end"]
                                             for p in question["gold_page"])]
    split = not any(flags) and _split_across(quote, gold_page_hits)
    errors = ["evidence_split"] if split else []
    compliance = None
    if filters:
        compliance = all(all(h["metadata"].get(k) == v for k, v in filters.items()) for h in hits)
    return {
        "hit_at_1": bool(flags and flags[0]),
        "hit_at_3": any(flags),
        "hit_ranks": [i + 1 for i, f in enumerate(flags) if f],
        "gold_page_in_top3": bool(gold_page_hits),
        "evidence_union_top3": any(flags) or split,  # supplementary multi-chunk view
        "filter_compliant": compliance,
        "errors": errors,
    }


def _split_across(quote: str, hits: list[dict[str, Any]], probe: int = 30) -> bool:
    """Quote not inside one chunk, but its head is in one gold-page chunk and its tail in another."""
    if len(hits) < 2 or len(quote) < 2 * probe:
        return False
    texts = [flat(h["text"]) for h in hits]
    head, tail = quote[:probe], quote[-probe:]
    return any(head in a and tail in b for i, a in enumerate(texts) for j, b in enumerate(texts) if i != j)


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for t in traces:
        for split in (t["split"], "all"):
            s = out.setdefault(t["strategy"], {}).setdefault(split, {
                "n": 0, "hit_at_1": 0, "hit_at_3": 0, "filter_questions": 0, "filter_compliant": 0,
                "evidence_split_errors": [], "misses_at_3": []})
            s["n"] += 1
            s["hit_at_1"] += t["eval"]["hit_at_1"]
            s["hit_at_3"] += t["eval"]["hit_at_3"]
            if t["eval"]["filter_compliant"] is not None:
                s["filter_questions"] += 1
                s["filter_compliant"] += t["eval"]["filter_compliant"]
            if "evidence_split" in t["eval"]["errors"]:
                s["evidence_split_errors"].append(t["id"])
            if not t["eval"]["hit_at_3"]:
                s["misses_at_3"].append(t["id"])
    for strat in out.values():
        for s in strat.values():
            n = s["n"]
            s["hit_at_1_str"] = f"{s['hit_at_1']}/{n}"
            s["hit_at_3_str"] = f"{s['hit_at_3']}/{n}"
            s["filter_compliance_str"] = (f"{s['filter_compliant']}/{s['filter_questions']}"
                                          if s["filter_questions"] else "N/A")
    return out
