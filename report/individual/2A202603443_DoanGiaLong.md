# Báo cáo cá nhân — Day 6

Học viên: Đoàn Gia Long · MSSV 2A202603443 · Corpus: Luật Đầu tư số 143/2025/QH15

## Dữ liệu và pipeline
- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:** Luật Đầu tư số 143/2025/QH15, PDF chính thức từ Cổng TTĐT Chính phủ (https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/01/luat143-2025.pdf), tải ngày 2026-11-13, sha256 `567993cf…64a6cb`. Quyền: văn bản quy phạm pháp luật, không thuộc đối tượng bảo hộ quyền tác giả theo Luật SHTT; nguồn chính thức chinhphu.vn. PDF có 76 trang vật lý; **tính 43 trang (1–43)** là trang có nội dung Chương/Điều. Loại trang 44 (trống, không có ảnh) và trang 45–76 (Phụ lục I–IV, bảng bị trải phẳng khi trích xuất). Trang 1 bỏ phần quốc hiệu/tiêu đề (106 ký tự), trang 43 bỏ câu thông qua và chữ ký (155 ký tự). Chi tiết từng trang và số ký tự: [starter/data/manifest.json](../../starter/data/manifest.json).
- **Chuẩn hóa, lỗi và giới hạn:** đọc bằng PyMuPDF 2.1, vì pypdf 6.21.4 tách sai âm tiết tiếng Việt ("Điề u", "h ệ thống", "nhân t ạo"; 5 lỗi trên 43 trang), còn PyMuPDF không có lỗi này. Chuẩn hóa: Unicode NFC, gộp khoảng trắng/NBSP trong dòng, bỏ dòng trống và dòng số trang; giữ xuống dòng để nhận tiêu đề Chương/Mục/Điều. Không cần OCR. Giới hạn: không đưa Phụ lục vào corpus nên không trả lời được câu hỏi tra danh mục (ví dụ danh mục ngành, nghề kinh doanh có điều kiện ở Phụ lục IV). Lỗi provider: HTTP 429 của Gemini free tier khi ingest, được xử lý bằng retry có backoff và ghi trong [starter/results/pipeline.log](../../starter/results/pipeline.log).
- **Embedding, vector store, lệnh chạy và chi phí:**
  - Embedding: Google Gemini API `gemini-embedding-001` (SDK google-genai 3.3.0), 768 chiều; document dùng task `RETRIEVAL_DOCUMENT`, query dùng `RETRIEVAL_QUERY`. Giữ nguyên cho A/B/C.
  - Vector store: PostgreSQL 16.16 + pgvector 0.8.7 (Docker), bảng `lab_chunks`, metric cosine (`<=>`), tìm kiếm chính xác (exact, không dùng index ANN).
  - Chi phí: 0 USD (free tier), tổng 58.130 token embed tài liệu. Nếu tính theo giá trả phí tham khảo 0,15 USD/1M token thì khoảng 0,009 USD (giá này cần đối chiếu trang giá Gemini).
  - Môi trường: Python 3.12.9, psycopg 3.3.8. Lệnh chạy: xem mục *Tái lập*.
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:**
  - Bắt buộc: `document_id`, `source`, `page`, `page_end` (int; chunk có thể vắt qua hai trang).
  - Bổ sung: `chapter` (text, I–VII), `chapter_title`, `section` (Mục), `article` (int), `char_start`/`char_end`.
  - Nguồn giá trị: dòng tiêu đề "Chương …", "Mục …", "Điều N." trong văn bản. Mỗi chunk lấy giá trị theo vị trí đầu chunk, không theo trang, vì các trang 8, 10, 14, 29, 31, 38 có ranh giới chương.
  - Lý do chọn `chapter` làm trường lọc: nhiều điều có nội dung tương tự ở chương khác nhau, ví dụ Điều 6 với Điều 40 (ngành, nghề cấm), Điều 7 với Điều 41 (ngành, nghề có điều kiện), Điều 18 với Điều 39 (hình thức đầu tư).

## Cấu hình và benchmark
- **A / B / C** (tham số trong [starter/configs/strategies.json](../../starter/configs/strategies.json)):
  - **A:** chia cố định 500 ký tự, overlap 50, không vượt ranh giới trang, lùi điểm cắt về khoảng trắng; không lọc.
  - **B:** chia theo cấu trúc Điều → khoản → điểm, tối đa 1.200 ký tự; chunk tiếp nối lặp dòng "Điều N. … (tiếp)"; không lọc.
  - **C:** dùng chung chunk và vector với B, thêm `WHERE chapter = …` khi câu hỏi có filter.
  - **Khác biệt duy nhất giữa B và C** là filter. Giữa A và B chỉ đổi cách chia chunk. Cùng corpus, model, store và benchmark.
- **≥20 câu, phân chia, câu filter và cách kiểm chứng evidence:**
  - 21 câu ([starter/data/benchmark.json](../../starter/data/benchmark.json)): 16 dev / 5 test. Chia theo Điều trước khi chạy thử: câu test hỏi về các Điều không có trong dev.
  - Loại câu: 5 trực tiếp, 5 diễn đạt lại, 7 điều kiện/ngoại lệ, 4 metadata filter (q08, q09, q16 thuộc dev; t01 thuộc test).
  - Filter là phạm vi chương do người dùng cung cấp trước khi truy xuất, không lấy từ gold.
  - Kiểm chứng evidence: [starter/scripts/verify_benchmark.py](../../starter/scripts/verify_benchmark.py) xác nhận mỗi `evidence_quote` nằm đúng trang gold sau chuẩn hóa (và không nằm ở trang khác), và giá trị filter khớp với chương tại vị trí evidence.
  - Nguy cơ rò rỉ: chỉ có một văn bản; Điều 40 (test) dẫn chiếu Điều 6 (dev).
- **Tiêu chí chọn chiến lược đã chốt trước khi xem test** ([starter/configs/evaluation.json](../../starter/configs/evaluation.json)): ưu tiên Evidence Hit@3, sau đó grounding, Hit@1, coherence, rồi tài nguyên. File này ghi các chỉ số trên tập test; theo README, việc chọn cấu hình nên dựa trên dev. Áp dụng cùng thứ tự cho dev cũng ra C (Hit@3 dev 16/16 > 15/16 > 12/16), nên lựa chọn không đổi. Không có thay đổi chiến lược nào sau khi xem test.

## Kết quả
| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 9/16 | 12/16 | 218 | ingest 80,96 s; search TB 6,9 ms | 0 USD (≈0,0045 USD nếu trả phí) |
| A | test | 4/5 | 5/5 | 218 | | |
| B | dev | 11/16 | 15/16 | 118 | ingest 117,4 s; search TB 5,0 ms | 0 USD (≈0,0042 USD nếu trả phí) |
| B | test | 4/5 | 5/5 | 118 | | |
| C | dev | 12/16 | 16/16 | 118 (chung B) | không ingest thêm; search TB 4,0 ms | 0 USD thêm |
| C | test | 5/5 | 5/5 | 118 (chung B) | | |

Thời gian ingest gồm cả thời gian chờ retry do HTTP 429, nên không phản ánh tốc độ thực của từng chiến lược. Embed 21 câu hỏi: 1 lần gọi API, 1,35 s, dùng chung cho cả ba cấu hình.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 1,10 (n=10) | 10/15 | N/A | 0/1/4 · 1,80 |
| B | 2,00 (n=10) | 15/15 | N/A | 0/0/5 · 2,00 |
| C | 2,00 (n=10, chung chunk B) | 15/15 | dev 3/3, test 1/1 | 0/0/5 · 2,00 |

**Đường dẫn trace, nhãn thủ công và cách chọn mẫu:**
- Trace top-3: [starter/results/traces.jsonl](../../starter/results/traces.jsonl) (63 dòng = 21 câu × 3 cấu hình), gồm câu hỏi, ID và text chunk, score, metadata, filter đã áp dụng, gold/evidence và nhãn hit.
- Chỉ số tự động: [metrics.json](../../starter/results/metrics.json). Bảng tổng hợp: [evaluation_summary.md](../../starter/results/evaluation_summary.md).
- Nhãn thủ công kèm lý do: [manual_labels.json](../../starter/results/manual_labels.json).
- Mẫu coherence: [coherence_samples.json](../../starter/results/coherence_samples.json), 10 vị trí cố định chọn trước khi chấm (điểm giữa nội dung của trang 1–10), chấm chunk bao phủ từng vị trí.
- Metadata và grounding: chấm trên 5 câu test × top-3.

## Phân tích và kết luận
- **So sánh A–B và B–C; 2 ví dụ sai có ID câu và nguồn:**
  - **A → B:** Hit@3 dev 12/16 → 15/16, Hit@1 dev 9/16 → 11/16; coherence 1,10 → 2,00; metadata 10/15 → 15/15; grounding 1,80 → 2,00; số chunk giảm từ 218 xuống 118. Trên test, Hit@3 và Hit@1 bằng nhau (5/5, 4/5).
  - **B → C:** chỉ khác ở các câu có filter. Hit@3 dev 15/16 → 16/16 (q16); Hit@1 test 4/5 → 5/5 (t01).
  - **Ví dụ 1 — q05 (dev, trang 10, Điều 13 khoản 3), A trượt:** evidence (khoản 3 kèm danh sách trọng tài) bị tách qua hai chunk 500 ký tự, nên không chunk nào chứa đủ evidence. B giữ trọn khoản 3 trong một chunk nên trúng.
  - **Ví dụ 2 — t01 (test, trang 30, Điều 40), B xếp sai top-1:** câu "Những ngành, nghề nào bị cấm đầu tư?" không lọc thì Điều 6 (Chương I) đứng hạng 1, Điều 40 chỉ hạng 2. C lọc Chương V đưa Điều 40 lên top-1.
  - **Ví dụ thêm — q16 (dev, trang 29, Điều 38):** B xếp Điều 29 (Chương IV) và Điều 4–5 (Chương I) lên trên, nên Điều 38 không vào top-3.
  - **Lỗi metadata của A:** chunk vắt qua ranh giới hai Điều nhưng `article` chỉ ghi Điều đầu. Ví dụ ở t05, chunk ghi Điều 50 nhưng evidence thuộc Điều 51.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:** chọn **C**. Hit@3 test ba cấu hình hòa (5/5); grounding B = C > A; Hit@1 test C > B; trên dev C cũng cao nhất. C không tốn thêm tài nguyên so với B, nhưng chỉ có ích khi người dùng hoặc ngữ cảnh cho biết phạm vi chương; không có filter thì C giống B. Đánh đổi của B/C so với A: chunk dài hơn (median 888 so với 497 ký tự), nên đưa nhiều chữ hơn vào ngữ cảnh mỗi kết quả, và cách chia phụ thuộc cấu trúc riêng của văn bản luật (Điều/khoản/điểm).
- **Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí:**
  - Một văn bản, 43 trang, bỏ Phụ lục.
  - Test chỉ 5 câu: mỗi khác biệt bằng 1 câu (20 điểm %), không đủ để kết luận thống kê. Không suy rộng ra ngoài corpus này.
  - Câu filter được thiết kế mơ hồ giữa các chương nên có thể phóng đại lợi ích của C.
  - Nhãn thủ công do một người chấm là tác giả, biết trước chiến lược nào sinh ra chunk; coherence B 10/10 điểm tối đa có thể thiên lệch.
  - Thời gian ingest bị nhiễu bởi retry 429; chi phí trả phí chỉ là ước tính.
- **Nếu làm lại hoặc mở rộng, cải tiến gì?**
  - Trích xuất bảng Phụ lục thành bản ghi có cấu trúc.
  - Thêm câu test, câu không có filter nhưng cần phân biệt chương, và một tập test mới độc lập.
  - Ghi `article` dạng danh sách cho chunk vắt qua nhiều Điều.
  - Thử hybrid BM25 + dense (theo phần mở rộng tùy chọn của GUIDE).
  - Có người chấm thứ hai cho nhãn coherence/grounding.

## Tái lập
Dữ liệu, benchmark và kết quả nằm trong `starter/`. Các vị trí tương ứng với cấu trúc tối thiểu trong SUBMISSION:

| SUBMISSION | Trong repo này |
|---|---|
| `src/` | [starter/src/](../../starter/src/): `pipeline.py`, `pdf_pages.py`, `lab_chunking.py`, `gemini_embedder.py`, `pg_store.py`, `lab_eval.py` |
| `data/manifest.*` | [starter/data/manifest.json](../../starter/data/manifest.json); PDF gốc `starter/data/raw/luat143-2025.pdf`; text đã chuẩn hóa `starter/data/processed/luat143-2025.pages.jsonl` |
| `benchmark/` | [starter/data/benchmark.json](../../starter/data/benchmark.json) |
| `results/` | [starter/results/](../../starter/results/): ingest_report, traces, metrics, manual_labels, coherence_samples, evaluation_summary, pipeline.log |
| cấu hình | [starter/configs/strategies.json](../../starter/configs/strategies.json), [starter/configs/evaluation.json](../../starter/configs/evaluation.json) |
| experiment log | [artifacts/experiment_log.csv](../../artifacts/experiment_log.csv) |

Lệnh chạy (từ thư mục `starter/`, cần Docker và key Gemini trong `GEMINI_API_KEY`, đặt ở `.env` hoặc biến môi trường):

```bash
python -m venv .venv            # kích hoạt .venv theo starter/README.md
python -m pip install -r requirements.txt -r requirements-lab.txt
cp .env.example .env            # điền GEMINI_API_KEY
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py
python scripts/build_manifest.py      # tải PDF nếu thiếu, kiểm sha256, tạo manifest + text trang
python scripts/verify_benchmark.py    # kiểm chứng evidence/filter của 21 câu
python -m src.pipeline ingest         # chunk + embed + upsert A, B; chạy lại để kiểm tra không nhân đôi
python -m src.pipeline evaluate       # 21 câu × A/B/C -> traces, metrics, coherence_samples
python scripts/summarize_eval.py      # bảng tổng hợp từ metrics + manual_labels + ingest_report
python -m src.pipeline search -s C --chapter V "Những ngành, nghề nào bị cấm đầu tư?"
```

Kiểm tra nạp lại (lần chạy 2 trong [ingest_report.json](../../starter/results/ingest_report.json)): 0 chunk embed lại, 218/218 và 118/118 ID không trùng. Khi tài liệu đổi: ID chunk sinh từ nội dung và model, nên chunk mới được embed, chunk không đổi được giữ, chunk lỗi thời bị xóa (đã thử trên dữ liệu tạm rồi dọn).

## Sử dụng AI
Công cụ: Claude Code (Anthropic), dùng hỗ trợ viết code, chạy lệnh và soạn nháp benchmark, lý do nhãn và báo cáo. Cách kiểm tra:
- Script kiểm chứng evidence của benchmark, có thử cố ý làm sai để chắc script bắt được lỗi.
- Kiểm tra chạy lại cho ra manifest giống hệt từng byte.
- Đếm dòng trùng trong database sau khi nạp lại.
- Đọc trace top-3 và mẫu coherence, đối chiếu với PDF khi chấm nhãn.
