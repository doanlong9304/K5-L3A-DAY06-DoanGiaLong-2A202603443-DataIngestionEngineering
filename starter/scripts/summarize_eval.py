"""Build results/evaluation_summary.md from metrics, manual labels and ingest stats.

Run from starter/ after `python -m src.pipeline evaluate`:
    python scripts/summarize_eval.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results"

# Analysis written after reading the traces; numbers refer to the tables above.
IMPACT = [
    "## 6. Data strategy impact", "",
    "**A → B (đổi chunking, cùng model/store, không lọc).** B tăng Evidence Hit@3 dev từ 12/16 lên 15/16 và Hit@1 dev "
    "9/16 → 11/16; test cả hai 5/5 Hit@3, 4/5 Hit@1. Coherence 1.10 → 2.00, metadata đủ và đúng 10/15 → 15/15, "
    "grounding 1.80 → 2.00. B dùng ít chunk hơn (118 so với 218) và ít token hơn (28.200 so với 29.930). "
    "Lỗi của A: evidence bị tách qua 2 chunk ở q05, q11 (không chunk nào chứa đủ); q15 có đúng trang 27 nhưng "
    "cửa sổ 500 ký tự cắt câu về bất khả kháng; t03 lấy được '24 tháng' nhưng mất điều kiện đi kèm; chunk A trải qua "
    "ranh giới Điều nên metadata article sai (vd. t05 ghi Điều 50 cho evidence của Điều 51). "
    "Đánh đổi: chunk B dài hơn (median 888 so với 497 ký tự), đưa nhiều chữ hơn vào ngữ cảnh mỗi kết quả.", "",
    "**B → C (chỉ thêm filter chapter).** Khác biệt chỉ ở 4 câu có filter: q16 (dev) B trượt vì Điều 29 (Chương IV) "
    "và Điều 4–5 (Chương I) lấn át, C lọc Chương V đưa Điều 38 vào top-3 → Hit@3 dev 15/16 → 16/16; q09 Hit@1 tăng "
    "(B xếp Điều 7 Chương I lên đầu); t01 (test) B xếp Điều 6 lên top-1, C đưa Điều 40 lên top-1 → Hit@1 test 4/5 → 5/5. "
    "Tuân thủ filter của C 3/3 dev, 1/1 test. Chi phí bằng B (cùng chunk/vector, chỉ thêm điều kiện WHERE). "
    "Hạn chế: C phụ thuộc người dùng cung cấp đúng chương; với câu không có filter C = B.", "",
    "**Chọn chiến lược theo tiêu chí định trước** (`configs/evaluation.json`): (1) Evidence Hit@3 test hòa 5/5 cả ba; "
    "(2) grounding test B = C = 2.00 > A = 1.80; (3) Hit@1 test C 5/5 > B 4/5 → **chọn C**, và B là lựa chọn khi "
    "không có thông tin phạm vi chương.", "",
    "**Sai số do mẫu nhỏ:** test chỉ 5 câu, mỗi khác biệt test tương ứng 1 câu (20 điểm %); dev 16 câu, một văn bản "
    "duy nhất. Kết quả chỉ minh họa cho corpus Luật Đầu tư 143/2025/QH15, không suy rộng sang tài liệu khác. "
    "Câu filter được thiết kế có chủ đích là mơ hồ giữa các chương nên phóng đại lợi ích của C so với truy vấn thực tế.",
]


def main() -> None:
    metrics = json.loads((R / "metrics.json").read_text(encoding="utf-8"))
    labels = json.loads((R / "manual_labels.json").read_text(encoding="utf-8"))
    ingest = json.loads((R / "ingest_report.json").read_text(encoding="utf-8"))["runs"][0]  # full ingest
    traces = [json.loads(l) for l in (R / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    S = metrics["summary"]
    strategies = ["A", "B", "C"]
    store_of = {"A": "A", "B": "B", "C": "B"}

    coh = labels["coherence"]["labels"]
    coh["C"] = coh["B"]
    coh_avg = {s: sum(x["score"] for x in coh[s]) / len(coh[s]) for s in strategies}
    n_meta = labels["metadata"]["checked_records_per_strategy"]
    meta_ok = {s: n_meta - len(labels["metadata"]["errors"][s]) for s in strategies}
    gr = labels["grounding"]["labels"]
    gr_dist = {s: {k: sum(x["score"] == k for x in gr[s]) for k in (0, 1, 2)} for s in strategies}
    gr_avg = {s: sum(x["score"] for x in gr[s]) / len(gr[s]) for s in strategies}

    L = [f"# Kết quả đánh giá (top-{metrics['top_k']})", "",
         f"Chạy: {metrics['run_at']} · model `{metrics['embedding_model']}` · trace: `results/traces.jsonl` "
         f"({len(traces)} dòng = 21 câu × 3 cấu hình).", "",
         "## 1. Retrieval accuracy (tự động)", "",
         "| Cấu hình | Hit@1 dev | Evidence Hit@3 dev | Hit@1 test | Evidence Hit@3 test | Tuân thủ filter dev | Tuân thủ filter test |",
         "|---|---|---|---|---|---|---|"]
    for s in strategies:
        d, t = S[s]["dev"], S[s]["test"]
        L.append(f"| {s} | {d['hit_at_1_str']} | {d['hit_at_3_str']} | {t['hit_at_1_str']} | {t['hit_at_3_str']} | "
                 f"{d['filter_compliance_str']} | {t['filter_compliance_str']} |")
    L += ["", "Câu trượt Hit@3 và lỗi evidence bị tách qua nhiều chunk:", ""]
    for s in strategies:
        miss = S[s]["all"]["misses_at_3"]
        split = S[s]["all"]["evidence_split_errors"]
        L.append(f"- **{s}**: trượt {miss or 'không'}; evidence_split {split or 'không'}")
    L += ["", "Filter chỉ áp dụng ở C và chỉ cho câu có filter (q08, q09, q16, t01); câu khác C chạy không lọc. "
          "A/B không lọc nên tuân thủ filter là N/A.", "",
          "## 2. Chunk coherence (thủ công, 10 vị trí, thang 0–2)", "",
          "| Vị trí | Trang | A | B (= C) |", "|---|---|---|---|"]
    for a, b in zip(coh["A"], coh["B"]):
        page = a["position_id"].replace("pos", "")
        L.append(f"| {a['position_id']} | {int(page)} | {a['score']} | {b['score']} |")
    L += [f"| **Trung bình /2** | | **{coh_avg['A']:.2f}** | **{coh_avg['B']:.2f}** |", "",
          "Lý do từng nhãn: `results/manual_labels.json`.", "",
          "## 3. Metadata usefulness (thủ công, 5 câu test × top-3)", "",
          "| Cấu hình | Đủ và đúng / đã kiểm tra | Tuân thủ filter (câu test có filter) |", "|---|---|---|"]
    for s in strategies:
        L.append(f"| {s} | {meta_ok[s]}/{n_meta} | {S[s]['test']['filter_compliance_str']} |")
    L += ["", "Lỗi:"]
    for s in strategies:
        for e in labels["metadata"]["errors"][s]:
            L.append(f"- {s} {e['record']}: {e['error']}")
    L += ["", f"- Hữu ích: {labels['metadata']['usefulness']}", f"- Hạn chế: {labels['metadata']['limitation']}", "",
          "## 4. Grounding quality (thủ công, 5 câu test, top-3 gộp)", "",
          "Đánh giá độ đủ của bằng chứng truy xuất, **không phải** độ chính xác câu trả lời LLM.", "",
          "| Cấu hình | Mức 0 | Mức 1 | Mức 2 | Trung bình /2 | Evidence Hit@3 test (proxy) |", "|---|---|---|---|---|---|"]
    for s in strategies:
        g = gr_dist[s]
        L.append(f"| {s} | {g[0]} | {g[1]} | {g[2]} | {gr_avg[s]:.2f} | {S[s]['test']['hit_at_3_str']} |")
    L += ["", "## 5. Tài nguyên (lần ingest đầy đủ)", "",
          "| Cấu hình | Số chunk | Độ dài min/median/max | Tổng ký tự | Token | Thời gian ingest (s) | Chi phí |",
          "|---|---|---|---|---|---|---|"]
    for s in strategies:
        st = ingest["strategies"][store_of[s]]
        cl = st["char_length"]
        note = " (dùng chung B)" if s == "C" else ""
        L.append(f"| {s} | {st['chunks']}{note} | {cl['min']}/{cl['median']}/{cl['max']} | {st['total_chars']} | "
                 f"{st['total_tokens']} | {st['seconds']['total']} | 0 USD free tier (~{st['cost_usd_if_paid_tier']} USD nếu trả phí) |")
    L += ["", f"Thời gian ingest gồm thời gian chờ retry do HTTP 429 của free tier (xem `results/pipeline.log`). "
          f"Embed 21 câu hỏi: {metrics['query_embedding_calls']} lần gọi API, {metrics['query_embedding_seconds']} s.", ""]
    L += IMPACT + ["", f"Người chấm: {labels['grader']}. {labels.get('method_note', '')} {labels['bias_note']}"]
    (R / "evaluation_summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("written results/evaluation_summary.md")


if __name__ == "__main__":
    main()
