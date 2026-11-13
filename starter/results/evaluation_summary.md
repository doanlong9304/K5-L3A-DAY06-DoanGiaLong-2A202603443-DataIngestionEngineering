# Kết quả đánh giá (top-3)

Chạy: 2026-11-13T18:45:20+07:00 · model `gemini-embedding-001` · trace: `results/traces.jsonl` (63 dòng = 21 câu × 3 cấu hình).

## 1. Retrieval accuracy (tự động)

| Cấu hình | Hit@1 dev | Evidence Hit@3 dev | Hit@1 test | Evidence Hit@3 test | Tuân thủ filter dev | Tuân thủ filter test |
|---|---|---|---|---|---|---|
| A | 9/16 | 12/16 | 4/5 | 5/5 | N/A | N/A |
| B | 11/16 | 15/16 | 4/5 | 5/5 | N/A | N/A |
| C | 12/16 | 16/16 | 5/5 | 5/5 | 3/3 | 1/1 |

Câu trượt Hit@3 và lỗi evidence bị tách qua nhiều chunk:

- **A**: trượt ['q05', 'q11', 'q15', 'q16']; evidence_split ['q05', 'q11']
- **B**: trượt ['q16']; evidence_split không
- **C**: trượt không; evidence_split không

Filter chỉ áp dụng ở C và chỉ cho câu có filter (q08, q09, q16, t01); câu khác C chạy không lọc. A/B không lọc nên tuân thủ filter là N/A.

## 2. Chunk coherence (thủ công, 10 vị trí, thang 0–2)

| Vị trí | Trang | A | B (= C) |
|---|---|---|---|
| pos01 | 1 | 2 | 2 |
| pos02 | 2 | 2 | 2 |
| pos03 | 3 | 1 | 2 |
| pos04 | 4 | 1 | 2 |
| pos05 | 5 | 0 | 2 |
| pos06 | 6 | 1 | 2 |
| pos07 | 7 | 2 | 2 |
| pos08 | 8 | 1 | 2 |
| pos09 | 9 | 1 | 2 |
| pos10 | 10 | 0 | 2 |
| **Trung bình /2** | | **1.10** | **2.00** |

Lý do từng nhãn: `results/manual_labels.json`.

## 3. Metadata usefulness (thủ công, 5 câu test × top-3)

| Cấu hình | Đủ và đúng / đã kiểm tra | Tuân thủ filter (câu test có filter) |
|---|---|---|
| A | 10/15 | N/A |
| B | 15/15 | N/A |
| C | 15/15 | 1/1 |

Lỗi:
- A t01 #1 A-33d015d56637a29f: article=40 nhưng chunk chứa cả đầu Điều 41; tiêu đề bị cắt còn '40. ...'.
- A t01 #3 A-63ae995e6fb3b8ef: article=6 nhưng chunk chứa cả đầu Điều 7.
- A t04 #3 A-746e5536c2333508: article=47 nhưng phần lớn chunk là Điều 48.
- A t05 #2 A-b3fba00804b6847d: chapter/article = null nhưng chunk chứa Chương I, Điều 1–3.
- A t05 #3 A-2535f2378f4f65b3: article=50 nhưng evidence nằm ở Điều 51 trong cùng chunk.

- Hữu ích: chapter giúp lọc phạm vi: ở t01 B (không lọc) xếp Điều 6 (Chương I) lên top-1, C lọc Chương V đưa Điều 40 lên top-1. page/page_end + article giúp truy về đúng khoản trong PDF.
- Hạn chế: chapter/article lấy theo vị trí đầu chunk: với chunk cố định (A) trải qua ranh giới Điều, metadata sai/thiếu; filter chapter cũng chỉ có ích khi người dùng biết trước phạm vi chương.

## 4. Grounding quality (thủ công, 5 câu test, top-3 gộp)

Đánh giá độ đủ của bằng chứng truy xuất, **không phải** độ chính xác câu trả lời LLM.

| Cấu hình | Mức 0 | Mức 1 | Mức 2 | Trung bình /2 | Evidence Hit@3 test (proxy) |
|---|---|---|---|---|---|
| A | 0 | 1 | 4 | 1.80 | 5/5 |
| B | 0 | 0 | 5 | 2.00 | 5/5 |
| C | 0 | 0 | 5 | 2.00 | 5/5 |

## 5. Tài nguyên (lần ingest đầy đủ)

| Cấu hình | Số chunk | Độ dài min/median/max | Tổng ký tự | Token | Thời gian ingest (s) | Chi phí |
|---|---|---|---|---|---|---|
| A | 218 | 72/497.0/499 | 100919 | 29930 | 80.96 | 0 USD free tier (~0.004489 USD nếu trả phí) |
| B | 118 | 133/888.0/1197 | 95407 | 28200 | 117.4 | 0 USD free tier (~0.00423 USD nếu trả phí) |
| C | 118 (dùng chung B) | 133/888.0/1197 | 95407 | 28200 | 117.4 | 0 USD free tier (~0.00423 USD nếu trả phí) |

Thời gian ingest gồm thời gian chờ retry do HTTP 429 của free tier (xem `results/pipeline.log`). Embed 21 câu hỏi: 1 lần gọi API, 1.35 s.

## 6. Data strategy impact

**A → B (đổi chunking, cùng model/store, không lọc).** B tăng Evidence Hit@3 dev từ 12/16 lên 15/16 và Hit@1 dev 9/16 → 11/16; test cả hai 5/5 Hit@3, 4/5 Hit@1. Coherence 1.10 → 2.00, metadata đủ và đúng 10/15 → 15/15, grounding 1.80 → 2.00. B dùng ít chunk hơn (118 so với 218) và ít token hơn (28.200 so với 29.930). Lỗi của A: evidence bị tách qua 2 chunk ở q05, q11 (không chunk nào chứa đủ); q15 có đúng trang 27 nhưng cửa sổ 500 ký tự cắt câu về bất khả kháng; t03 lấy được '24 tháng' nhưng mất điều kiện đi kèm; chunk A trải qua ranh giới Điều nên metadata article sai (vd. t05 ghi Điều 50 cho evidence của Điều 51). Đánh đổi: chunk B dài hơn (median 888 so với 497 ký tự), đưa nhiều chữ hơn vào ngữ cảnh mỗi kết quả.

**B → C (chỉ thêm filter chapter).** Khác biệt chỉ ở 4 câu có filter: q16 (dev) B trượt vì Điều 29 (Chương IV) và Điều 4–5 (Chương I) lấn át, C lọc Chương V đưa Điều 38 vào top-3 → Hit@3 dev 15/16 → 16/16; q09 Hit@1 tăng (B xếp Điều 7 Chương I lên đầu); t01 (test) B xếp Điều 6 lên top-1, C đưa Điều 40 lên top-1 → Hit@1 test 4/5 → 5/5. Tuân thủ filter của C 3/3 dev, 1/1 test. Chi phí bằng B (cùng chunk/vector, chỉ thêm điều kiện WHERE). Hạn chế: C phụ thuộc người dùng cung cấp đúng chương; với câu không có filter C = B.

**Chọn chiến lược theo tiêu chí định trước** (`configs/evaluation.json`): (1) Evidence Hit@3 test hòa 5/5 cả ba; (2) grounding test B = C = 2.00 > A = 1.80; (3) Hit@1 test C 5/5 > B 4/5 → **chọn C**, và B là lựa chọn khi không có thông tin phạm vi chương.

**Sai số do mẫu nhỏ:** test chỉ 5 câu, mỗi khác biệt test tương ứng 1 câu (20 điểm %); dev 16 câu, một văn bản duy nhất. Kết quả chỉ minh họa cho corpus Luật Đầu tư 143/2025/QH15, không suy rộng sang tài liệu khác. Câu filter được thiết kế có chủ đích là mơ hồ giữa các chương nên phóng đại lợi ích của C so với truy vấn thực tế.

Người chấm: Đoàn Gia Long (tác giả). Chấm bằng cách đọc text chunk trong trace/mẫu và đối chiếu với PDF đã chuẩn hóa; công cụ hỗ trợ khai báo ở mục Sử dụng AI của báo cáo. Người chấm là tác giả, biết chiến lược nào sinh ra chunk và đã thiết kế benchmark; nhãn có thể thiên về B. Các nhãn coherence của B gần như tuyệt đối nên cần người thứ hai kiểm tra nếu có thể.
