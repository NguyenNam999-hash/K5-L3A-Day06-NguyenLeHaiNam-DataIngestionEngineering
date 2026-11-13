# Benchmark — Alice in Wonderland

Bộ câu hỏi **20 câu** cho corpus `data/pages.jsonl` (39 đơn vị nội dung, 142.777 ký tự) từ
*Alice's Adventures in Wonderland* (Project Gutenberg eBook #11, public domain tại Hoa Kỳ).

## Files

| File | Vai trò |
|---|---|
| `benchmark.json` | Bộ chính thức dùng để chạy và chấm: 15 dev (`q01`–`q15`) + 5 test (`q16`–`q20`). |
| `exploratory_benchmark.json` | Bộ 20 câu ban đầu, dùng cho lần chạy khám phá đầu tiên. Không dùng cho kết quả cuối. |
| `candidates.json` | Ứng viên do LLM soạn cho 20 trang ban đầu, trước khi kiểm chứng. |
| `holdout_candidates.json` | Ứng viên test mới sinh từ các trang chưa dùng, sau khi phân tích lỗi dev. |
| `generate_questions.py` | Script soạn câu hỏi (chỉ là công cụ hỗ trợ; không chạy trong pipeline). |
| `verify_benchmark.py` | Kiểm tra split, filter và evidence khớp đúng trang nguồn. |

## Cách tạo và kiểm chứng

1. Mỗi câu được soạn từ **một trang nguồn** bởi LLM, theo loại `direct`, `paraphrase` hoặc
   `conditional`; sau đó tác giả đọc lại và chỉ giữ câu trả lời rõ ràng, không cần kiến thức ngoài.
2. `evidence_quote` phải là đoạn **trích nguyên văn** trong trang. Script kiểm tra bằng so khớp
   substring sau chuẩn hóa khoảng trắng; câu nào không khớp bị loại hoặc sinh lại.
3. `gold_source_id` + `gold_page` xác định đơn vị nguồn; `evidence_quote` xác định bằng chứng đủ.
4. Filter chỉ dùng trường `section` lấy từ heading chương thật trong nguồn. Câu có filter nêu rõ
   tên chương trong câu hỏi; không đưa đáp án hoặc gold page vào query/filter.
5. Split được chốt trước lần chạy cuối:
   - **dev**: 15 câu, dùng để chọn tham số chunking.
   - **test**: 5 câu từ các trang chưa dùng để chọn tham số/viết câu hỏi (`q16`–`q20`),
     cố định trước lần đo cuối.
6. Lần chạy đầu tiên đã xem bộ test ban đầu; vì vậy bộ test đó được thay bằng 5 câu held-out mới
   trước lần đo cuối. `exploratory_benchmark.json` lưu lại bộ cũ để minh bạch, không dùng để báo cáo.

## Filter

- Dev: `q04` (Chapter IV), `q08` (Chapter VI), `q12` (Chapter IX).
- Test: `q16` (Chapter III), `q18` (Chapter VIII), `q19` (Chapter XI).
- Các câu không có filter: cấu hình C chạy **không filter** cho câu đó.

## Kiểm tra

```bash
cd starter
.venv/bin/python ../benchmark/verify_benchmark.py
```

Lệnh trên kiểm tra 20 câu, tỷ lệ 15/5, tối thiểu 3 câu filter và mỗi evidence quote thực sự
nằm trong đúng trang gold. Pipeline cũng lặp lại kiểm tra này trước khi ingest.
