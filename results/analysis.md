# Phân tích kết quả — Day 06

Toàn bộ số liệu dưới đây lấy từ lần chạy cuối (`results/run_metadata.json`, `results/traces.json`).
Corpus: 39 đơn vị nội dung, 142.777 ký tự, 19.968 token theo tokenizer của embedding model.
Benchmark chính thức: 20 câu (15 dev / 5 test), 6 câu có filter `section`.

## 1. Cấu hình

| Thành phần | Giá trị |
|---|---|
| Embedding | `BAAI/bge-base-en-v1.5`, 768 chiều, fastembed 0.9.1 / onnxruntime 1.31.1, chạy local |
| Vector store | PostgreSQL 16 + pgvector 0.8.7, bảng `chunks`, metric cosine, exact scan (không ANN) |
| A | `fixed_size`, 1.600 ký tự, overlap 200, không filter |
| B | `by_paragraph`, tối đa 1.000 ký tự, không filter |
| C | Giống B, thêm filter `section` khi câu hỏi cung cấp (6/20 câu; 3 câu test) |
| Chi phí API | 0 USD (model local) |

## 2. Retrieval

| Strategy | Split | Hit@1 | Evidence Hit@3 | Chunks | Token | Embed (s) | Search (s) |
|---|---|---:|---:|---:|---:|---:|---:|
| A | dev | 8/15 (53.3%) | 12/15 (80.0%) | 116 | 41.003 | 37.46 | 0.64 |
| A | test | 1/5 (20.0%) | 3/5 (60.0%) | 116 | 41.003 | 37.46 | 0.64 |
| A | overall | 9/20 (45.0%) | 15/20 (75.0%) | 116 | 41.003 | 37.46 | 0.64 |
| B | dev | 9/15 (60.0%) | 12/15 (80.0%) | 180 | 37.067 | 32.60 | 0.57 |
| B | test | 2/5 (40.0%) | 3/5 (60.0%) | 180 | 37.067 | 32.60 | 0.57 |
| B | overall | 11/20 (55.0%) | 15/20 (75.0%) | 180 | 37.067 | 32.60 | 0.57 |
| C | dev | 9/15 (60.0%) | 13/15 (86.7%) | 180 | 37.067 | 0 (reuse B) | 0.61 |
| C | test | 2/5 (40.0%) | 3/5 (60.0%) | 180 | 37.067 | 0 (reuse B) | 0.61 |
| C | overall | 11/20 (55.0%) | 16/20 (80.0%) | 180 | 37.067 | 0 (reuse B) | 0.61 |

Định nghĩa hit: chunk thuộc đúng `document_id` + `page` gold **và** chứa trọn `evidence_quote`
sau chuẩn hóa khoảng trắng. Đây là tiêu chí chặt; evidence bị chia qua hai chunk bị tính miss.

## 3. Chấm thủ công

### 3.1 Chunk coherence

Chọn trước 10 vị trí tại 60% độ dài của 10 trang đầu (p01–p10), lấy chunk chứa vị trí đó cho
mỗi strategy. Thang 0–2; file đầy đủ: `results/coherence_audit.csv`.

| Strategy | Điểm trung bình /2 | Phân bố 0/1/2 | Nhận xét |
|---|---:|---|---|
| A | 1.0 | 0 / 10 / 0 | Mọi chunk bắt đầu giữa từ/câu do cửa sổ ký tự; đọc được nhưng mất ngữ cảnh mở đầu. |
| B | 2.0 | 0 / 0 / 10 | Bắt đầu ở ranh giới đoạn/câu, kết thúc trọn câu; giữ đủ ngữ cảnh. |
| C | 2.0 | 0 / 0 / 10 | C dùng đúng chunk của B; chỉ khác filter. |

### 3.2 Metadata usefulness

5 câu test × top-3 × 3 strategy = 45 bản ghi; file: `results/metadata_audit.csv`.

- `document_id`, `source`, `page`, `section` đủ và đúng: **45/45** bản ghi.
- Tuân thủ filter trên 3 câu test có filter: **C 3/3 câu**, **A 2/3**, **B 2/3**.
  A/B không áp filter; với q19, top-3 của A/B chứa chunk ngoài Chapter XI.
- `section` lấy trực tiếp từ heading chương trong nguồn, giúp giới hạn phạm vi và truy vết.
  Hạn chế: chỉ có một trường filter, và filter do người dùng/ngữ cảnh cung cấp trước truy xuất.

### 3.3 Grounding quality

5 câu test × 3 strategy = 15 nhãn; file: `results/grounding_audit.csv`.
Mỗi nhãn chấm cho cả top-3 của một câu, thang 0–2.

| Strategy | 0 | 1 | 2 | Trung bình /2 |
|---|---:|---:|---:|---:|
| A | 2 | 0 | 3 | 1.2 |
| B | 2 | 0 | 3 | 1.2 |
| C | 2 | 0 | 3 | 1.2 |

Hai câu thất bại hoàn toàn ở cả ba cấu hình: **q17** (gold page 20, top-3 trả về p25/p29)
và **q20** (gold page 37, top-3 trả về p2/p1/p1). Ba câu còn lại có đủ evidence trong top-3.
Không có trường hợp 1 vì khi chunk đúng trang gold xuất hiện, evidence quote cũng nằm trọn
trong ít nhất một chunk top-3.

## 4. Tác động của chiến lược dữ liệu

### A–B: chunking

- Cùng Hit@3 tổng (15/20), nhưng B tốt hơn Hit@1 (11 vs 9) và coherence (2.0 vs 1.0).
- A có 116 chunk, 158.147 ký tự (do overlap) so với B 180 chunk, 142.495 ký tự.
- A rẻ hơn về số chunk nhưng cắt giữa câu, làm evidence khó nằm trọn trong một chunk.
  Ví dụ **q01** (gold page 2): A trả về p24/p34/p3; B/C trả về p2 ở top-3.
- Chọn B làm nền vì chất lượng chunk và Hit@1 tốt hơn; chấp nhận nhiều chunk hơn.

### B–C: metadata filter

- C giữ nguyên chunk, model và metric của B; chỉ thêm filter `section` cho 6 câu có filter.
- Dev Hit@3 tăng **12/15 → 13/15**; overall **15/20 → 16/20**; test giữ nguyên 3/5.
- Tuân thủ filter tăng **2/3 → 3/3** câu test.
- Ví dụ **q08** (gold page 19, Chapter VI): B trả về p19/p12/p8 nhưng evidence không nằm trong
  top-3; C lọc Chapter VI và đưa chunk chứa evidence lên rank 2.
- Ví dụ giới hạn **q12** (gold page 28, Chapter IX): C lọc đúng Chapter IX và top-3 là p28/p28/p30,
  nhưng chunk chứa evidence vẫn xếp ngoài top-3. Filter cải thiện phạm vi, không bảo đảm evidence.
- Chọn **C** vì cải thiện dev Hit@3 và metadata compliance mà không tăng chunk/embedding; đánh đổi
  là phụ thuộc vào filter đúng và có thể loại bỏ evidence hợp lệ nếu `section` bị gán sai.

## 5. Ví dụ lỗi

1. **q17 — test, gold `alice-1865` p20**: “If Alice wants to play croquet with the Queen today,
   what does she say has not happened to her yet?”. Cả A/B/C trả về p29/p25/p26 — các chương nói
   nhiều về croquet và Queen. Câu trả lời đúng (“I haven’t been invited yet”) rất ngắn và ít trùng
   từ với query, nên embedding ưu tiên ngữ cảnh croquet chung thay vì câu cụ thể ở p20.
2. **q12 — dev, gold `alice-1865` p28**: “In Chapter IX ..., if Alice were a Duchess, what would she
   refuse to have in her kitchen at all?”. C lọc đúng Chapter IX nhưng top-3 là p28/p28/p30; chunk
   chứa “I won’t have any pepper in my kitchen at all” không vào top-3. Đây là lỗi ranking trong
   cùng section, không phải lỗi filter.

## 6. Tái lập và kiểm tra

- Chạy hai lần liên tiếp không `--recreate`: số chunk giữ nguyên **116 / 180 / 180**, chứng minh
  upsert không nhân đôi. Log: `results/reingest_check.log`.
- `python -m src.run --recreate` tạo lại schema khi đổi embedding dimension.
- `python ../benchmark/verify_benchmark.py` kiểm tra split, filter và evidence trước khi chạy.
- Chi phí 0 USD; thời gian chạy cuối 72,89 giây (70,06 giây embedding, 1,81 giây search).

## 7. Giới hạn

- Chỉ một tài liệu, một ngôn ngữ, 12 chương; không suy rộng ra ngoài corpus.
- 20 câu là mẫu nhỏ; test chỉ 5 câu nên một câu thay đổi làm lệch 20 điểm phần trăm.
- Câu hỏi do LLM soạn, tác giả đọc và kiểm chứng evidence; không dùng LLM judge. Nhãn thủ công do
  tác giả chấm nên có nguy cơ thiên lệch.
- Lần chạy khám phá đầu tiên đã xem bộ test ban đầu; bộ test đó được thay bằng 5 câu held-out mới
  trước lần đo cuối. `benchmark/exploratory_benchmark.json` lưu bộ cũ để minh bạch.
- Hit@3 yêu cầu evidence nằm trọn trong một chunk; evidence bị chia chunk bị tính miss, nên điểm
  thấp hơn cảm nhận thực tế ở một số câu.
- Chưa đo độ ổn định qua nhiều lần chạy; BGE model local có thể khác phiên bản nếu tải lại.
