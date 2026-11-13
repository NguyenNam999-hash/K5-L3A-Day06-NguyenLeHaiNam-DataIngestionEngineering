# Báo cáo cá nhân — Day 6

Họ và tên: Nguyễn Lê Hải Nam · MSSV: 2A202603699 · Lớp: L3A

## Dữ liệu và pipeline

- **Chủ đề/corpus:** *Alice's Adventures in Wonderland* của Lewis Carroll, Project Gutenberg eBook #11
  (`https://www.gutenberg.org/files/11/11-0.txt`), public domain tại Hoa Kỳ, dùng theo Project
  Gutenberg License. Snapshot tải ngày 13/11/2026; manifest đầy đủ ở `data/manifest.json`
  (`source_id=alice-1865`, license, URL, accessed_at, hash, danh sách trang).
- **Đơn vị nội dung:** nguồn là plain text, không có phân trang PDF. Tôi chia tuần tự trong từng
  chương thành **39 đơn vị** liên tiếp, không chồng lấn, mỗi đơn vị 1.500–5.000 ký tự (mục tiêu
  ~3.800), cắt tại ranh giới đoạn. Tổng **142.777 ký tự**, 12 chương; bỏ bìa, mục lục, boilerplate
  Gutenberg và dấu phân cách. Quy tắc đếm nằm trong manifest; ID trang là vị trí 1-based sau
  chuẩn hóa.
- **Chuẩn hóa (bước Normalize):** `normalize_text()` trong `starter/src/ingestion.py` chạy lại
  mỗi lần load `pages.jsonl` và **idempotent** (chuẩn hóa hai lần không đổi text), nên bước load
  không thể làm lệch corpus. Thứ tự cố định: (1) Unicode **NFC**; (2) CRLF/CR → `\n`; (3) xóa
  soft hyphen `U+00AD` và BOM `U+FEFF`; (4) `U+00A0` → space; (5) xóa dòng `[Illustration]` và
  dòng phân cách `* * *`; (6) gộp cụm space/tab, bỏ space thừa quanh newline, gộp cụm ≥3 newline
  thành `\n\n`; (7) `strip`. Giữ nguyên ranh giới đoạn vì strategy B cắt theo paragraph — làm
  phẳng newline sẽ làm sai so sánh B–C. (8) Chuyển bảng về dạng markdown (nếu có).
- **Đo hiệu quả chuẩn hóa:** đếm trên phần thân sách của `data/alice_raw.txt` (144.483 ký tự,
  không tính boilerplate Gutenberg): **3** dòng `* * *`, **121** cụm space/tab, **69** dòng có
  space thừa ở đầu/cuối, **43** cụm newline ≥3; còn NFC, CRLF, soft hyphen, BOM, NBSP và
  `[Illustration]` đều **0** lần — bản #11 vốn đã sạch, các rule đó vẫn giữ cho nhánh PDF. Không
  có trang scan nên không dùng OCR. Sau chuẩn hóa không còn space đôi hay newline ≥3; hạn chế:
  **220** chỗ in nghiêng vẫn còn dấu `_..._` trong text.
- **Kiểm lỗi (fail-closed, không tự sửa):** trước khi ingest, `src/run.py` kiểm ≥30 đơn vị, page
  ID duy nhất, không đơn vị rỗng, mỗi đơn vị ≥1.500 ký tự (thực tế ngắn nhất **2.057**),
  `manifest.page_count` và tập page ID khớp `pages.jsonl`; benchmark phải có ≥20 câu, ID duy
  nhất, ≥15 dev/≥5 test, ≥3 câu filter và ≥1 ở test, mỗi `evidence_quote` là substring nguyên
  văn của đúng gold page, `gold_source_id` khớp `document_id`, mọi giá trị filter có thật trong
  metadata trang đó, và không câu hỏi nào chứa lại chính quote. `load_pages_jsonl` ném
  `ValueError` kèm `file:dòng` khi record thiếu field/sai kiểu và khi text rỗng; `normalize_text`
  ném `TypeError` nếu input không phải str. `benchmark/verify_benchmark.py` lặp lại các kiểm tra
  này độc lập, không gọi API. Mọi vi phạm đều dừng pipeline — không fallback, không sửa tay
  corpus; UI (bước 1) hiển thị đúng các cổng này kèm giá trị quan sát được.
- **Embedding:** `BAAI/bge-base-en-v1.5`, **768 chiều**, chạy local qua `fastembed` 0.9.1 /
  `onnxruntime` 1.31.1 (Python 3.11.15). `embed_documents` dùng cho chunk, `query_embed` cho câu
  hỏi; cùng model cho cả ba cấu hình. Không dùng mock/random vector; lỗi provider được raise,
  không fallback.
- **Vector store:** PostgreSQL 16 + **pgvector 0.8.7**, bảng `chunks`, metric **cosine**, tìm kiếm
  **exact** (không tạo ANN index vì corpus nhỏ). Khởi tạo bằng Docker Compose trong `starter/`
  (host `127.0.0.1:55433`), schema do `store.create_schema()` tạo.
- **Lệnh chạy:** từ `starter/`:
  `python -m pip install -r requirements.txt`
  → `docker compose -p day6_student_starter up -d --wait`
  → `python scripts/check_setup.py`
  → `python ../benchmark/verify_benchmark.py`
  → `python -m src.run --recreate`.
  Chi phí API: **0 USD** (model local). Lần chạy cuối 72,89 giây (70,06 giây embedding, 1,81 giây
  search). Token theo tokenizer của model: corpus 19.968; A 41.003; B/C 37.067.
- **Metadata:** mỗi chunk giữ `document_id`, `source`, `page`, `section`, `language`,
  `document_type`, `char_count`. `section` lấy trực tiếp từ heading chương trong nguồn, là trường
  filter duy nhất; lý do: corpus có 12 chương phân biệt, filter này giúp loại distractor từ chương
  khác mà không cần suy đoán. Filter chỉ áp dụng khi câu hỏi/ngữ cảnh cung cấp trước truy xuất;
  không đưa gold page/đáp án vào query hay filter.

## Cấu hình và benchmark

- **A:** `fixed_size` 1.600 ký tự, overlap 200, không filter.
- **B:** `by_paragraph`, tối đa 1.000 ký tự, không filter.
- **C:** giống B, thêm filter `section` khi câu hỏi có filter. **B–C chỉ khác filter**; cùng chunk,
  model, store và metric.
- **Bộ câu hỏi:** 20 câu trong `benchmark/benchmark.json`, **15 dev (`q01`–`q15`) / 5 test
  (`q16`–`q20`)**. Có 6 câu filter: dev `q04`, `q08`, `q12`; test `q16`, `q18`, `q19`.
  Câu hỏi gồm trực tiếp, diễn đạt lại và điều kiện/ngoại lệ.
- **Kiểm chứng evidence:** mỗi câu có `gold_source_id`, `gold_page`, `evidence_quote`; quote được
  kiểm tra là substring nguyên văn trong đúng trang bằng `benchmark/verify_benchmark.py` và lặp lại
  trong `src/run.py` trước khi ingest. Câu hỏi do LLM soạn từ một trang, tác giả đọc lại, loại câu
  mơ hồ; không dùng đáp án/gold trong query.
- **Chốt trước khi đo:** ưu tiên **dev Evidence Hit@3**, sau đó metadata/filter compliance và
  grounding, cuối cùng là số chunk/thời gian. Dev dùng để chọn chunk size và model; test chỉ dùng
  cho lần đo cuối. Lần chạy khám phá đầu tiên đã xem bộ test ban đầu, nên bộ đó được thay bằng 5 câu
  held-out mới trước lần đo cuối; bộ cũ lưu ở `benchmark/exploratory_benchmark.json`.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---:|---:|---:|---:|---|---:|
| A | dev | 8/15 (53,3%) | 12/15 (80,0%) | 116 | embed 37,46s; search 0,64s | 0 USD |
| A | test | 1/5 (20,0%) | 3/5 (60,0%) | 116 | — | 0 USD |
| B | dev | 9/15 (60,0%) | 12/15 (80,0%) | 180 | embed 32,60s; search 0,57s | 0 USD |
| B | test | 2/5 (40,0%) | 3/5 (60,0%) | 180 | — | 0 USD |
| C | dev | 9/15 (60,0%) | 13/15 (86,7%) | 180 | embed 0s (reuse B); search 0,61s | 0 USD |
| C | test | 2/5 (40,0%) | 3/5 (60,0%) | 180 | — | 0 USD |

| Strategy | Coherence /2 (n=10) | Metadata đủ/đúng (n=15) | Filter tuân thủ (n/N) | Grounding 0/1/2; TB /2 |
|---|---|---|---|---|
| A | 1,0 | 15/15 | 2/3 | 2/0/3; 1,2 |
| B | 2,0 | 15/15 | 2/3 | 2/0/3; 1,2 |
| C | 2,0 | 15/15 | 3/3 | 2/0/3; 1,2 |

Tổng metadata đã kiểm tra: **45/45 bản ghi** (5 câu test × top-3 × 3 strategy).

- Trace đầy đủ: `results/traces.json` (60 trace, top-3 kèm text/metadata/score/hit).
- Nhãn thủ công: `results/coherence_audit.csv` (10 vị trí × 3), `results/metadata_audit.csv`
  (5 câu test × top-3 × 3), `results/grounding_audit.csv` (5 câu test × 3). Phân tích:
  `results/analysis.md`; log: `results/final_run.log`, `results/reingest_check.log`.
- Cách chọn mẫu coherence: 10 vị trí tại 60% độ dài của 10 trang đầu (p01–p10), xác định trước khi
  chấm; mỗi vị trí lấy chunk chứa nó. Metadata/grounding dùng toàn bộ 5 câu test.

## Phân tích và kết luận

- **A–B:** cùng Hit@3 tổng (15/20) nhưng B tốt hơn Hit@1 (11 vs 9) và coherence (2,0 vs 1,0).
  A có 116 chunk nhưng 158.147 ký tự do overlap; cửa sổ cố định cắt giữa từ/câu. Ví dụ **q01**
  (gold p2): A trả về p24/p34/p3; B/C đưa p2 vào top-3. Chọn B làm nền.
- **B–C:** C tăng dev Hit@3 **12/15 → 13/15**, overall **15/20 → 16/20**, filter compliance
  **2/3 → 3/3**; test giữ 3/5. Ví dụ **q08** (gold p19, Chapter VI): B trả p19/p12/p8 nhưng evidence
  không vào top-3; C lọc Chapter VI và đưa evidence lên rank 2.
- **Hai lỗi tiêu biểu:** **q17** (test, gold p20): “play croquet with the Queen today … not happened
  yet?”; cả ba trả về p29/p25/p26 vì query khớp ngữ cảnh croquet/Queen, trong khi câu trả lời đúng
  “I haven’t been invited yet” rất ngắn. **q12** (dev, gold p28): C lọc đúng Chapter IX nhưng top-3
  là p28/p28/p30, chunk chứa “I won’t have any pepper in my kitchen at all” vẫn ngoài top-3 —
  filter cải thiện phạm vi, không bảo đảm evidence.
- **Chiến lược chọn:** **C** cho các câu có filter, **B** cho câu không filter (C chạy không filter
  khi câu không có filter). Đánh đổi: C phụ thuộc `section` đúng và có thể loại evidence hợp lệ nếu
  metadata sai; B nhiều chunk hơn A (~55%) và tốn embedding hơn, nhưng chunk mạch lạc hơn.
- **Hạn chế:** một tài liệu/ngôn ngữ, 20 câu, test 5 câu; nhãn thủ công do tác giả chấm nên có
  thiên lệch; Hit@3 yêu cầu evidence nằm trọn một chunk nên evidence bị chia bị tính miss; chưa đo
  độ ổn định nhiều lần chạy; kết quả chỉ đúng snapshot, không phải dữ liệu sống. Bộ test ban đầu đã
  được xem trong lần chạy khám phá và được thay bằng held-out mới; vì vậy không coi đây là holdout
  độc lập tuyệt đối.
- **Nếu làm lại:** thêm hybrid BM25/dense, rerank top-20 bằng cross-encoder, thử chunk theo
  scene/paragraph lớn hơn, thêm nhiều trường filter (`chapter`, `character`, `document_type`), và
  tăng test set lên 15–20 câu để giảm phương sai.

## Ghi công AI

Tôi dùng OpenCode (DeepSeek V4.1 Flash) để hỗ trợ viết code pipeline, soạn nháp câu hỏi benchmark,
và gợi ý phân tích. Mọi evidence quote được kiểm tra tự động là substring của trang nguồn; mọi số
liệu trong báo cáo lấy từ lần chạy thật trong `results/`; không có kết quả, log hay nhãn nào được
tạo giả. Tôi đã đọc lại code, chạy pipeline, kiểm tra top-3 và tự chấm các nhãn thủ công.
