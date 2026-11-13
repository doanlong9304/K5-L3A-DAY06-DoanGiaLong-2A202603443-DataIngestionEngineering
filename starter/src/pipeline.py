"""Day 06 pipeline: ingest strategies A/B(/C) into pgvector and search them.

Run from starter/:
    python -m src.pipeline ingest              # chunk + embed + upsert A and B, write stats
    python -m src.pipeline evaluate            # all benchmark questions x A/B/C -> results/
    python -m src.pipeline search -s C --chapter V "Ngành, nghề nào bị cấm đầu tư?"
C reuses B's stored chunks/vectors and adds the SQL chapter filter when given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .gemini_embedder import GeminiEmbedder
from .lab_chunking import Stream, fixed_size, structure
from .lab_eval import evaluate_query, summarize
from .pdf_pages import classify_pages, load_pdf
from .pg_store import PgVectorStore

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "strategies.json"
RESULTS = ROOT / "results"
log = logging.getLogger("pipeline")


def setup_logging() -> None:
    RESULTS.mkdir(exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[
        logging.FileHandler(RESULTS / "pipeline.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr)])  # stdout stays clean JSON for `search`
    for noisy in ("httpx", "httpx2", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def make_embedder(cfg: dict) -> GeminiEmbedder:
    e = cfg["embedding"]
    return GeminiEmbedder(e["model"], e["dimensions"], e["batch_size"],
                          e["document_task_type"], e["query_task_type"])


def make_store(cfg: dict) -> PgVectorStore:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL missing in starter/.env")
    return PgVectorStore(url, cfg["embedding"]["dimensions"], cfg["embedding"]["model"])


def build_stream(cfg: dict) -> tuple[Stream, str, int]:
    corpus = cfg["corpus"]
    pdf = ROOT / corpus["pdf"]
    manifest = json.loads((ROOT / corpus["manifest"]).read_text(encoding="utf-8"))
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if sha != manifest["file"]["sha256"]:
        raise RuntimeError("PDF does not match manifest sha256; rebuild the manifest first.")
    pages = load_pdf(pdf, corpus["document_id"], corpus["pdf"])
    included = [d for d in classify_pages(pages) if d.included]
    if len(included) < 30:
        raise RuntimeError(f"only {len(included)} content pages (< 30)")
    if [d.page for d in included] != [p["page"] for p in manifest["counted_pages"]]:
        raise RuntimeError("counted pages differ from manifest")
    return Stream.build(corpus["document_id"], corpus["pdf"], [(d.page, d.content) for d in included]), sha, len(included)


def chunk_strategy(stream: Stream, name: str, spec: dict):
    if spec["chunker"] == "fixed_size":
        return fixed_size(stream, name, spec["size"], spec["overlap"])
    if spec["chunker"] == "structure":
        return structure(stream, name, spec["max_size"])
    raise ValueError(f"unknown chunker {spec['chunker']}")


def length_stats(lengths: list[int]) -> dict:
    q = statistics.quantiles(lengths, n=4)
    return {"min": min(lengths), "p25": q[0], "median": statistics.median(lengths), "p75": q[2],
            "max": max(lengths), "mean": round(statistics.mean(lengths), 1)}


def cmd_ingest(_: argparse.Namespace) -> None:
    cfg = load_config()
    stream, doc_version, n_pages = build_stream(cfg)
    embedder, store = make_embedder(cfg), make_store(cfg)
    price = cfg["embedding"].get("price_per_1m_tokens_usd")
    report = {"run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "embedding": cfg["embedding"], "store": cfg["store"], "doc_version": doc_version,
              "content_pages": n_pages, "strategies": {}}
    try:
        store.create_schema()
        stored = {s["store_as"] for s in cfg["strategies"].values()}
        for name in sorted(stored):
            spec = cfg["strategies"][name]
            t0 = time.perf_counter()
            chunks = chunk_strategy(stream, name, spec)
            t_chunk = time.perf_counter() - t0
            todo = store.needs_embedding(name, chunks)
            calls_before = embedder.api_calls
            t1 = time.perf_counter()
            vectors = dict(zip([c.chunk_id for c in todo], embedder.embed_documents([c.text for c in todo])))
            t_embed = time.perf_counter() - t1
            t2 = time.perf_counter()
            result = store.upsert(name, chunks, vectors, doc_version)
            t_db = time.perf_counter() - t2
            rows = store.count(name)
            if rows != len(chunks):
                raise RuntimeError(f"{name}: {rows} rows stored for {len(chunks)} chunks")
            tokens_all = embedder.count_tokens([c.text for c in chunks])
            tokens_embedded = embedder.count_tokens([c.text for c in todo]) if todo else 0
            lengths = [len(c.text) for c in chunks]
            report["strategies"][name] = {
                "spec": spec, "chunks": len(chunks), "rows_in_store": rows,
                "char_length": length_stats(lengths), "total_chars": sum(lengths),
                "total_tokens": tokens_all, "tokens_embedded_this_run": tokens_embedded,
                "chunks_embedded_this_run": len(todo), "embedding_api_calls": embedder.api_calls - calls_before,
                "write": result,
                "seconds": {"chunking": round(t_chunk, 3), "embedding": round(t_embed, 2),
                            "db_write": round(t_db, 3), "total": round(t_chunk + t_embed + t_db, 2)},
                "cost_usd_if_paid_tier": round(tokens_embedded / 1e6 * price, 6) if price is not None else None,
            }
            log.info("%s: %d chunks, embedded %d, %s, %.1fs", name, len(chunks), len(todo), result,
                     t_chunk + t_embed + t_db)
    except Exception:
        log.exception("ingest failed")
        raise
    finally:
        store.close()
    out = RESULTS / "ingest_report.json"  # every run is appended: first = full ingest, later = re-ingests
    runs = json.loads(out.read_text(encoding="utf-8"))["runs"] if out.exists() else []
    out.write_text(json.dumps({"runs": runs + [report]}, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("run %d appended to results/ingest_report.json", len(runs) + 1)


def cmd_evaluate(_: argparse.Namespace) -> None:
    """Run every benchmark question on A, B, C; save top-k traces, metrics and manual-review samples."""
    cfg = load_config()
    ecfg = json.loads((ROOT / "configs" / "evaluation.json").read_text(encoding="utf-8"))
    questions = json.loads((ROOT / ecfg["benchmark"]).read_text(encoding="utf-8"))["questions"]
    stream, doc_version, _ = build_stream(cfg)
    embedder, store = make_embedder(cfg), make_store(cfg)
    top_k = ecfg["top_k"]
    traces = []
    try:
        t0 = time.perf_counter()
        qvecs = embedder.embed_queries([q["question"] for q in questions])  # same query vectors for A/B/C
        t_embed = time.perf_counter() - t0
        for name, spec in cfg["strategies"].items():
            for q, qv in zip(questions, qvecs):
                filters = q.get("filter") if spec["filter"] else None
                t1 = time.perf_counter()
                hits = store.search(spec["store_as"], qv, top_k, filters)
                ms = (time.perf_counter() - t1) * 1000
                traces.append({
                    "strategy": name, "id": q["id"], "split": q["split"], "type": q.get("type"),
                    "question": q["question"], "filter_in_question": q.get("filter"),
                    "filter_applied": filters, "gold": {k: q[k] for k in
                                                        ("gold_source_id", "gold_page", "evidence_quote", "expected_answer")},
                    "top_k": [{"rank": i + 1, **h} for i, h in enumerate(hits)],
                    "search_ms": round(ms, 1), "eval": evaluate_query(q, hits, filters)})
        # coherence samples: chunk covering the midpoint of each of the first 10 counted pages
        positions = []
        for i in range(10):
            start = stream.page_starts[i]
            end = stream.page_starts[i + 1] - 1 if i + 1 < len(stream.page_starts) else len(stream.text)
            positions.append({"position_id": f"pos{i + 1:02d}", "page": stream.page_numbers[i],
                              "offset": (start + end) // 2})
        coherence = []
        for pos in positions:
            row = {**pos, "source_excerpt": stream.text[pos["offset"] - 60: pos["offset"] + 60]}
            for name in sorted({s["store_as"] for s in cfg["strategies"].values()}):
                row[name] = store.chunk_at(name, stream.document_id, pos["offset"])
            coherence.append(row)
    finally:
        store.close()

    metrics = {"run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "embedding_model": cfg["embedding"]["model"], "doc_version": doc_version, "top_k": top_k,
               "query_embedding_seconds": round(t_embed, 2), "query_embedding_calls": embedder.api_calls,
               "summary": summarize(traces)}
    with (RESULTS / "traces.jsonl").open("w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (RESULTS / "coherence_samples.json").write_text(json.dumps(coherence, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
    for name, splits in metrics["summary"].items():
        for split in ("dev", "test"):
            s = splits[split]
            log.info("%s %-4s Hit@1 %s  Hit@3 %s  filter %s  misses %s  split-evidence %s", name, split,
                     s["hit_at_1_str"], s["hit_at_3_str"], s["filter_compliance_str"], s["misses_at_3"],
                     s["evidence_split_errors"])


def cmd_search(args: argparse.Namespace) -> None:
    cfg = load_config()
    spec = cfg["strategies"][args.strategy]
    filters = {"chapter": args.chapter} if (spec["filter"] and args.chapter) else None
    if args.chapter and not spec["filter"]:
        log.info("strategy %s does not use filters; --chapter ignored", args.strategy)
    embedder, store = make_embedder(cfg), make_store(cfg)
    try:
        hits = store.search(spec["store_as"], embedder.embed_query(args.question), args.top_k, filters)
    finally:
        store.close()
    print(json.dumps({"strategy": args.strategy, "filter": filters, "hits": hits}, ensure_ascii=False, indent=2))


def main() -> None:
    load_dotenv(ROOT / ".env")  # does not override variables already set in the environment
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").set_defaults(func=cmd_ingest)
    sub.add_parser("evaluate").set_defaults(func=cmd_evaluate)
    s = sub.add_parser("search")
    s.add_argument("question")
    s.add_argument("-s", "--strategy", choices=["A", "B", "C"], default="C")
    s.add_argument("--chapter")
    s.add_argument("-k", "--top-k", type=int, default=3)
    s.set_defaults(func=cmd_search)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
